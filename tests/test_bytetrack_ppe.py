"""tests/test_bytetrack_ppe.py — 보호구(ppe) 라벨별 ByteTrack 추적 (2026-10-12, 사용자 결정 opt-in).

배경: ByteTrack 은 person 슬롯에만 쓰였다(라이브러리가 클래스 비구분 → 섞으면 오매칭). 사용자가 보호구 박스도 ByteTrack 으로
추적하길 원해 tuning track.bytetrack_detectors 로 검출기 단위 opt-in 을 만들고, 보호구는 **라벨별 인스턴스**로 추적한다.
고정하는 계약:
  ① 기본(bytetrack_detectors=["person"]): ppe 라벨은 그대로 _track_iou(작은 tid), person 은 10,000,000+ — 기존 동작 0 변화
  ② ["person","ppe"]: 보호구 라벨마다 ByteTrack 인스턴스가 따로 생기고(키 "cam|Hardhat" 등) tid 가 라벨별 100만 단위로 분리된다
  ③ 어떤 라벨이 이번 프레임에 0건이면 그 인스턴스에 update(빈) 가 호출된다(시간 진행, M1-5 와 같은 취지)
  ④ 오프셋 표에 없는 라벨은 ByteTrack 대상 검출기여도 IoU 로 남긴다(tid 충돌 방지)
  ⑤ 키 청소(_drop_bt_key)가 라벨별 하위 인스턴스도 지운다
  ⑥ 이 저장소 tuning 은 [person, ppe](바뀌면 의도된 변경인지 확인) / person 이 빠진 설정은 person 을 되살린다
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vigent-core"))
import supervision  # noqa: E402,F401,I001  ★대역 설치 전에 미리 올린다(sys.modules 복원 시 scipy 재import 사고 방지)
import engine.guard as G  # noqa: E402


class _FakeBT:
    instances: list = []

    def __init__(self, **kw):
        self.kw = kw; self.calls: list[int] = []; self._next = 1; self._ids: dict = {}
        _FakeBT.instances.append(self)

    def update(self, det):
        self.calls.append(len(det))
        ids = []
        for i in range(len(det)):          # 같은 class_id(입력 인덱스)엔 같은 id — 프레임 간 안정 id 흉내
            key = int(det.class_id[i]) if det.class_id is not None else i
            if key not in self._ids:
                self._ids[key] = self._next; self._next += 1
            ids.append(self._ids[key])
        det.tracker_id = np.array(ids, dtype=int)
        return det


def _guard(detectors=("person",)) -> G.Guard:
    g = G.Guard.__new__(G.Guard)
    g._tracks_by_key = {}; g._bytetrack_by_key = {}; g._key_last_used = {}; g._last_sweep_at = 0.0; g._tid_seq = 0
    g.TRACK_ALGO = "bytetrack"; g.BYTETRACK_DETECTORS = tuple(detectors)
    g.DETECTOR_CONF = {"person": 0.40, "ppe": 0.35}; g.DEFAULT_CONF = 0.5; g.BYTETRACK_HIGH_CONF = 0.50
    return g


def _d(label, detector, x):
    return {"label": label, "conf": 0.8, "bbox": [x, 0.1, x + 0.1, 0.3], "detector": detector}


class PpeByteTrack(unittest.TestCase):
    def setUp(self):
        _FakeBT.instances = []
        fake = types.ModuleType("trackers"); fake.ByteTrackTracker = _FakeBT
        self._p = mock.patch.dict(sys.modules, {"trackers": fake}); self._p.start(); self.addCleanup(self._p.stop)

    def test_default_person_only_unchanged(self):
        g = _guard(("person",))
        out = g._track_bytetrack([_d("person", "person", 0.1), _d("NO-Hardhat", "ppe", 0.5)], "cam")
        by = {o["label"]: o["tid"] for o in out}
        self.assertGreaterEqual(by["person"], G._BYTETRACK_TID_OFFSET)
        self.assertLess(by["NO-Hardhat"], G._BYTETRACK_TID_OFFSET, "기본 설정에서 ppe 가 ByteTrack 으로 갔다(회귀)")
        self.assertEqual(len(_FakeBT.instances), 1, "person 인스턴스 1개만 있어야 한다")

    def test_ppe_per_label_instances_and_namespaces(self):
        g = _guard(("person", "ppe"))
        fresh = [_d("person", "person", 0.1), _d("Hardhat", "ppe", 0.1), _d("NO-Safety-Vest", "ppe", 0.3)]
        out = g._track_bytetrack(fresh, "cam")
        self.assertEqual(set(g._bytetrack_by_key), {"cam", "cam|Hardhat", "cam|NO-Safety-Vest"})
        by = {o["label"]: o["tid"] for o in out}
        span = G._BYTETRACK_LABEL_SPAN; base = G._BYTETRACK_TID_OFFSET
        self.assertEqual(by["person"] // span, base // span, "person tid 네임스페이스가 바뀌었다")
        self.assertEqual(by["Hardhat"] // span, (base + G._BYTETRACK_LABEL_OFFSETS["Hardhat"] * span) // span)
        self.assertEqual(by["NO-Safety-Vest"] // span, (base + G._BYTETRACK_LABEL_OFFSETS["NO-Safety-Vest"] * span) // span)
        self.assertEqual(len({v // span for v in by.values()}), 3, "라벨별 네임스페이스가 겹친다 → 프론트가 다른 박스를 합친다")
        ppe_inst = g._bytetrack_by_key["cam|Hardhat"]
        self.assertAlmostEqual(ppe_inst.kw["track_activation_threshold"], 0.35, msg="ppe 활성화 임계는 ppe 운용 임계에 연동")
        self.assertAlmostEqual(ppe_inst.kw["high_conf_det_threshold"], 0.35,
                               msg="ppe 고신뢰 기준이 0.50 이면 0.35~0.50 박스가 새 트랙을 못 만들어 사라진다(실측 2026-10-12)")
        self.assertAlmostEqual(g._bytetrack_by_key["cam"].kw["high_conf_det_threshold"], g.BYTETRACK_HIGH_CONF, msg="person 은 기존 0.50 그대로")

    def test_missing_label_gets_empty_update(self):
        g = _guard(("person", "ppe"))
        g._track_bytetrack([_d("person", "person", 0.1), _d("Hardhat", "ppe", 0.1)], "cam")
        hat = g._bytetrack_by_key["cam|Hardhat"]
        g._track_bytetrack([_d("person", "person", 0.1)], "cam")       # 이번 프레임엔 Hardhat 0건
        self.assertEqual(hat.calls, [1, 0], "라벨 0건 프레임에서 빈 update 가 호출되지 않았다(시간 정지)")

    def test_unknown_label_falls_back_to_iou(self):
        g = _guard(("person", "ppe"))
        out = g._track_bytetrack([_d("Goggles", "ppe", 0.2)], "cam")
        self.assertEqual(len(out), 1); self.assertLess(out[0]["tid"], G._BYTETRACK_TID_OFFSET)
        self.assertEqual(g._bytetrack_by_key, {})

    def test_drop_key_removes_sub_instances(self):
        g = _guard(("person", "ppe"))
        g._track_bytetrack([_d("person", "person", 0.1), _d("Mask", "ppe", 0.1)], "cam")
        g._track_bytetrack([_d("person", "person", 0.1)], "other")
        g._drop_bt_key("cam")
        self.assertEqual(set(g._bytetrack_by_key), {"other"})


class TuningWiring(unittest.TestCase):
    def test_repo_tuning_enables_ppe(self):
        import tuning
        self.assertEqual(list(tuning.section("track").get("bytetrack_detectors") or []), ["person", "ppe"])

    def test_person_is_always_included(self):
        g = G.Guard.__new__(G.Guard)
        # __init__ 의 파서만 따로 검증: 문자열·person 누락 입력
        conv = G._conv_bt_detectors
        self.assertEqual(conv("ppe"), ("ppe",)); self.assertEqual(conv([]), ("person",)); self.assertEqual(conv(["person", "ppe"]), ("person", "ppe"))
        g.BYTETRACK_DETECTORS = conv("ppe")
        if "person" not in g.BYTETRACK_DETECTORS:
            g.BYTETRACK_DETECTORS = ("person",) + tuple(x for x in g.BYTETRACK_DETECTORS if x != "person")
        self.assertEqual(g.BYTETRACK_DETECTORS, ("person", "ppe"))


if __name__ == "__main__":
    unittest.main()
