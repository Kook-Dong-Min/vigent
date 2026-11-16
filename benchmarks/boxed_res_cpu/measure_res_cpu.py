"""해상도별 프레임당 CPU 비용 측정 (읽기 전용 — 저장소 파일을 바꾸지 않는다).

재는 것: 저장소의 실제 함수를 해상도별 프레임으로 직접 호출.
  copy      : frame.copy()
  anonymize : privacy.anonymize_faces (사본 + 머리 모자이크 + YuNet 얼굴검출)
  yunet     : YuNet 얼굴검출만(원본 크기 입력)
  jpeg      : cv2.imencode q80
  boxed     : boxed_view.jpeg_for (anonymize + 박스 그리기 + JPEG) = 박스 영상 1장
  boxed960  : 가로 960 으로 줄인 뒤 같은 처리(개선안 비용)
  detect    : RfdetrDetector.detect (person 슬롯, 실제 어댑터·GPU)
  decode    : H.264 파일 grab / retrieve (cv2 FFmpeg)
wall = 한 번 호출의 벽시계 중앙값(ms), cpu = 프로세스 전체 스레드의 CPU 시간/호출(ms).
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT / "vigent-core"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

SIZES = [(640, 480), (1280, 720), (1920, 1080), (2304, 1296), (2560, 1440), (3840, 2160)]
BITRATE = {(640, 480): "1M", (1280, 720): "2M", (1920, 1080): "4M", (2304, 1296): "4M",
           (2560, 1440): "6M", (3840, 2160): "8M"}


def bench(fn, min_s: float = 1.5, min_n: int = 12, warm: int = 2):
    for _ in range(warm):
        fn()
    walls = []
    c0 = time.process_time()
    t_end = time.perf_counter() + min_s
    n = 0
    while n < min_n or time.perf_counter() < t_end:
        t = time.perf_counter()
        fn()
        walls.append((time.perf_counter() - t) * 1000.0)
        n += 1
    cpu = (time.process_time() - c0) * 1000.0 / n
    return {"wall_ms": round(statistics.median(walls), 2), "cpu_ms": round(cpu, 2), "n": n}


def make_frame(base, w, h):
    f = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 4, f.shape).astype(np.int16)          # 센서 잡음 흉내(업스케일 영상이 지나치게 매끈해지는 것 방지)
    return np.clip(f.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def main():
    res: dict = {"env": {}, "stages": {}, "decode": {}}
    import torch
    res["env"] = {"cv2": cv2.__version__, "torch": torch.__version__, "cuda": torch.cuda.is_available(),
                  "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                  "cv2_threads": cv2.getNumThreads()}
    base = cv2.imread(str(ROOT / "tests" / "fixtures" / "person_far.jpg"))
    assert base is not None, "기준 사진 없음"
    res["env"]["base_image"] = list(base.shape[:2])

    import boxed_view
    import privacy
    res["env"]["privacy_enabled"] = privacy.enabled()
    res["env"]["yunet_loaded"] = privacy._get_cascade() is not None
    person = [0.40, 0.20, 0.60, 0.90]
    dets = [{"class": "person", "score": 0.9, "bbox": person},
            {"class": "Hardhat", "score": 0.8, "bbox": [0.45, 0.20, 0.55, 0.30]},
            {"class": "NO-Safety-Vest", "score": 0.7, "bbox": [0.42, 0.35, 0.58, 0.60]}]

    # 검출 어댑터(실제 서버 경로와 같은 방식으로 로드)
    import app_state
    guard = app_state.load_theme("safety")["agents"]["Guard"]
    model = guard._get_model("person")
    res["env"]["detect_model"] = None if model is None else {
        "device": str(getattr(model, "device", "?")), "resolution": getattr(model, "resolution", None),
        "optimized": getattr(model, "optimized", None)}
    conf = float(guard.DETECTOR_CONF.get("person", 0.4))

    for (w, h) in SIZES:
        key = f"{w}x{h}"
        f = make_frame(base, w, h)
        st: dict = {}
        st["copy"] = bench(lambda: f.copy())
        st["anonymize"] = bench(lambda: privacy.anonymize_faces(f, [person]))
        det = privacy._get_cascade()
        if det is not None:
            def _yunet():
                with privacy._lock:
                    det.setInputSize((w, h))
                    det.detect(f)
            st["yunet"] = bench(_yunet)
        drawn = boxed_view.render(f, dets)
        ok, buf = cv2.imencode(".jpg", drawn, [cv2.IMWRITE_JPEG_QUALITY, 80])
        st["jpeg"] = bench(lambda: cv2.imencode(".jpg", drawn, [cv2.IMWRITE_JPEG_QUALITY, 80]))
        st["jpeg"]["kb"] = round(len(buf) / 1024, 1)
        ts = [0.0]

        def _boxed():
            ts[0] += 1.0
            boxed_view.jpeg_for("bench", (f, dets, ts[0]))
        st["boxed"] = bench(_boxed)

        def _boxed960():
            ts[0] += 1.0
            if w > 960:
                small = cv2.resize(f, (960, round(h * 960 / w)), interpolation=cv2.INTER_AREA)
            else:
                small = f
            boxed_view.jpeg_for("bench", (small, dets, ts[0]))
        st["boxed960"] = bench(_boxed960)
        if model is not None:
            st["detect"] = bench(lambda: model.detect(f, conf=conf), min_s=2.0, warm=4)
        res["stages"][key] = st
        print(key, json.dumps(st), flush=True)

    # ── 디코드: 해상도별 H.264 시험 영상 생성 → grab / grab+retrieve ──
    ff = ROOT / "bin" / "ffmpeg.exe"
    for (w, h) in SIZES:
        key = f"{w}x{h}"
        vid = OUT / f"t_{w}x{h}.mp4"
        if not vid.exists():
            cmd = [str(ff), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30",
                   "-vf", "noise=alls=6:allf=t", "-t", "5", "-c:v", "libx264", "-preset", "veryfast",
                   "-b:v", BITRATE[(w, h)], "-g", "30", "-pix_fmt", "yuv420p", str(vid)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 or not vid.exists():
                res["decode"][key] = {"error": (r.stderr or "")[-300:]}
                print(key, "ffmpeg 실패", res["decode"][key], flush=True)
                continue
        d: dict = {"file_kb": round(vid.stat().st_size / 1024)}

        def run(params, retrieve: bool):
            cap = cv2.VideoCapture(str(vid), cv2.CAP_FFMPEG, params) if params else cv2.VideoCapture(str(vid))
            if not cap.isOpened():
                return None
            hw = cap.get(cv2.CAP_PROP_HW_ACCELERATION)
            n = 0
            c0 = time.process_time()
            t0 = time.perf_counter()
            while True:
                if not cap.grab():
                    break
                if retrieve:
                    ok, _fr = cap.retrieve()
                    if not ok:
                        break
                n += 1
            wall = (time.perf_counter() - t0) * 1000.0
            cpu = (time.process_time() - c0) * 1000.0
            cap.release()
            if n == 0:
                return None
            return {"frames": n, "wall_ms": round(wall / n, 2), "cpu_ms": round(cpu / n, 2), "hw": hw}
        # 같은 파일을 3회 반복해 CPU 시계 해상도(15.6ms) 영향 축소 — 중앙값
        def rep(params, retrieve):
            outs = [run(params, retrieve) for _ in range(3)]
            outs = [o for o in outs if o]
            if not outs:
                return None
            return {"frames": outs[0]["frames"], "hw": outs[0]["hw"],
                    "wall_ms": statistics.median(o["wall_ms"] for o in outs),
                    "cpu_ms": statistics.median(o["cpu_ms"] for o in outs)}
        d["grab_only"] = rep(None, False)
        d["grab_retrieve"] = rep(None, True)
        try:
            d["hw_any_grab_retrieve"] = rep([cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY], True)
        except Exception as ex:  # noqa: BLE001
            d["hw_any_grab_retrieve"] = {"error": f"{type(ex).__name__}: {ex}"}
        res["decode"][key] = d
        print(key, "decode", json.dumps(d), flush=True)

    (OUT / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ENV", json.dumps(res["env"], ensure_ascii=False))
    print("saved", OUT / "result.json", (OUT / "result.json").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
