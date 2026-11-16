"""[2026-10-12] 등급 이름 정합 — 코드가 내는 등급(critical·high·mid·low)과 dispatch.on_severity 키가 같다.

배경: 코드는 'mid'(worker crowd_density), 설정은 'medium' 이라 'mid' 가 항상 기본값(log)으로 떨어졌다(동작은 우연히 같았지만
`medium: [alarm]` 으로 바꿔도 'mid' 경보에 적용되지 않는 잠재 결함 · 2026-08-21 데드레터 사고의 뿌리).
고정하는 것:
  ① vision.yaml on_severity 키 ⊇ 워커가 발화하는 등급 전수(`fired.append` + ErgonomicsTracker 'low') 이고 'medium' 키는 없다.
  ② 옛 설정('medium')을 주면 Dispatcher 가 'mid' 로 읽고 WARNING 1줄, 동작은 같다(기록 전용). 둘 다 있으면 'mid' 우선.
  ③ dispatch('mid')·dispatch('medium') 은 예전처럼 원격 전송 없음·큐 미진입(delivered False, actions ['log']) — 동작 불변.
  ④ 'high' 는 원격 등급 그대로.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

from _isolate import isolate_alerts  # noqa: E402
from engine.dispatcher import Dispatcher  # noqa: E402


class _Cfg:
    def __init__(self, on_severity):
        self.raw = {"dispatch": {"on_severity": on_severity}}


def _worker_levels() -> set[str]:
    src = (_ROOT / "vigent-core" / "worker.py").read_text(encoding="utf-8")
    levels = set(re.findall(r'fired\.append\(\("[a-z_]+", "([a-z]+)"', src))
    levels.add("low")                      # ErgonomicsTracker → ("ergonomic_risk", "low", …) (test_ergonomic_level_low)
    return levels


class ConfigKeysMatchCode(unittest.TestCase):
    def test_vision_yaml_keys_cover_all_emitted_levels(self):
        cfg = yaml.safe_load((_ROOT / "themes" / "safety" / "vision.yaml").read_text(encoding="utf-8"))
        keys = set((cfg.get("dispatch") or {}).get("on_severity") or {})
        emitted = _worker_levels()
        self.assertTrue(emitted, "워커 등급을 못 뽑았다")
        self.assertTrue(emitted <= keys, f"설정에 없는 등급: {sorted(emitted - keys)} (키 {sorted(keys)})")
        self.assertNotIn("medium", keys, "옛 이름 'medium' 이 남아 있다")
        self.assertEqual(keys & {"critical", "high", "mid", "low"}, {"critical", "high", "mid", "low"})


class LegacyMediumAlias(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts())

    def test_medium_is_read_as_mid_with_warning(self):
        with self.assertLogs("vigent.dispatcher", level="WARNING") as cm:
            a = Dispatcher(_Cfg({"critical": ["alarm"], "high": ["alarm"], "medium": ["log"]}))
        self.assertIn("mid", a.on_severity); self.assertNotIn("medium", a.on_severity)
        self.assertEqual(len([m for m in cm.output if "medium" in m]), 1)
        self.assertEqual(a._actions_for("mid"), ["log"]); self.assertEqual(a._actions_for("medium"), ["log"])
        self.assertFalse(a._remote_level("mid")); self.assertFalse(a._remote_level("medium")); self.assertTrue(a._remote_level("high"))

    def test_both_names_present_prefers_mid(self):
        with self.assertLogs("vigent.dispatcher", level="WARNING"):
            a = Dispatcher(_Cfg({"mid": ["alarm"], "medium": ["log"], "high": ["alarm"]}))
        self.assertEqual(a.on_severity["mid"], ["alarm"]); self.assertNotIn("medium", a.on_severity)

    def test_new_config_logs_no_warning(self):
        with self.assertNoLogs("vigent.dispatcher", level="WARNING"):
            a = Dispatcher(_Cfg({"critical": ["alarm"], "high": ["alarm"], "mid": ["log"], "low": ["log"]}))
        self.assertEqual(sorted(a.on_severity), ["critical", "high", "low", "mid"])


class BehaviourUnchanged(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts())

    def test_mid_and_medium_stay_record_only(self):
        a = Dispatcher(_Cfg({"critical": ["alarm"], "high": ["alarm", "manager_call"], "mid": ["log"], "low": ["log"]}))
        import alert_queue
        with mock.patch.object(alert_queue, "enqueue") as enq, \
             mock.patch.object(a, "_send_telegram") as tg, mock.patch.object(a, "_send_email") as em, mock.patch.object(a, "_send_webhook") as wh:
            for lv in ("mid", "medium", "low"):
                r = a.dispatch(lv, f"{lv} 시험")
                self.assertFalse(r.get("delivered"), lv); self.assertEqual(r["actions"], ["log"], lv)
            enq.assert_not_called(); tg.assert_not_called(); em.assert_not_called(); wh.assert_not_called()
            self.assertTrue(a._queue_enabled("high") or not a.channels_configured())   # high 는 채널이 있으면 큐 대상


if __name__ == "__main__":
    unittest.main()
