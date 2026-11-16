"""박스 영상 1장(boxed_view.jpeg_for — 저장소 실제 코드) 전·후 비교. 같은 프로세스에서 설정만 바꿔 번갈아 잰다.

  전(before) = VIGENT_BOXED_MAX_SIDE=0, VIGENT_BOXED_FACE_SIDE=0  → 원본 크기로 얼굴 찾기·내보내기(2026-11-16 수정 전 동작)
  후(after)  = 환경변수 없음 → config/tuning.yaml 값(hub.boxed_max_side / hub.boxed_face_side)
wall = 호출 1회 벽시계 중앙값(ms) · cpu = 프로세스 전체 스레드 CPU 시간/호출(ms) · fps_max = 1000/wall(계산값).

사용: python measure_boxed_ab.py <저장소> <결과폴더>
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT / "vigent-core"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

SIZES = [(640, 480), (1280, 720), (1920, 1080), (2304, 1296), (2560, 1440), (3840, 2160), (1080, 1920)]
ENV = ("VIGENT_BOXED_MAX_SIDE", "VIGENT_BOXED_FACE_SIDE")


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
    w = statistics.median(walls)
    return {"wall_ms": round(w, 2), "cpu_ms": round(cpu, 2), "fps_max": round(1000.0 / w, 1), "n": n}


def main():
    import boxed_view
    import privacy
    base = cv2.imread(str(ROOT / "tests" / "fixtures" / "person_far.jpg"))
    dets = [{"class": "person", "score": 0.9, "bbox": [0.40, 0.20, 0.60, 0.90]},
            {"class": "Hardhat", "score": 0.8, "bbox": [0.45, 0.20, 0.55, 0.30]},
            {"class": "NO-Safety-Vest", "score": 0.7, "bbox": [0.42, 0.35, 0.58, 0.60]}]
    for k in ENV:
        os.environ.pop(k, None)
    res: dict = {"env": {"cv2": cv2.__version__, "cv2_threads": cv2.getNumThreads(), "privacy": privacy.enabled(),
                         "yunet": privacy._get_cascade() is not None,
                         "after_max_side": boxed_view.max_side(), "after_face_side": boxed_view.face_side()},
                 "rows": {}}
    rng = np.random.default_rng(0)
    ts = [0.0]
    for (w, h) in SIZES:
        f = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
        f = np.clip(f.astype(np.int16) + rng.normal(0, 4, f.shape).astype(np.int16), 0, 255).astype(np.uint8)
        row: dict = {}

        def _one():
            ts[0] += 1.0
            return boxed_view.jpeg_for("ab", (f, dets, ts[0]))
        for label, env in (("before", {"VIGENT_BOXED_MAX_SIDE": "0", "VIGENT_BOXED_FACE_SIDE": "0"}), ("after", {})):
            for k in ENV:
                os.environ.pop(k, None)
            os.environ.update(env)
            r = bench(_one)
            jpg = _one()
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            r["out"] = f"{img.shape[1]}x{img.shape[0]}"
            r["kb"] = round(len(jpg) / 1024, 1)
            row[label] = r
        for k in ENV:
            os.environ.pop(k, None)
        res["rows"][f"{w}x{h}"] = row
        print(f"{w}x{h}", json.dumps(row), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result_boxed_ab.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ENV", json.dumps(res["env"]))


if __name__ == "__main__":
    main()
