"""tests/test_ocsort_tracker.py — track.algo="ocsort" (2026-10-12, 사용자 결정).

trackers 2.4.0(Apache) 의 OCSORTTracker 를 ByteTrack 과 같은 경로(_track_bytetrack, 라벨별 인스턴스, tid 네임스페이스)로 쓴다.
고정하는 것:
  ① _track 디스패처: ocsort 도 칼만 경로(_track_bytetrack)로 간다 / iou 는 그대로
  ② 인스턴스 공장: ocsort → OCSORTTracker, 고신뢰 기준 = 검출기 운용 임계(person 0.40·ppe 0.35 — 신규 트랙 생성 기준을 겸함), BYTETRACK_* 공유
     bytetrack → ByteTrackTracker(기존 인자 그대로)
  ③ person 슬롯 저신뢰 하향(BYTETRACK_LOW_CONF)이 ocsort 에도 적용된다(소스 잠금)
  ④ 실제 라이브러리로 tid 부여·라벨별 네임스페이스 확인(합성 박스)
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vigent-core")); sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervision  # noqa: E402,F401,I001
import engine.guard as G  # noqa: E402


class _Fake:
    instances: list = []

    def __init__(self, **kw):
        self.kw = kw; _Fake.instances.append(self)

    def update(self, det):
        det.tracker_id = np.arange(len(det), dtype=int) + 1; return det


class _FakeOC(_Fake):
    pass


class _FakeBT(_Fake):
    pass


def _guard(algo, detectors=("person", "ppe")) -> G.Guard:
    g = G.Guard.__new__(G.Guard)
    g._tracks_by_key = {}; g._bytetrack_by_key = {}; g._pre_track_by_key = {}; g._key_last_used = {}; g._last_sweep_at = 0.0; g._tid_seq = 0
    g.TRACK_ALGO = algo; g.BYTETRACK_DETECTORS = tuple(detectors)
    g.DETECTOR_CONF = {"person": 0.40, "ppe": 0.35}; g.DEFAULT_CONF = 0.5; g.BYTETRACK_HIGH_CONF = 0.50
    return g


def _d(label, detector, x, conf=0.8):
    return {"label": label, "conf": conf, "bbox": [x, 0.1, x + 0.2, 0.6], "detector": detector}


class Factory(unittest.TestCase):
    def setUp(self):
        _Fake.instances = []
        fake = types.ModuleType("trackers"); fake.OCSORTTracker = _FakeOC; fake.ByteTrackTracker = _FakeBT
        pp = mock.patch.dict(sys.modules, {"trackers": fake}); pp.start(); self.addCleanup(pp.stop)

    def test_ocsort_instances_and_thresholds(self):
        g = _guard("ocsort")
        out = g._track([_d("person", "person", 0.1), _d("NO-Hardhat", "ppe", 0.5)], "cam")
        self.assertEqual({type(i).__name__ for i in _Fake.instances}, {"_FakeOC"})
        per = g._bytetrack_by_key["cam"].kw; hat = g._bytetrack_by_key["cam|NO-Hardhat"].kw
        self.assertAlmostEqual(per["high_conf_det_threshold"], 0.40, msg="person: OC-SORT 고신뢰=운용 임계(신규 트랙 생성 기준)")
        self.assertAlmostEqual(hat["high_conf_det_threshold"], 0.35, msg="ppe: 0.50 이면 0.35~0.50 박스가 사라진다")
        self.assertNotIn("track_activation_threshold", per, "OC-SORT 엔 활성화 임계 인자가 없다")
        self.assertEqual(per["minimum_consecutive_frames"], g.BYTETRACK_MIN_FRAMES); self.assertEqual(per["minimum_iou_threshold"], g.BYTETRACK_MIN_IOU)
        self.assertEqual(per["direction_consistency_weight"], g.OCSORT_DIRECTION_WEIGHT); self.assertEqual(per["delta_t"], g.OCSORT_DELTA_T)
        by = {o["label"]: o["tid"] for o in out}
        self.assertGreaterEqual(by["person"], G._BYTETRACK_TID_OFFSET); self.assertNotEqual(by["person"] // G._BYTETRACK_LABEL_SPAN, by["NO-Hardhat"] // G._BYTETRACK_LABEL_SPAN)

    def test_bytetrack_unchanged(self):
        g = _guard("bytetrack")
        g._track([_d("person", "person", 0.1)], "cam")
        self.assertEqual([type(i).__name__ for i in _Fake.instances], ["_FakeBT"])
        self.assertAlmostEqual(_Fake.instances[0].kw["track_activation_threshold"], 0.40); self.assertAlmostEqual(_Fake.instances[0].kw["high_conf_det_threshold"], 0.50)

    def test_iou_untouched(self):
        g = _guard("iou")
        out = g._track([_d("person", "person", 0.1)], "cam")
        self.assertEqual(_Fake.instances, []); self.assertLess(out[0]["tid"], G._BYTETRACK_TID_OFFSET)


class SourceLocks(unittest.TestCase):
    def test_low_conf_applies_to_ocsort(self):
        from _source_probe import code_of
        code = code_of("vigent-core/engine/guard.py", "detect")
        self.assertIn('self.TRACK_ALGO in ("bytetrack", "ocsort") and conf_override is None', code)
        disp = code_of("vigent-core/engine/guard.py", "_track")
        self.assertIn('self.TRACK_ALGO in ("bytetrack", "ocsort")', disp)

    def test_repo_tuning_is_ocsort(self):
        import tuning
        self.assertEqual(str(tuning.val("track", "algo", "iou")).lower(), "ocsort")


class RealLibrary(unittest.TestCase):
    def test_real_ocsort_assigns_ids_per_label(self):
        g = _guard("ocsort")
        fresh = [_d("person", "person", 0.1, 0.9), _d("NO-Hardhat", "ppe", 0.12, 0.41)]
        g._track(fresh, "cam"); out = g._track(fresh, "cam")      # 첫 프레임은 라이브러리가 -1 을 줄 수 있어 2프레임째를 본다
        by = {o["label"]: o["tid"] for o in out}
        self.assertEqual(set(by), {"person", "NO-Hardhat"}, "0.41 보호구 박스가 OC-SORT 에서 사라졌다(고신뢰 기준 오설정)")
        self.assertTrue(all(v >= G._BYTETRACK_TID_OFFSET for v in by.values()))


if __name__ == "__main__":
    unittest.main()
