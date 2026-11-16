"""스레드 수를 줄여도 결과가 같은가(정확도 쪽 확인) + CPU 로 검출할 때 스레드 1개의 속도.

  parity : GPU(또는 현재 장치) 검출 3슬롯을 같은 사진에 ① 기본 스레드 ② 기본 스레드(한 번 더 = 실행 간 흔들림 바닥) ③ torch 1개로 돌려
           박스 수·라벨·신뢰도·좌표를 비교한다. YuNet 얼굴 찾기도 OpenCV 스레드 기본 vs 2개로 비교한다.
  cpu    : VIGENT_DETECT_DEVICE=cpu 로 강제한 뒤 person 슬롯 1회 검출 시간을 torch 스레드 기본 vs 1개로 잰다
           (GPU 없는 기계에 '스레드 1개'를 기본으로 주면 어떻게 되는지).
사진은 읽기만 한다(복사·저장 없음). 사용: python thread_parity.py <저장소> parity|cpu [사진 수]
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MODE = sys.argv[2]
N_IMG = int(sys.argv[3]) if len(sys.argv) > 3 else 60
if MODE == "cpu":
    os.environ["VIGENT_DETECT_DEVICE"] = "cpu"
os.environ.setdefault("RF_HOME", str(ROOT / "vigent-core" / "weights"))
# 이 스크립트는 스레드 수를 직접 바꿔 가며 잰다 — 저장소의 자동 제한(thread_tune)이 끼어들지 않게 끈다
os.environ["VIGENT_TORCH_THREADS"] = "0"
os.environ["VIGENT_OPENCV_THREADS"] = "0"
sys.path.insert(0, str(ROOT / "vigent-core"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def images() -> list[Path]:
    fixed = sorted((ROOT / "tests" / "fixtures").glob("*.jpg")) + sorted((ROOT / "vigent-core" / "demo_assets").glob("*.jpg"))
    ev = sorted((ROOT / "data" / "evidence").rglob("*.jpg"))
    if len(ev) > N_IMG:
        step = len(ev) / float(N_IMG)
        ev = [ev[int(i * step)] for i in range(N_IMG)]
    return fixed + ev


def run_slot(model, imgs, conf):
    out = []
    for im in imgs:
        dets = model.detect(im, conf=conf)
        out.append(sorted((d["label"], round(float(d["conf"]), 6), tuple(round(float(v), 6) for v in d["bbox"])) for d in dets))
    return out


def diff(a, b):
    """두 결과 목록 비교 → 박스 수가 다른 사진 수, 신뢰도·좌표 최대 차."""
    n_mismatch, dconf, dbox, n_box = 0, 0.0, 0.0, 0
    for x, y in zip(a, b):
        if len(x) != len(y) or [r[0] for r in x] != [r[0] for r in y]:
            n_mismatch += 1
            continue
        for r, s in zip(x, y):
            n_box += 1
            dconf = max(dconf, abs(r[1] - s[1]))
            dbox = max(dbox, max(abs(p - q) for p, q in zip(r[2], s[2])))
    return {"images_with_different_boxes": n_mismatch, "boxes_compared": n_box,
            "max_conf_diff": round(dconf, 6), "max_bbox_diff": round(dbox, 6)}


def main():
    import app_state
    import torch
    guard = app_state.load_theme("safety")["agents"]["Guard"]
    default_nt = torch.get_num_threads()
    res: dict = {"mode": MODE, "torch_threads_default": default_nt, "cv2_threads_default": cv2.getNumThreads()}
    if MODE == "cpu":
        m = guard._get_model("person")
        res["device"] = str(getattr(m, "device", "?"))
        base = cv2.imread(str(ROOT / "tests" / "fixtures" / "person_far.jpg"))
        for (w, h) in ((640, 480), (1920, 1080)):
            f = cv2.resize(base, (w, h))
            row = {}
            for nt in (default_nt, 2, 1):
                torch.set_num_threads(nt)
                for _ in range(3):
                    m.detect(f, conf=0.4)
                ts = []
                c0 = time.process_time()
                for _ in range(12):
                    t = time.perf_counter(); m.detect(f, conf=0.4); ts.append((time.perf_counter() - t) * 1000)
                row[f"threads={nt}"] = {"wall_ms": round(statistics.median(ts), 1),
                                        "cpu_ms": round((time.process_time() - c0) * 1000 / 12, 1)}
            torch.set_num_threads(default_nt)
            res[f"{w}x{h}"] = row
        print("RESULT " + json.dumps(res, ensure_ascii=False))
        return

    paths = images()
    imgs = [im for im in (cv2.imread(str(p)) for p in paths) if im is not None]
    res["images"] = len(imgs)
    res["sizes"] = sorted({f"{im.shape[1]}x{im.shape[0]}" for im in imgs})
    for slot in ("person", "ppe", "fire_smoke"):
        m = guard._get_model(slot)
        if m is None:
            res[slot] = {"error": "모델 없음"}
            continue
        res.setdefault("device", str(getattr(m, "device", "?")))
        torch.set_num_threads(default_nt)
        a1 = run_slot(m, imgs, 0.05)
        a2 = run_slot(m, imgs, 0.05)
        torch.set_num_threads(1)
        b = run_slot(m, imgs, 0.05)
        torch.set_num_threads(default_nt)
        res[slot] = {"boxes_default": sum(len(x) for x in a1),
                     "default_vs_default(실행 간 흔들림)": diff(a1, a2),
                     "default_vs_torch1": diff(a1, b)}
        print(slot, json.dumps(res[slot], ensure_ascii=False), flush=True)

    # YuNet 얼굴 찾기: OpenCV 스레드 기본 vs 2 vs 1
    import privacy
    det = privacy._get_cascade()
    if det is not None:
        def faces(nt):
            cv2.setNumThreads(nt)
            out = []
            for im in imgs:
                h, w = im.shape[:2]
                det.setInputSize((w, h))
                _ok, fs = det.detect(im)
                out.append([] if fs is None else [tuple(round(float(v), 4) for v in f[:4]) + (round(float(f[14]), 6),) for f in fs])
            return out
        fa = faces(-1)
        res["yunet"] = {"faces_default": sum(len(x) for x in fa)}
        for nt in (2, 1):
            fb = faces(nt)
            same = sum(1 for x, y in zip(fa, fb) if x == y)
            mx = max((abs(p - q) for x, y in zip(fa, fb) if len(x) == len(y) for r, s in zip(x, y) for p, q in zip(r, s)), default=0.0)
            res["yunet"][f"default_vs_cv2_{nt}"] = {"identical_images": same, "of": len(imgs), "max_abs_diff": round(mx, 6)}
        cv2.setNumThreads(-1)
        # 색 변환(검출 전처리에서 쓰는 유일한 OpenCV 연산)도 스레드 수와 무관한지
        cv2.setNumThreads(2); c2 = [cv2.cvtColor(im, cv2.COLOR_BGR2RGB) for im in imgs]
        cv2.setNumThreads(-1); cd = [cv2.cvtColor(im, cv2.COLOR_BGR2RGB) for im in imgs]
        res["cvtColor_identical"] = all(np.array_equal(x, y) for x, y in zip(c2, cd))
    print("RESULT " + json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
