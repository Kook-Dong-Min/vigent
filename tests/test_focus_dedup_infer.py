"""tests/test_focus_dedup_infer.py — 풀세트 프레임의 표시용(:pf) 중복 추론 제거 (2026-10-12).

실측(GTX 1650 Ti, 확대뷰): 풀세트 프레임(2 Hz)이 표시용 :pf 추론(person[+ppe] ≈ 60~125 ms) + 판정용 풀세트 추론(≈ 180 ms)을
**같은 프레임에서 두 번** 돌려 1회 250~300 ms 였다. 풀세트의 추적 전 원시 검출을 :pf 추적기에 통과만 시키면(추론 없음) 그 비용이
사라진다. 판정 경로(detect 반환·신호·히스테리시스)는 불변.
고정하는 것:
  ① Guard.detect 가 키별 추적 전 원시 검출을 보관하고 last_pre_track 이 사본을 준다(원본 변형 무영향)
  ② Guard.track_only 는 추론 없이 추적만 — tid 를 붙이고 키별 추적 상태를 쓴다
  ③ 워커 _process_frame: 풀세트 차례(do_full)엔 :pf detect 를 부르지 않고 last_pre_track → track_only 로 표시 박스를 만든다(소스 잠금)
  ④ 키 청소가 _pre_track_by_key 도 비운다
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vigent-core")); sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine.guard as G  # noqa: E402


def _guard(algo="iou") -> G.Guard:
    g = G.Guard.__new__(G.Guard)
    g._tracks_by_key = {}; g._bytetrack_by_key = {}; g._pre_track_by_key = {}; g._key_last_used = {}; g._last_sweep_at = 0.0; g._tid_seq = 0
    g.TRACK_ALGO = algo; g.BYTETRACK_DETECTORS = ("person",)
    return g


def _d(label, detector, x, conf=0.8):
    return {"label": label, "conf": conf, "bbox": [x, 0.1, x + 0.2, 0.6], "detector": detector}


class TrackOnly(unittest.TestCase):
    def test_track_only_assigns_tids_without_inference(self):
        g = _guard("iou")
        fresh = [_d("person", "person", 0.1), _d("NO-Hardhat", "ppe", 0.15)]
        out1 = g.track_only(fresh, "cam:x:pf")
        out2 = g.track_only(fresh, "cam:x:pf")
        self.assertEqual({o["label"] for o in out1}, {"person", "NO-Hardhat"})
        self.assertTrue(all("tid" in o for o in out1))
        self.assertEqual([o["tid"] for o in out1], [o["tid"] for o in out2], "같은 박스를 다시 넣으면 같은 tid(추적 상태 유지)")
        self.assertIn("cam:x:pf", g._tracks_by_key)

    def test_track_only_merges_cross_source_person(self):
        """detect() 처럼 추적 뒤 교차소스 person 중복을 병합한다(같은 자리의 person 모델·ppe 모델 박스 → 1개)."""
        g = _guard("iou")
        fresh = [_d("person", "person", 0.1, 0.9), {"label": "person", "conf": 0.7, "bbox": [0.11, 0.1, 0.31, 0.6], "detector": "ppe"}]
        g.track_only(fresh, "k"); out = g.track_only(fresh, "k")
        self.assertEqual(sum(1 for o in out if o["label"] == "person"), 1, "교차소스 person 중복이 병합되지 않았다")

    def test_track_only_does_not_mutate_input(self):
        g = _guard("iou")
        fresh = [_d("person", "person", 0.1)]
        before = [dict(x) for x in fresh]
        g.track_only(fresh, "k"); g.track_only(fresh, "k")
        self.assertEqual(fresh, before, "입력 dict 가 추적 EMA 로 바뀌었다 — 사본을 넣어야 한다")

    def test_last_pre_track_returns_copies_and_sweep_clears(self):
        g = _guard("iou")
        g._pre_track_by_key["k"] = [_d("person", "person", 0.1)]
        a = g.last_pre_track("k"); a[0]["bbox"][0] = 9.9
        self.assertEqual(g._pre_track_by_key["k"][0]["bbox"][0], 0.1, "last_pre_track 이 원본을 그대로 내줬다")
        self.assertEqual(g.last_pre_track("없음"), [])
        g._key_last_used["k"] = 0.0; g.KEY_TTL_SEC = 0.0; g.SWEEP_INTERVAL_SEC = 0.0; g.MAX_TRACKED_KEYS = 100
        g._maybe_sweep_stale_keys(10.0)
        self.assertNotIn("k", g._pre_track_by_key)

    def test_detect_stores_raw_before_nms_and_track_only_applies_nms(self):
        from _source_probe import code_of
        code = code_of("vigent-core/engine/guard.py", "detect")
        self.assertIn("self._raw_store()[track_key] = [_copy_det(d) for d in detections]", code)
        self.assertLess(code.index("self._raw_store()[track_key]"), code.index("detections = _nms(detections)"), "원시 보관은 NMS 보다 먼저")
        g = _guard("iou")
        dup = [_d("person", "person", 0.1, 0.9), _d("person", "person", 0.1, 0.8)]   # 같은 자리 중복(NMS 대상)
        out = g.track_only(dup, "k"); out = g.track_only(dup, "k")
        self.assertEqual(sum(1 for o in out if o["label"] == "person"), 1, "track_only 가 같은 라벨 NMS 를 적용하지 않았다")
        g2 = _guard("iou"); g2.CONTAIN_RATIO = 0.70
        nested = [{"label": "person", "conf": 0.9, "bbox": [0.1, 0.1, 0.6, 0.9], "detector": "person"},
                  {"label": "person", "conf": 0.8, "bbox": [0.2, 0.2, 0.4, 0.6], "detector": "person"}]   # 큰 박스 안의 작은 박스(IoU 낮아 NMS 는 못 지움)
        out2 = g2.track_only(nested, "k2"); out2 = g2.track_only(nested, "k2")
        self.assertEqual(sum(1 for o in out2 if o["label"] == "person"), 1, "포함억제가 없으면 보조 유령 박스가 표시된다(2026-10-12 실측 tid 4개)")
        g._raw_by_key = {"k": [_d("person", "person", 0.1)]}; a = g.last_raw_pre_nms("k"); a[0]["bbox"][0] = 9
        self.assertEqual(g._raw_by_key["k"][0]["bbox"][0], 0.1, "last_raw_pre_nms 는 사본을 줘야 한다")

    def test_detect_stores_pre_track(self):
        from _source_probe import code_of
        code = code_of("vigent-core/engine/guard.py", "detect")
        self.assertIn("self._pre_track_store()[track_key] = [_copy_det(d) for d in _pre_track]", code)
        self.assertLess(code.index("_pre_track_store()[track_key]"), code.index("detections = self._track(detections, track_key)"),
                        "원시 검출 보관이 추적(_track) 보다 먼저여야 한다")


class WorkerUsesTrackOnlyOnFullsetFrames(unittest.TestCase):
    def test_process_frame_wiring(self):
        from _source_probe import code_of
        code = code_of("vigent-core/worker.py", "_process_frame")
        self.assertIn("if not do_full:", code)
        self.assertIn("pout = guard.detect(frame, detectors=_person_dets, track_key=_pf_key)", code)
        i_if = code.index("if not do_full:"); i_pf = code.index("pout = guard.detect(frame, detectors=_person_dets, track_key=_pf_key)")
        self.assertLess(i_if, i_pf, ":pf 추론은 풀세트 차례가 아닐 때만")
        self.assertIn("guard.last_raw_pre_nms(_main_key)", code, "표시용 사람은 NMS·억제 전 사람 모델 원시 박스여야 한다(2026-10-12 실사고)")
        self.assertIn("guard.last_pre_track(_main_key)", code)
        self.assertIn("_pd = guard.track_only(_persons, _pf_key)", code)
        self.assertIn('str(d.get("label", "")).lower() == "person" and d.get("detector") == "person"', code,
                      "표시용 사람은 사람 모델의 person 이어야 한다 — 보호구 모델의 Person 이 섞이면 박스가 튄다(2026-10-12 실사고)")
        self.assertIn('d.get("detector") == "ppe" and str(d.get("label", "")).lower() != "person"', code,
                      "보호구 feed 에서 그 모델의 Person 은 제외")
        self.assertIn("_pf_ppe_key)", code, "보호구는 별도 키로 추적")
        self.assertEqual(code.count("guard.detect("), 3, "detect 호출은 :pf 사람·:pf 보호구(비풀세트)·풀세트 세 곳뿐이어야 한다")


if __name__ == "__main__":
    unittest.main()
