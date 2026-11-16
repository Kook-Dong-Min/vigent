"""[②-a, 2026-10-12] RULE_KB 를 agents/scribe → rule_labels(핵심)로 옮긴 뒤 동작·내용 불변 고정."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core"))

import rule_labels  # noqa: E402

_WORKER_RULES = {"zone_intrusion", "ppe_missing", "fire_smoke", "proximity_hazard", "crowd_density", "ergonomic_risk"}


class RuleLabelsMove(unittest.TestCase):
    def test_worker_rules_present_with_required_fields(self):
        kb = rule_labels.RULE_KB
        self.assertTrue(_WORKER_RULES <= set(kb), _WORKER_RULES - set(kb))
        for r in _WORKER_RULES:
            self.assertTrue({"hazard", "sev"} <= set(kb[r]), (r, sorted(kb[r])))

    def test_recognition_allowlist_uses_core_module(self):
        from routers import recognition
        src = (_ROOT / "vigent-core" / "routers" / "recognition.py").read_text(encoding="utf-8")
        self.assertNotIn("from agents.scribe import", src, "recognition 이 다시 에이전트 층을 import 한다")
        self.assertTrue(set(rule_labels.RULE_KB) <= recognition.allowed_rules())


if __name__ == "__main__":
    unittest.main()
