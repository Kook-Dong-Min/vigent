"""[#39, 2026-10-12] 비전 전용으로 남는 화면이 부르는 주소·링크가 전부 서버에 등록돼 있는지 — 정적 검사 + 이벤트 목록 동작.

실기 사고(2026-10-12 03:0x, VIGENT_AGENTS=0): /safety-hub 가 2 s 마다 /safety/auto/feed(에이전트 라우트)를 불러 404 → 경보가 나도
"이벤트 없음". 이 파일이 고정하는 것:
  ① 남는 화면 5개(+그 화면이 싣는 /static/*.js)의 fetch/api/href/action/location.href 주소가 기준선
     (baseline_openapi.json, ⑤-5 부터 하나)·정적 접두·WS 경로에 전부 있다. **⑤ 에서 지울 것으로 이미 확인된 항목**(KNOWN_UNTIL_STEP5)은
     정확히 그 집합만 허용 — 새 누락이 생기거나, 정리했는데 목록을 안 지우면 둘 다 실패한다.
  ② 허브는 더 이상 /safety/auto/feed 를 부르지 않는다.
  ③ /recognition/log?hours=24&limit=60 이 허브 renderEvents 가 쓰는 필드(ts·time·rule·level·site)를 돌려준다 — 켜짐/꺼짐 무관(비전 라우트).
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

from _isolate import isolate_alerts, isolate_data_dirs, isolate_no_token, isolate_shutdown, isolate_warmup  # noqa: E402

PAGES: dict[str, list[Path]] = {
    "/safety-hub": [_ROOT / "themes/safety/index_hub.html"],
    "/safety-pro": [_ROOT / "themes/safety/index_rfdetr.html"],
    "/safety/setup": [_ROOT / "vigent-core/setup_console.py"],
    "/dashboard": [_ROOT / "vigent-core/dashboard.py"],
    "/login": [_ROOT / "vigent-core/main.py"],       # 로그인 HTML 은 main.py 안의 문자열(아래서 login 구간만 자른다)
}
STATIC_PREFIXES = ("/static/", "/evidence/")
WS_PATHS = {"/tapo/ws"}
# ⑤ 에서 함께 지울 것으로 VISION_ONLY_CHECK_20261012 §1 에 기록된 누락(정리하면 여기서도 지운다)
KNOWN_UNTIL_STEP5: set[tuple[str, str]] = set()   # [⑤-1, 2026-10-12] 대시보드 링크 4·VLM 호출 1 정리 완료 — 이제 누락 0 이어야 한다
_LIT = re.compile(r"""(['"`])(/[A-Za-z0-9_./{}$()\-]*(?:\?[^'"`]*)?)\1""")


def _page_text(page: str, f: Path) -> str:
    s = f.read_text(encoding="utf-8", errors="replace")
    if page == "/login" and f.name == "main.py":
        a = s.find("def login_page"); b = s.find("def login_submit")
        pre = s.rfind("<!doctype", 0, a) if a > 0 else -1
        return s[pre if pre >= 0 else 0:b if b > 0 else len(s)]
    return s


def _literals(text: str) -> list[tuple[str, bool]]:
    """(주소, 이어붙임 여부). 이어붙임('+' 뒤)은 조각일 수 있어 '맞는 것만' 본다."""
    out = []
    for m in _LIT.finditer(text):
        before = text[:m.start()].rstrip()
        cont = before.endswith("+")
        out.append((m.group(2).split("?")[0], cont))
    return out


def _vision_paths() -> list[str]:
    return list(json.loads((_ROOT / "baseline_openapi.json").read_text(encoding="utf-8"))["paths"].keys())   # [⑤-5] 기준선 하나


def _registered(url: str, paths: list[str]) -> bool:
    if url in WS_PATHS or url.startswith(STATIC_PREFIXES):
        return True
    if url.endswith("/"):                                   # '/cameras/' 같은 접두 조각
        return any(p.startswith(url) for p in paths)
    for p in paths:
        if re.fullmatch("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$", url):
            return True
    return False


def _files_for(page: str) -> list[Path]:
    files = list(PAGES[page])
    for f in list(files):
        for js in re.findall(r'src="(/static/[^"?]+\.js)', f.read_text(encoding="utf-8", errors="replace")):
            p = _ROOT / "vigent-core" / js.lstrip("/")
            if p.exists():
                files.append(p)
    return files


class RemainingPagesOnlyCallRegisteredRoutes(unittest.TestCase):
    def test_every_url_and_link_is_registered_except_known_step5_items(self):
        paths = _vision_paths()
        missing: set[tuple[str, str]] = set()
        seen = 0
        for page in PAGES:
            for f in _files_for(page):
                for url, cont in _literals(_page_text(page, f)):
                    if url.startswith("/{") and page == "/dashboard":    # f-string 테마 링크 '/{e(theme)}' → '/safety'
                        url = "/safety"
                    if "$" in url or "(" in url:
                        continue                                        # 템플릿·식 조각은 판정 불가
                    ok = _registered(url, paths)
                    if cont and not ok:
                        continue                                        # 이어붙임 조각('/snapshot?t=' 등)은 세지 않는다
                    seen += 1
                    if not ok:
                        missing.add((page, url))
        self.assertGreater(seen, 20, "주소를 거의 못 뽑았다 — 추출 정규식 점검")
        self.assertEqual(missing, KNOWN_UNTIL_STEP5,
                         f"새 누락: {sorted(missing - KNOWN_UNTIL_STEP5)} / 정리됐는데 목록에 남음: {sorted(KNOWN_UNTIL_STEP5 - missing)}")

    def test_hub_no_longer_calls_agent_feed(self):
        s = (_ROOT / "themes/safety/index_hub.html").read_text(encoding="utf-8")
        self.assertNotIn("api('GET','/safety/auto/feed", s)
        self.assertIn("/recognition/log?hours=24&limit=60", s)


class RecognitionLogServesHubEvents(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts()); self.addCleanup(isolate_shutdown()); self.addCleanup(isolate_no_token())
        self.addCleanup(isolate_data_dirs()); self.addCleanup(isolate_warmup())   # [2026-10-12] 예열·go2rtc 미기동(뒤 타이밍 테스트 보호) — tests/_isolate.isolate_warmup 참조

    def test_fields_hub_needs_present_in_both_switch_states(self):
        import data_engine
        import main
        from fastapi.testclient import TestClient
        data_engine.log_event(rule="crowd_density", level="mid", site="시험", note="인원 밀집 — 5명 감지")
        with TestClient(main.app) as c:
            r = c.get("/recognition/log?hours=24&limit=60")
        self.assertEqual(r.status_code, 200, r.text[:200])
        ev = r.json().get("events") or []
        self.assertTrue(ev, "방금 기록한 이벤트가 목록에 없다")
        for k in ("ts", "time", "rule", "level", "site"):
            self.assertIn(k, ev[0], k)
        self.assertEqual((ev[0]["rule"], ev[0]["level"], ev[0]["site"]), ("crowd_density", "mid", "시험"))


if __name__ == "__main__":
    unittest.main()
