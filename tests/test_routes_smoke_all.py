"""[정리 0, 2026-10-12] 등록된 라우트가 삭제된 모듈·없는 엔진 키를 참조해 500 을 내지 않는다.

배경: ⑤-2 에서 Copilot 을 지운 뒤 /evidence/search 가 `bundle["agents"].get("Copilot")` → None.cite() 로 500 이었다
(호출처가 없어 아무도 몰랐다). 같은 종류를 두 겹으로 잡는다:
  ① 정적: vigent-core 안의 `bundle["agents"].get("<키>")` 가 전부 build_agents 가 실제로 만드는 이름(Guard·Dispatcher)이다.
  ② 동적: app 의 GET 라우트 전수를 TestClient 로 호출해 **500 이 하나도 없다**(200·3xx·4xx·502·503 은 허용 — 인증·없는 id·
     외부 go2rtc 부재는 정상 응답). 경로 매개변수는 존재하지 않는 값으로 채워 404 를 기대한다.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

from _isolate import isolate_alerts, isolate_no_token, isolate_shutdown, isolate_warmup  # noqa: E402

_KEY = re.compile(r'\[\"agents\"\]\.get\(\"([A-Za-z_]+)\"\)')


class AgentKeysExist(unittest.TestCase):
    def test_every_lookup_names_a_built_engine(self):
        import engine
        built = {cls.name for cls in engine.AGENT_CLASSES}
        bad = []
        for f in sorted((_ROOT / "vigent-core").rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            for key in _KEY.findall(f.read_text(encoding="utf-8", errors="replace")):
                if key not in built:
                    bad.append((f.relative_to(_ROOT).as_posix(), key))
        self.assertEqual(bad, [], f"없는 엔진 키를 꺼내는 곳: {bad} (만드는 것: {sorted(built)})")


class EveryGetRouteNo500(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        from fastapi.testclient import TestClient
        cls.addClassCleanup(isolate_alerts()); cls.addClassCleanup(isolate_shutdown())
        cls.addClassCleanup(isolate_no_token()); cls.addClassCleanup(isolate_warmup())
        cls.main = main
        cls.client = TestClient(main.app); cls.client.__enter__()
        cls.addClassCleanup(lambda: cls.client.__exit__(None, None, None))

    def test_all_get_routes_respond_without_500(self):
        # include_router 분은 app.routes 얕은 열거에 안 보인다(scripts/check_openapi_diff.py 와 같은 이유) → openapi 로 전수
        paths = [p for p, item in self.main.app.openapi()["paths"].items() if "get" in item]
        failures, checked = [], 0
        for raw in paths:
            path = re.sub(r"\{[^}]+\}", "no-such-id", raw)
            resp = self.client.get(path, follow_redirects=False)
            checked += 1
            if resp.status_code == 500:
                failures.append((raw, resp.text[:120]))
        self.assertGreaterEqual(checked, 20, "GET 라우트를 거의 못 찾았다")
        self.assertEqual(failures, [], f"500 을 내는 GET 라우트: {failures}")

    def test_dead_evidence_search_is_gone(self):
        self.assertEqual(self.client.get("/evidence/search?rule=ppe_missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
