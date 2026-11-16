"""[2026-10-12] 발화 규칙 전수에 한국어 표시명이 있다 — 정본(rule_labels.RULE_DISPLAY_KO)과 허브(index_hub.html RULE_KO)가 일치.

실사고: 허브 이벤트 목록에 'ergonomic_risk' 가 영어 그대로. 발화 규칙은 코드에서 뽑는다(하드코딩 목록 아님):
  · worker.py 의 ("rule", "level", …) 튜플(fired.append 와 out[...] = (...) 두 형태)
  · ErgonomicsTracker 경로 "ergonomic_risk"
  · routers/safety_core.py 센서 spec 의 규칙
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core"))

import rule_labels  # noqa: E402

_TUPLE = re.compile(r'\(\s*"([a-z_]+)"\s*,\s*"(critical|high|mid|low)"\s*,')
_SENSOR = re.compile(r'"[a-z0-9]+":\s*\(\s*"([a-z_]+)"\s*,\s*lambda')
_HUB = re.compile(r"var RULE_KO=\{(.*?)\};", re.S)
_PAIR = re.compile(r"([a-z_]+):'([^']+)'")


def emitted_rules() -> set[str]:
    w = (_ROOT / "vigent-core" / "worker.py").read_text(encoding="utf-8")
    rules = set(_TUPLE.findall(w) and [m[0] for m in _TUPLE.findall(w)])
    rules.add("ergonomic_risk")
    sc = (_ROOT / "vigent-core" / "routers" / "safety_core.py").read_text(encoding="utf-8")
    rules |= set(_SENSOR.findall(sc))
    return rules


class EveryEmittedRuleHasKoreanName(unittest.TestCase):
    def test_extraction_finds_known_rules(self):
        r = emitted_rules()
        self.assertTrue({"zone_intrusion", "ppe_missing", "fire_smoke", "proximity_hazard", "crowd_density", "immobility",
                         "rapid_motion", "ergonomic_risk", "asphyxiation", "gas_alarm", "heat_stress"} <= r, sorted(r))

    def test_python_source_covers_all(self):
        missing = emitted_rules() - set(rule_labels.RULE_DISPLAY_KO)
        self.assertEqual(missing, set(), f"표시명 없는 규칙: {sorted(missing)}")
        for k, v in rule_labels.RULE_DISPLAY_KO.items():
            self.assertTrue(v and not re.fullmatch(r"[a-z_]+", v), (k, v))   # 영어 id 를 그대로 둔 값 금지

    def test_hub_dictionary_matches_python_source(self):
        html = (_ROOT / "themes" / "safety" / "index_hub.html").read_text(encoding="utf-8")
        m = _HUB.search(html); self.assertIsNotNone(m, "허브 RULE_KO 를 못 찾음")
        hub = dict(_PAIR.findall(m.group(1)))
        self.assertEqual(emitted_rules() - set(hub), set(), "허브 사전에 없는 발화 규칙")
        self.assertEqual(hub, rule_labels.RULE_DISPLAY_KO, "허브 RULE_KO 와 정본이 다르다")

    def test_rule_ko_fallback_keeps_id(self):
        self.assertEqual(rule_labels.rule_ko("immobility"), "장시간 무동작"); self.assertEqual(rule_labels.rule_ko("x_y"), "x_y")


if __name__ == "__main__":
    unittest.main()
