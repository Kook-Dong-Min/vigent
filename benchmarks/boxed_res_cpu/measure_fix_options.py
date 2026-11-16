"""개선안 비용 측정(읽기 전용). 1차(measure_res_cpu.py)와 같은 방식: wall=호출 중앙값(ms), cpu=프로세스 CPU/호출(ms).

A. YuNet 입력 크기별 비용(기본 스레드)
B. YuNet 스레드 수별 비용(cv2.setNumThreads)
C. 박스 영상 1장 변형안(원본 1920x1080 · 2304x1296)
D. 검출 1회: torch 스레드 수별 + '초당 8회로 띄엄띄엄 부를 때' 실제 점유 코어 수
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT / "vigent-core"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402


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


def paced(fn, hz: float, dur_s: float = 4.0):
    """hz 로 띄엄띄엄 부를 때 점유 코어 수 = CPU초 / 벽시계초 (호출 사이 대기 포함)."""
    for _ in range(3):
        fn()
    c0 = time.process_time()
    t0 = time.perf_counter()
    n = 0
    nxt = t0
    while time.perf_counter() - t0 < dur_s:
        fn()
        n += 1
        nxt += 1.0 / hz
        slp = nxt - time.perf_counter()
        if slp > 0:
            time.sleep(slp)
        else:
            nxt = time.perf_counter()
    wall = time.perf_counter() - t0
    return {"hz_target": hz, "hz_actual": round(n / wall, 1), "cores": round((time.process_time() - c0) / wall, 2)}


def make_frame(base, w, h):
    f = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    rng = np.random.default_rng(0)
    return np.clip(f.astype(np.int16) + rng.normal(0, 4, f.shape).astype(np.int16), 0, 255).astype(np.uint8)


def main():
    res: dict = {}
    base = cv2.imread(str(ROOT / "tests" / "fixtures" / "person_far.jpg"))
    import boxed_view
    import privacy
    det = privacy._get_cascade()
    person = [0.40, 0.20, 0.60, 0.90]
    dets = [{"class": "person", "score": 0.9, "bbox": person},
            {"class": "Hardhat", "score": 0.8, "bbox": [0.45, 0.20, 0.55, 0.30]},
            {"class": "NO-Safety-Vest", "score": 0.7, "bbox": [0.42, 0.35, 0.58, 0.60]}]

    def yunet(img):
        h, w = img.shape[:2]
        with privacy._lock:
            det.setInputSize((w, h))
            return det.detect(img)

    # ── A. YuNet 입력 크기별 ──
    res["A_yunet_by_size"] = {}
    for (w, h) in [(320, 180), (480, 270), (640, 360), (960, 540), (1280, 720), (1920, 1080)]:
        f = make_frame(base, w, h)
        res["A_yunet_by_size"][f"{w}x{h}"] = bench(lambda: yunet(f))
    print("A", json.dumps(res["A_yunet_by_size"]), flush=True)

    # ── B. YuNet 스레드 수별(640x360 · 1920x1080) ──
    res["B_yunet_threads"] = {}
    for (w, h) in [(640, 360), (1920, 1080)]:
        f = make_frame(base, w, h)
        row = {}
        for nt in (1, 2, 4):
            cv2.setNumThreads(nt)
            row[f"threads={nt}"] = bench(lambda: yunet(f))
        cv2.setNumThreads(-1)
        row["threads=default"] = bench(lambda: yunet(f))
        res["B_yunet_threads"][f"{w}x{h}"] = row
    print("B", json.dumps(res["B_yunet_threads"]), flush=True)
    res["B_yunet_paced30_640x360"] = {}
    f360 = make_frame(base, 640, 360)
    for nt in (-1, 2, 1):
        cv2.setNumThreads(nt)
        res["B_yunet_paced30_640x360"][f"threads={'default' if nt < 0 else nt}"] = paced(lambda: yunet(f360), 30.0)
    cv2.setNumThreads(-1)
    print("B2", json.dumps(res["B_yunet_paced30_640x360"]), flush=True)

    # ── C. 박스 영상 1장 변형안 ──
    def head_mosaic(img):
        h, w = img.shape[:2]
        x1, y1, x2, y2 = person[0] * w, person[1] * h, person[2] * w, person[3] * h
        bw, bh = x2 - x1, y2 - y1
        privacy._mosaic_region(img, int(x1 + bw * 0.1), int(y1), int(x2 - bw * 0.1), int(y1 + bh * 0.30))

    def draw_encode(img):
        h, w = img.shape[:2]
        thick = max(1, round(min(w, h) / 320))
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            cv2.rectangle(img, (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h)), (255, 208, 57), thick)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf

    def shrink(img, maxw):
        h, w = img.shape[:2]
        if w <= maxw:
            return img.copy()
        return cv2.resize(img, (maxw, round(h * maxw / w)), interpolation=cv2.INTER_AREA)

    def variant(f, out_w, face_w):
        """out_w: 내보낼 영상 최대 가로(None=원본). face_w: 얼굴 찾기에 쓸 최대 가로(None=내보낼 크기 그대로, 0=얼굴 찾기 생략)."""
        img = shrink(f, out_w) if out_w else f.copy()
        head_mosaic(img)
        if face_w != 0:
            src = shrink(img, face_w) if face_w else img
            _ok, faces = yunet(src)
            sc = img.shape[1] / src.shape[1]
            for fc in (faces if faces is not None else []):
                fx, fy, fw, fh = (int(v * sc) for v in fc[:4])
                privacy._mosaic_region(img, fx, fy, fx + fw, fy + fh)
        return draw_encode(img)

    res["C_boxed_variants"] = {}
    ts = [0.0]
    for (w, h) in [(1920, 1080), (2304, 1296)]:
        f = make_frame(base, w, h)
        row = {}

        def _cur():
            ts[0] += 1.0
            boxed_view.jpeg_for("bench", (f, dets, ts[0]))
        row["V0 현재(원본 크기·원본 크기로 얼굴 찾기)"] = bench(_cur)
        for name, ow, fw in [("V1 내보내기 1280·얼굴 1280", 1280, None),
                             ("V2 내보내기 960·얼굴 960", 960, None),
                             ("V3 내보내기 원본·얼굴 640", None, 640),
                             ("V4 내보내기 1280·얼굴 640", 1280, 640),
                             ("V5 내보내기 1280·얼굴 480", 1280, 480),
                             ("V6 내보내기 960·얼굴 480", 960, 480),
                             ("V7 내보내기 1280·얼굴 찾기 생략", 1280, 0),
                             ("V8 내보내기 원본·얼굴 찾기 생략", None, 0)]:
            r = bench(lambda: variant(f, ow, fw))
            r["kb"] = round(len(variant(f, ow, fw)) / 1024, 1)
            row[name] = r
        res["C_boxed_variants"][f"{w}x{h}"] = row
        print("C", f"{w}x{h}", json.dumps(row, ensure_ascii=False), flush=True)

    # ── D. 검출: torch 스레드 수 ──
    import app_state
    import torch
    guard = app_state.load_theme("safety")["agents"]["Guard"]
    model = guard._get_model("person")
    conf = float(guard.DETECTOR_CONF.get("person", 0.4))
    res["D_detect"] = {"torch_threads_default": torch.get_num_threads()}
    frames = {k: make_frame(base, *k) for k in [(640, 480), (1920, 1080), (2304, 1296)]}
    default_nt = torch.get_num_threads()
    for nt in (default_nt, 2, 1):
        torch.set_num_threads(nt)
        row = {}
        for k, f in frames.items():
            row[f"{k[0]}x{k[1]}"] = bench(lambda: model.detect(f, conf=conf), min_s=2.0, warm=4)
        row["paced8hz_1920x1080"] = paced(lambda: model.detect(frames[(1920, 1080)], conf=conf), 8.0)
        row["paced8hz_640x480"] = paced(lambda: model.detect(frames[(640, 480)], conf=conf), 8.0)
        res["D_detect"][f"torch_threads={nt}"] = row
        print("D", nt, json.dumps(row), flush=True)
    torch.set_num_threads(default_nt)
    # 줄여서 넣기(정확도 영향 미측정 — 비용만): 1920x1080 → 가로 960 으로 줄인 뒤 detect
    f1080 = frames[(1920, 1080)]
    res["D_detect"]["preshrink960_from_1920x1080"] = bench(
        lambda: model.detect(cv2.resize(f1080, (960, 540), interpolation=cv2.INTER_AREA), conf=conf), min_s=2.0, warm=4)
    print("D2", json.dumps(res["D_detect"]["preshrink960_from_1920x1080"]), flush=True)

    (OUT / "result_fix.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", (OUT / "result_fix.json").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
