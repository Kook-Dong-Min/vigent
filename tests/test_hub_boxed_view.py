"""[2026-11-16] 관제 허브 확대뷰 ↔ 박스 그린 영상(/cameras/{id}/boxed.mjpg) 연결.

고정하는 것:
  ① /safety-hub 는 tuning hub.spot_video 를 window.VIGENT_HUB_CFG.spot_video 로 주입한다. 값이 없거나 이상하면 "webrtc"
     (= 예전 화면 그대로 — 이 줄이 없는 현장 프로파일은 영향이 없다).
  ② 전역 tuning.yaml 은 boxed(사용자 결정), 학원 프로파일은 이 키가 없다(= webrtc).
  ③ 페이지: 박스 영상 모드에서는 캔버스가 박스를 다시 그리지 않는다(겹치면 박스가 둘) · 스냅샷 폴링이 영상 src 를 덮지 않는다 ·
     실패하면 예전 체인(spotWebRTC)으로 넘어간다 · 숨긴 탭은 스트림을 끊는다 · 카메라를 바꾸면 이전 스트림을 끊는다.
  ④ 페이지가 부르는 주소가 실제 라우트에 있다.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import tuning  # noqa: E402
from _isolate import isolate_alerts, isolate_no_token, isolate_shutdown, isolate_warmup  # noqa: E402

_HTML = (_ROOT / "themes" / "safety" / "index_hub.html").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """페이지에서 function <name>(...) { ... } 본문을 중괄호 짝으로 잘라 낸다."""
    i = _HTML.index("function " + name + "(")
    j = _HTML.index("{", i)
    depth, k = 0, j
    while True:
        ch = _HTML[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return _HTML[i:k + 1]
        k += 1


class InjectionTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts()); self.addCleanup(isolate_shutdown())
        self.addCleanup(isolate_no_token()); self.addCleanup(isolate_warmup())

    def _cfg(self, value):
        import main
        from fastapi.testclient import TestClient
        real = tuning.val

        def fake(sec, key, default, env=None):
            if (sec, key) == ("hub", "spot_video"):
                return default if value is None else value
            return real(sec, key, default, env=env)
        with mock.patch.object(tuning, "val", side_effect=fake), TestClient(main.app) as c:
            r = c.get("/safety-hub")
        self.assertEqual(r.status_code, 200)
        m = re.search(r"window\.VIGENT_HUB_CFG=(\{.*?\});</script>", r.text)
        self.assertIsNotNone(m, "VIGENT_HUB_CFG 주입이 없다")
        return json.loads(m.group(1))

    def test_default_is_webrtc_when_key_absent(self):
        self.assertEqual(self._cfg(None)["spot_video"], "webrtc")

    def test_boxed_is_injected(self):
        self.assertEqual(self._cfg("boxed")["spot_video"], "boxed")
        self.assertEqual(self._cfg(" Boxed ")["spot_video"], "boxed")

    def test_unknown_value_falls_back_to_webrtc(self):
        self.assertEqual(self._cfg("hls")["spot_video"], "webrtc")
        self.assertEqual(self._cfg(1)["spot_video"], "webrtc")

    def test_other_keys_still_injected(self):
        cfg = self._cfg("boxed")
        for k in ("box_extrap_cap", "box_render_tau", "spot_poll_ms"):
            self.assertIn(k, cfg)


class ConfigTest(unittest.TestCase):
    def test_global_tuning_selects_boxed(self):
        import yaml
        cfg = yaml.safe_load((_ROOT / "config" / "tuning.yaml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["hub"]["spot_video"], "boxed")

    def test_academy_profile_keeps_previous_view(self):
        import yaml
        p = _ROOT / "deploy" / "academy" / "tuning.academy.yaml"
        if not p.exists():
            self.skipTest("학원 프로파일 없음")
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        self.assertNotEqual((cfg.get("hub") or {}).get("spot_video"), "boxed",
                            "학원 현장 프로파일까지 박스 영상으로 바뀌었다 — 현장 영향은 따로 결정해야 한다")


class PageWiringTest(unittest.TestCase):
    def test_mode_comes_from_injected_config_with_webrtc_default(self):
        self.assertRegex(_HTML, r"SPOT_VIDEO=String\(\(window\.VIGENT_HUB_CFG\|\|\{\}\)\.spot_video\|\|'webrtc'\)")

    def test_select_cam_stops_previous_stream_and_picks_mode(self):
        f = _fn("selectCam")
        self.assertIn("stopBoxed()", f, "카메라를 바꿀 때 이전 박스 영상 스트림을 끊지 않는다")
        self.assertIn("SPOT_VIDEO==='boxed'", f)
        self.assertIn("spotBoxed(id)", f); self.assertIn("spotWebRTC(id)", f)

    def test_boxed_stream_url_and_fallback_chain(self):
        f = _fn("spotBoxed")
        self.assertIn("'/boxed.mjpg?t='", f)
        self.assertIn("encodeURIComponent(cid)", f)
        self.assertIn("spotWebRTC(cid)", f, "박스 영상이 끝내 실패하면 예전 체인으로 넘어가야 한다(화면이 죽지 않게)")
        self.assertIn("gen!==spotGen", f, "옛 카메라의 늦은 콜백이 새 화면을 건드리면 안 된다")
        self.assertIn("spotFocus(true)", f)

    def test_stop_boxed_releases_stream_and_focus(self):
        f = _fn("stopBoxed")
        self.assertIn("removeAttribute('src')", f, "src 를 지워야 브라우저가 스트림 연결을 닫는다")
        self.assertIn("spotFocus(false)", f)
        self.assertIn("clearTimeout(boxedTimer)", f)

    def test_canvas_does_not_redraw_boxes_in_boxed_mode(self):
        f = _fn("drawSpot")
        self.assertIn("if(!zoneEdit && spotMode!=='boxed')", f, "박스 영상 위에 브라우저가 박스를 또 그리면 박스가 둘이 된다")
        self.assertLess(f.index("if(showZone&&zone.length)"), f.index("spotMode!=='boxed'"), "위험구역은 모드와 무관하게 그려야 한다")

    def test_snapshot_polling_does_not_clobber_stream(self):
        f = _fn("spotTick")
        self.assertIn("boxedActive()", f)
        self.assertLess(f.index("boxedActive()"), f.index("/snapshot?t="), "박스 영상 중에 스냅샷이 src 를 덮어쓴다")

    def test_hidden_tab_drops_stream(self):
        i = _HTML.index("document.addEventListener('visibilitychange'")
        block = _HTML[i:i + 700]
        self.assertIn("stopBoxed()", block); self.assertIn("spotBoxed(spotId)", block)

    def test_badge_names_the_mode(self):
        self.assertIn("boxed:['#39d0ff','박스 영상']", _HTML)
        self.assertIn("return {mode:'boxed', lat:null}", _fn("pmodeState"))

    def test_route_used_by_page_exists(self):
        base = json.loads((_ROOT / "baseline_openapi.json").read_text(encoding="utf-8"))
        self.assertIn("/cameras/{cid}/boxed.mjpg", base["paths"])


if __name__ == "__main__":
    unittest.main()
