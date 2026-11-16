"""[2026-10-11] 테스트 격리 결함 22건 — 토큰 있는 개발기에서만 깨지던 것의 회귀 방지.

★무엇을 고정하는가
  ① `_isolate.isolate_no_token()` 은 main 의 `_API_TOKEN` 상수와 환경변수를 **둘 다** 비우고, 복원 함수가 둘 다 되돌린다.
     (환경변수만 지우면 import 시점에 읽힌 상수가 남아 /health 가 401 — 개발기 .env 토큰 모드에서 22건이 깨진 원인)
  ② `TestClient(main.app)` 로 무인증 요청을 보내는 테스트 파일은 전부 토큰을 다룬다(`isolate_no_token` 또는 `main._API_TOKEN` 직접 처리).
     새 테스트가 Mac(토큰 없음)에서만 통과하고 Windows 개발기에서 깨지는 일이 재발하지 않게 정적 검사.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

from _isolate import isolate_no_token, isolate_shutdown, isolate_warmup  # noqa: E402


class NoTokenHelper(unittest.TestCase):
    def test_clears_constant_and_env_then_restores(self):
        import main
        saved_const, saved_env = main._API_TOKEN, os.environ.get("VIGENT_API_TOKEN")
        try:
            main._API_TOKEN = "const-xyz"; os.environ["VIGENT_API_TOKEN"] = "env-xyz"
            restore = isolate_no_token()
            self.assertEqual(main._API_TOKEN, ""); self.assertIsNone(os.environ.get("VIGENT_API_TOKEN"))
            restore()
            self.assertEqual(main._API_TOKEN, "const-xyz"); self.assertEqual(os.environ.get("VIGENT_API_TOKEN"), "env-xyz")
            # env 가 원래 없던 경우에도 복원 뒤 남기지 않는다
            os.environ.pop("VIGENT_API_TOKEN", None)
            restore2 = isolate_no_token(); restore2()
            self.assertIsNone(os.environ.get("VIGENT_API_TOKEN"))
        finally:
            main._API_TOKEN = saved_const
            if saved_env is None:
                os.environ.pop("VIGENT_API_TOKEN", None)
            else:
                os.environ["VIGENT_API_TOKEN"] = saved_env

    def test_health_open_under_helper_even_if_token_mode(self):
        """토큰 모드(상수 설정) 상태에서도 헬퍼를 쓰면 /health 가 401 이 아니다 — 22건이 깨진 바로 그 경로."""
        import main
        from fastapi.testclient import TestClient
        saved = main._API_TOKEN
        try:
            main._API_TOKEN = "secret-xyz"
            self.addCleanup(isolate_shutdown()); self.addCleanup(isolate_warmup())   # [A-7] lifespan 종료(진짜 _shutdown)가 세운 '종료 중' 깃발을 원복(뒤 테스트의 start 거부 방지)
            restore = isolate_no_token(); self.addCleanup(restore)
            with TestClient(main.app) as c:
                self.assertNotEqual(c.get("/health").status_code, 401)
        finally:
            restore(); main._API_TOKEN = saved


class EveryClientTestHandlesToken(unittest.TestCase):
    def test_static_scan(self):
        bad = []
        for f in sorted((_ROOT / "tests").glob("test_*.py")):
            s = f.read_text(encoding="utf-8", errors="replace")
            if "TestClient(main.app)" not in s:
                continue
            if "isolate_no_token" in s or "_API_TOKEN" in s or "Authorization" in s:
                continue
            bad.append(f.name)
        self.assertEqual(bad, [], "무인증 TestClient 테스트가 토큰을 다루지 않는다 — isolate_no_token() 을 setUp 에 넣을 것")


if __name__ == "__main__":
    unittest.main()
