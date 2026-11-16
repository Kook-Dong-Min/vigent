"""[2026-10-12] 근접 경보 문구 — COCO 차량 라벨은 '차량' 으로 묶고 원래 검출 라벨은 note 에 남긴다.

실기(10-12 04:33, VIGENT_AGENTS=0): COCO 사전학습 person 슬롯이 야드 차량을 'motorcycle' 로 분류해 텔레그램에
"motorcycle 작업반경 침입" 이 그대로 갔다. 고정하는 것:
  ① vehicle_display: motorcycle·truck·car·bus·train·boat·bicycle → '차량', forklift → '지게차', 미지 라벨은 그대로.
  ② proximity_note: 표시명으로 시작하고 거리와 '(검출 라벨 <원라벨>)' 을 포함한다 — 원라벨이 기록(note)에 남는다.
  ③ 워커 발화 경로가 proximity_note 를 쓴다(두 분기 모두) — 정적 확인.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import proximity  # noqa: E402


class VehicleDisplay(unittest.TestCase):
    def test_coco_vehicles_grouped(self):
        for lab in ("motorcycle", "truck", "car", "bus", "train", "boat", "bicycle", "Truck", " MOTORCYCLE "):
            self.assertEqual(proximity.vehicle_display(lab), "차량", lab)

    def test_known_equipment_korean_and_unknown_passthrough(self):
        self.assertEqual(proximity.vehicle_display("forklift"), "지게차")
        self.assertEqual(proximity.vehicle_display("excavator"), "굴착기")
        self.assertEqual(proximity.vehicle_display("agv"), "agv")
        self.assertEqual(proximity.vehicle_display(""), "")

    def test_note_keeps_raw_label_for_analysis(self):
        n = proximity.proximity_note({"vehicle": "motorcycle", "distance_m": 2.1})
        self.assertTrue(n.startswith("차량 작업반경 침입 — 사람 약 2.1m"), n)
        self.assertIn("(검출 라벨 motorcycle)", n); self.assertNotIn("motorcycle 작업반경", n)
        n2 = proximity.proximity_note({"vehicle": "forklift", "distance_m": 1.4})
        self.assertTrue(n2.startswith("지게차 작업반경 침입 — 사람 약 1.4m"), n2); self.assertIn("(검출 라벨 forklift)", n2)

    def test_worker_uses_helper_on_both_branches(self):
        src = (_ROOT / "vigent-core" / "worker.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("proximity.proximity_note(prox_hit)"), 2)
        self.assertNotIn("prox_hit['vehicle']} 작업반경", src)


if __name__ == "__main__":
    unittest.main()
