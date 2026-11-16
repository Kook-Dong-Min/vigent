"""Dispatcher(코어) 동작 — [⑤-2, 2026-10-12] test_scribe_copilot_dispatcher 에서 Dispatcher 2건만 옮김(Scribe·Copilot 은 삭제).
① 채널 키가 없으면 예외 없이 폴백(로그) ② critical 의 safety_relay_signal 은 보조 신호(§8).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import vision_loader  # noqa: E402
from _isolate import isolate_alerts, isolate_data_dirs  # noqa: E402
from engine import build_agents  # noqa: E402


class DispatcherCore(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts()); self.addCleanup(isolate_data_dirs())
        self.agents = build_agents(vision_loader.load_vision("safety"))

    def test_dispatcher_fallback_without_keys(self):
        import engine.dispatcher as _d
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_URL"):
            os.environ.pop(k, None)
        with mock.patch.object(_d, "notify_cfg", return_value={"telegram_token": None, "telegram_chat": None, "webhook_url": None,
                                                              "smtp_host": None, "smtp_port": 587, "smtp_user": None,
                                                              "smtp_pass": None, "email_to": None}):
            r = self.agents["Dispatcher"].dispatch("high", "테스트")
        self.assertFalse(r["delivered"]); self.assertTrue(r["fallback"])
        self.assertTrue(any(x["channel"] == "log" and x["sent"] for x in r["results"]))

    def test_dispatcher_critical_relay_is_supplementary(self):
        r = self.agents["Dispatcher"].dispatch("critical", "프레스 우회")
        relay = [x for x in r["results"] if x["channel"] == "safety_relay_signal"]
        self.assertTrue(relay); self.assertIn("보조", relay[0]["note"])

    def test_build_agents_is_core_two(self):
        self.assertEqual(sorted(self.agents), ["Dispatcher", "Guard"])


if __name__ == "__main__":
    unittest.main()
