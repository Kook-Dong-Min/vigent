"""[2026-10-12] 통보 스레드 로그 문구 분리 — 기록 전용 등급 vs 원격 등급 미도달.

실기(2026-10-12 04:32, VIGENT_AGENTS=0): 근골격(low)·인원 밀집(mid)은 on_severity 에 원격 동작이 없는 기록 전용 등급인데
"경보 즉시 전송 미도달 — 재시도 큐로 이월" 로 찍혀 전송 장애로 오인됐다(큐에도 안 들어가므로 '이월' 도 사실이 아님).
고정하는 것:
  ① actions 에 alarm/manager_call 이 없으면 "기록 전용 등급 — 원격 전송 대상 아님" INFO + stats.log_only(failed 는 안 늘어남).
  ② actions 에 원격 동작이 있는데 delivered 가 아니면 예전 문구 "미도달 — 재시도 큐로 이월" + stats.failed.
  ③ delivered 면 둘 다 안 나오고 stats.sent.
  ④ actions 가 없는 결과(stub)는 예전처럼 '미도달' 로 본다(기존 테스트 test_alert_wiring 호환).
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import alert_gate  # noqa: E402
import alert_notify  # noqa: E402
from _isolate import isolate_alerts  # noqa: E402


class LogWording(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts()); alert_notify.reset_for_test(); alert_gate.reset()
        self.addCleanup(alert_notify.reset_for_test); self.addCleanup(alert_notify.stop)

    def _run(self, res: dict, level: str, expect_logs: bool = True):
        alert_notify.set_sender(lambda lvl, msg, meta: res)
        alert_notify.start()
        ctx = (self.assertLogs("vigent.alert_notify", level="INFO") if expect_logs
               else self.assertNoLogs("vigent.alert_notify", level="INFO"))
        with ctx as cm:
            r = alert_notify.submit("cam-t", "crowd_density", level, f"[t] {level} 시험")
            self.assertTrue(r["queued"], r)
            for _ in range(60):
                st = alert_notify.stats()
                if st["sent"] + st["failed"] + st["log_only"]:
                    break
                time.sleep(0.05)
        return alert_notify.stats(), ("\n".join(cm.output) if expect_logs else "")

    def test_log_only_level_gets_record_only_wording(self):
        st, out = self._run({"actions": ["log"], "delivered": False, "fallback": True}, "mid")
        self.assertEqual((st["log_only"], st["failed"], st["sent"]), (1, 0, 0))
        self.assertIn("기록 전용 등급 — 원격 전송 대상 아님", out); self.assertNotIn("재시도 큐로 이월", out)

    def test_remote_level_not_delivered_keeps_carry_over_wording(self):
        st, out = self._run({"actions": ["alarm", "manager_call"], "delivered": False}, "high")
        self.assertEqual((st["log_only"], st["failed"], st["sent"]), (0, 1, 0))
        self.assertIn("경보 즉시 전송 미도달 — 재시도 큐로 이월", out); self.assertNotIn("기록 전용 등급", out)

    def test_delivered_logs_neither(self):
        st, _ = self._run({"actions": ["alarm"], "delivered": True}, "high", expect_logs=False)   # INFO 로그 0건(assertNoLogs)
        self.assertEqual((st["log_only"], st["failed"], st["sent"]), (0, 0, 1))

    def test_stub_without_actions_is_treated_as_not_delivered(self):
        st, out = self._run({}, "critical")
        self.assertEqual((st["log_only"], st["failed"]), (0, 1)); self.assertIn("미도달", out)


if __name__ == "__main__":
    unittest.main()
