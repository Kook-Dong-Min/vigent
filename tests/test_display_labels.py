"""[2026-10-12] 관제 화면 표시 클래스(detect.display_labels) — 표시만 거르고 검출·경보 판정에는 손대지 않는다.

고정하는 것:
  ① 기본값: train·boat·knife·scissors 는 표시 안 함, person·forklift·truck·fire 는 표시. 보호구류(NO-Hardhat 등)는 이름으로 통과.
  ② tuning detect.display_labels 로 바꾸면 그대로 반영(리스트·쉼표 문자열).
  ③ 판정 경로(worker·alert_gate·alert_notify·proximity·zone_debounce·engine/guard)는 is_safety_label/display_labels 를 부르지 않는다(정적).
  ④ /cameras/{cid}/detections 는 표시 필터를 거치지만 워커가 들고 있는 검출(_last_dets)과 인원 수는 그대로다(기능).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import tuning  # noqa: E402
import web_util  # noqa: E402
from _isolate import isolate_alerts, isolate_no_token, isolate_shutdown, isolate_warmup  # noqa: E402


class DefaultDisplayLabels(unittest.TestCase):
    def test_default_excludes_non_safety_coco_classes(self):
        with mock.patch.object(tuning, "val", side_effect=lambda sec, key, default, env=None: default):
            for lab in ("train", "boat", "knife", "scissors", "bench", "default"):
                self.assertFalse(web_util.is_safety_label(lab), lab)
            for lab in ("person", "Person", "forklift", "truck", "motorcycle", "fire", "smoke", "NO-Hardhat", "Safety-Vest", "Mask"):
                self.assertTrue(web_util.is_safety_label(lab), lab)

    def test_yaml_default_matches_code_default(self):
        import yaml
        cfg = yaml.safe_load((_ROOT / "config" / "tuning.yaml").read_text(encoding="utf-8"))
        self.assertEqual(list(cfg["detect"]["display_labels"]), list(web_util._DISPLAY_LABELS_DEFAULT))

    def test_tuning_override_is_honoured(self):
        with mock.patch.object(tuning, "val", side_effect=lambda sec, key, default, env=None: ["person", "train"]):
            self.assertTrue(web_util.is_safety_label("train")); self.assertFalse(web_util.is_safety_label("forklift"))
        with mock.patch.object(tuning, "val", side_effect=lambda sec, key, default, env=None: "person, boat"):
            self.assertTrue(web_util.is_safety_label("boat")); self.assertFalse(web_util.is_safety_label("truck"))


class DetectionAndAlertsUnaffected(unittest.TestCase):
    def test_judgement_paths_do_not_use_display_filter(self):
        for rel in ("worker.py", "alert_gate.py", "alert_notify.py", "proximity.py", "zone_debounce.py", "zone_tile.py",
                    "engine/guard.py", "engine/dispatcher.py", "data_engine.py"):
            src = (_ROOT / "vigent-core" / rel).read_text(encoding="utf-8")
            self.assertNotIn("is_safety_label", src, rel); self.assertNotIn("display_labels", src, rel)

    def test_detections_api_filters_display_only(self):
        import main
        import worker
        from fastapi.testclient import TestClient
        self.addCleanup(isolate_alerts()); self.addCleanup(isolate_shutdown()); self.addCleanup(isolate_no_token()); self.addCleanup(isolate_warmup())
        dets = [{"class": "person", "bbox": [0.1, 0.1, 0.2, 0.3], "conf": 0.9, "stale": False},
                {"class": "train", "bbox": [0.5, 0.5, 0.9, 0.9], "conf": 0.8, "stale": False}]
        fake = mock.Mock(); fake.state = {"running": True}; fake._last_dets = dets; fake._last_det_ts = 1.0
        fake._last_pc = 1; fake._last_sig = {}; fake._last_fired = []
        with mock.patch.dict(worker.manager._workers, {"camx": fake}, clear=False), TestClient(main.app) as c:
            r = c.get("/cameras/camx/detections")
        self.assertEqual(r.status_code, 200, r.text[:200])
        shown = [d["class"] for d in r.json()["detections"]]
        self.assertEqual(shown, ["person"], "표시 필터가 train 을 걸러야 한다")
        self.assertEqual([d["class"] for d in fake._last_dets], ["person", "train"], "워커 검출은 그대로여야 한다")
        self.assertEqual(r.json()["person_count"], 1)


if __name__ == "__main__":
    unittest.main()
