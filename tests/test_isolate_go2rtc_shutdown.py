"""tests/test_isolate_go2rtc_shutdown.py — 테스트가 실제 서버의 go2rtc 를 죽이지 않는지 (2026-10-12 실사고).

사고: 개발기에서 서버를 켜둔 채 전체 스위트를 돌렸더니 11:02:27 부터 웹캠 워커가 go2rtc RTSP 재연결 루프에 빠지고 허브가
스냅샷 폴백(1.2 초)으로 "뚝뚝 끊겼다". lifespan 을 도는 테스트(test_gpu_fallback·test_token_isolation·test_vision_only_pages)의
종료 훅이 진짜 stop_go2rtc() 를 불렀고, 테스트 프로세스엔 _G2_PROC 핸들이 없어 PID 파일 경로 → 저장소 data/go2rtc.pid 의
**실제 서버 go2rtc** 를 terminate 하고 PID 파일까지 지웠다. isolate_warmup 은 ensure 만 막고 stop 은 안 막았다.
고정하는 것:
  ① isolate_warmup() 활성 중엔 stop_go2rtc 가 가짜(False)라 PID 파일·프로세스를 건드리지 않는다.
  ② 격리 없이 stop_go2rtc 를 부르면 PID 파일 경로가 **실행된다**(= 격리가 왜 필요한지 — 가짜 PID 파일로 재현, 실제 프로세스 접촉 없음).
  ③ lifespan 을 도는 세 테스트 파일이 isolate_warmup 을 쓴다(소스로 잠금).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

from _isolate import isolate_warmup  # noqa: E402
from routers import cameras as C  # noqa: E402


class IsolateBlocksRealGo2rtcShutdown(unittest.TestCase):
    def test_isolate_warmup_neutralizes_stop_and_ensure(self):
        restore = isolate_warmup(); self.addCleanup(restore)
        with mock.patch.object(C, "_read_pidfile", side_effect=AssertionError("격리 중인데 PID 파일을 읽었다")), \
             mock.patch.object(C, "_terminate_pid", side_effect=AssertionError("격리 중인데 프로세스를 죽이려 했다")):
            self.assertFalse(C.stop_go2rtc())
            self.assertFalse(C.ensure_go2rtc())

    def test_without_isolation_pidfile_path_is_live(self):
        """격리가 없으면 핸들 없는 프로세스는 PID 파일의 go2rtc 를 종료한다 — 그래서 격리가 필수다(가짜 PID·가짜 종료로 재현)."""
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        pidfile = Path(tmp.name) / "go2rtc.pid"; pidfile.write_text("4242", encoding="utf-8")
        killed: list[int] = []
        with mock.patch.object(C, "_G2_PROC", None), mock.patch.object(C, "_G2_LOGF", None), mock.patch.object(C, "_G2_RELAY", None), \
             mock.patch.object(C, "_g2_pidfile", return_value=pidfile), \
             mock.patch.object(C, "_pid_alive", return_value=True), \
             mock.patch.object(C, "_terminate_pid", side_effect=lambda pid: killed.append(pid) or True):
            self.assertTrue(C.stop_go2rtc())
        self.assertEqual(killed, [4242]); self.assertFalse(pidfile.exists(), "PID 파일도 지운다 — 사고 때 비어 있던 이유")

    def test_lifespan_tests_use_isolate_warmup(self):
        for name in ("test_gpu_fallback.py", "test_token_isolation.py", "test_vision_only_pages.py"):
            src = (_ROOT / "tests" / name).read_text(encoding="utf-8")
            if "with TestClient" in src:
                self.assertIn("isolate_warmup()", src, f"{name}: lifespan 을 돌면서 go2rtc 격리가 없다 — 실제 서버의 go2rtc 를 죽인다")


if __name__ == "__main__":
    unittest.main()
