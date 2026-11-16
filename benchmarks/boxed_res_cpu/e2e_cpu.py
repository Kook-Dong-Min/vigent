"""서버와 같은 구성(실제 Worker + 캡처 스레드 + 박스 영상 생성기)으로 프로세스 전체 CPU 점유를 잰다.

· 카메라: 시험 영상(H.264 파일)을 **30장/초 라이브 카메라처럼** 흘려 넣는다(worker._open_capture 만 대역으로 교체).
  사용자 웹캠은 켜지 않는다. 캡처 스레드·검출 루프·추적·박스 영상은 저장소 코드 그대로.
· 기록 차단: data_engine.log_event · alert_notify.submit 은 이 프로세스에서만 빈 함수로 바꾼다(이벤트·증거·알림을 남기지 않는다).
· 시청자: 박스 영상(boxed_view.frames)을 받는 사람 1명(viewer=1) 또는 없음(viewer=0 — 허브를 닫은 상태).
· 결과: cores = (프로세스 CPU 초) / (벽시계 초). 논리 코어 12개 PC 에서 6.0 = 작업 관리자 50%.

사용: python e2e_cpu.py <저장소> <영상파일> <label> <viewer 0|1> [torch_threads cv2_threads]
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
VIDEO = str(Path(sys.argv[2]).resolve())
LABEL = sys.argv[3]
VIEWER = sys.argv[4] == "1"
THREADS = (int(sys.argv[5]), int(sys.argv[6])) if len(sys.argv) > 6 else None
WARM_S, MEASURE_S = 8.0, 20.0

# run.ps1 과 같은 환경
os.environ.setdefault("VIGENT_CAPTURE_MODE", "thread")
os.environ.setdefault("RF_HOME", str(ROOT / "vigent-core" / "weights"))
os.environ.setdefault("TORCH_HOME", str(ROOT / "vigent-core" / "weights" / "rtm_cache"))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")              # main.py 와 같이 — 검출 슬롯 구성(.env)을 실제 서버와 맞춘다
except ImportError:
    pass
sys.path.insert(0, str(ROOT / "vigent-core"))

import cv2  # noqa: E402


class PacedFileCap:
    """파일을 fps 로 흘려보내는 '라이브 카메라' 대역. 소비가 늦으면 밀린 프레임이 곧바로 나온다(실제 스트림 큐처럼)."""

    def __init__(self, path: str, fps: float = 30.0):
        self.cap = cv2.VideoCapture(path)
        self.dt = 1.0 / fps
        self.next = time.perf_counter()
        self.pts = 0.0

    def isOpened(self):
        return self.cap.isOpened()

    def set(self, *_a):
        return True

    def get(self, prop):
        return self.pts if prop == cv2.CAP_PROP_POS_MSEC else self.cap.get(prop)

    def grab(self):
        now = time.perf_counter()
        if now < self.next:
            time.sleep(self.next - now)
        elif now - self.next > 2.0:                 # 밀림 상한 2초(큐 길이 대역)
            self.next = now - 2.0
        self.next += self.dt
        ok = self.cap.grab()
        if not ok:                                   # 파일 끝 → 처음으로
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok = self.cap.grab()
        self.pts += self.dt * 1000.0
        return ok

    def retrieve(self):
        return self.cap.retrieve()

    def read(self):
        return self.cap.retrieve() if self.grab() else (False, None)

    def release(self):
        self.cap.release()


def main():
    import alert_notify
    import app_state
    import boxed_view
    import data_engine
    import worker

    blocked = {"events": 0, "alerts": 0}

    def _no_log(**_kw):
        blocked["events"] += 1
        return {}

    def _no_submit(**_kw):
        blocked["alerts"] += 1
        return {"queued": False, "reason": "bench"}
    data_engine.log_event = _no_log
    alert_notify.submit = _no_submit
    worker._open_capture = lambda _src: PacedFileCap(VIDEO)

    import torch
    if THREADS:
        torch.set_num_threads(THREADS[0])
        cv2.setNumThreads(THREADS[1])

    guard = app_state.load_theme("safety")["agents"]["Guard"]
    dets = worker.active_detectors()
    probe = cv2.VideoCapture(VIDEO)
    ok, f0 = probe.read()
    probe.release()
    assert ok, "시험 영상 못 읽음"
    for _ in range(3):                                # 예열(모델 로드·컴파일) — 측정 구간 밖
        guard.detect(f0, detectors=dets, track_key="bench-warm")
    guard.reset_tracks("bench-warm")

    w = worker.Worker()
    r = w.start(guard, app_state.DETECT_LOCK, "rtsp://bench.invalid/stream", name="bench", fps=30.0)
    assert r.get("ok"), r

    view = {"frames": 0, "bytes": 0, "w": 0, "h": 0}
    stop = threading.Event()

    def _viewer():
        import numpy as np
        while not stop.is_set():
            for part in boxed_view.frames("bench", lambda: boxed_view.latest_view(w), closing=stop.is_set):
                view["frames"] += 1
                view["bytes"] += len(part)
                if view["w"] == 0:
                    s = part.index(b"\xff\xd8")
                    img = cv2.imdecode(np.frombuffer(part[s:-2], np.uint8), cv2.IMREAD_COLOR)
                    view["h"], view["w"] = img.shape[:2]
            time.sleep(0.05)
    if VIEWER:
        threading.Thread(target=_viewer, daemon=True).start()

    time.sleep(WARM_S)
    c0, t0 = time.process_time(), time.perf_counter()
    f_start, v_start, b_start = w.state.get("frames", 0), view["frames"], view["bytes"]
    infer, lag, det_ts = [], [], set()
    while time.perf_counter() - t0 < MEASURE_S:
        time.sleep(0.05)
        if w.state.get("infer_ms") is not None:
            infer.append(float(w.state["infer_ms"]))
        det_ts.add(w._last_det_ts)
        sc = w._streamcap
        if sc is not None and sc.lag_ms is not None:
            lag.append(float(sc.lag_ms))
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    vf = view["frames"] - v_start
    out = {
        "label": LABEL, "viewer": VIEWER, "src": f"{f0.shape[1]}x{f0.shape[0]}",
        "threads": THREADS, "detectors": dets,
        "cores": round(cpu / wall, 2), "cpu_pct_of_12": round(cpu / wall / 12 * 100, 1),
        "boxed_fps": round(vf / wall, 1), "boxed_kb": round((view["bytes"] - b_start) / max(1, vf) / 1024, 1) if vf else None,
        "boxed_size": f"{view['w']}x{view['h']}" if view["w"] else None,
        "loop_hz": round((w.state.get("frames", 0) - f_start) / wall, 1),
        "det_update_hz": round(len(det_ts) / wall, 1),
        "infer_ms_p50": round(statistics.median(infer), 1) if infer else None,
        "capture_lag_ms_p50": round(statistics.median(lag), 1) if lag else None,
        "capture_lag_ms_max": round(max(lag), 1) if lag else None,
        "worker_error": w.state.get("error") or "", "restarts": w.state.get("restarts"),
        "blocked_events": blocked["events"], "blocked_alerts": blocked["alerts"],
    }
    print("RESULT " + json.dumps(out, ensure_ascii=False), flush=True)
    stop.set()
    os._exit(0)          # 데몬 스레드·모델 정리 대기 없이 끝낸다(측정은 이미 끝났다)


if __name__ == "__main__":
    main()
