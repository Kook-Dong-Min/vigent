"""[2026-11-16] 관제 허브 — "+ 카메라"로 추가되는 **모든** 카메라가 박스 그린 영상(/cameras/{id}/boxed.mjpg)으로 나온다.

배경(사용자 보고 "제대로 연결이 안 됐다"): 확대뷰를 boxed 로 바꾼 뒤에도
  ① 허브를 열기만 하면 확대뷰가 1.2초 간격 스냅샷이었다 — 박스 영상 연결(selectCam)이 **타일을 직접 눌러야만** 시작됐고,
     첫 카메라 자동 선택·경보 자동전환은 spotId 만 바꿨다.
  ② 왼쪽 타일은 박스 영상을 아예 쓰지 않았다(1.3초마다 정지화면, 박스 없음).

고정하는 것:
  ① /safety-hub 는 hub.tile_video 를 window.VIGENT_HUB_CFG.tile_video 로 주입한다. 값이 없거나 이상하면 확대뷰(spot_video)를 따른다.
  ② 전역 tuning.yaml 은 boxed(사용자 결정), 학원 프로파일은 snapshot(기존 타일 유지 — 현장 영향 미측정).
  ③ 타일 계획(tilePlan) — **실제 배포 파일의 함수를 꺼내 Node 로 실행**: 실시간은 앞에서부터 max 개, 나머지·프레임 멈춘 카메라는
     박스 정지영상, 오프라인은 숨김, 숨긴 탭은 아무것도 받지 않음, snapshot 모드는 예전 그대로.
  ④ 실시간 타일 수 상한이 브라우저 동시 연결 한도(사이트당 6개)를 넘기지 않는다 — 넘기면 상태 폴링(경보 배지)이 멈춘다.
  ⑤ 박스 정지영상용 첫 장 자르기(mjpegFirstJpeg)가 서버 조각 형식(boxed_view._part)과 맞는다.
  ⑥ 페이지 배선: 타일 요소 재사용(다시 그릴 때 영상 연결이 끊기지 않게) · 확대뷰 자동 연결 · 서버 끊김/프레임 멈춤 시 내리고 다시 잇기.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core"))

import tuning  # noqa: E402

_HTML = (_ROOT / "themes" / "safety" / "index_hub.html").read_text(encoding="utf-8")
_NODE = shutil.which("node")
_BROWSER_CONN_LIMIT = 6      # 브라우저가 한 사이트(HTTP/1.1)에 동시에 여는 연결 수 — Chrome·Edge·Firefox 공통 기본값


def _fn(name: str) -> str:
    """페이지에서 function <name>(...) { ... } 을 중괄호 짝으로 잘라 낸다."""
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


def _const(name: str) -> float:
    m = re.search(r"\b" + re.escape(name) + r"=(\d+(?:\.\d+)?)", _HTML)
    if not m:
        raise AssertionError(f"페이지에 상수 {name} 이 없다")
    return float(m.group(1))


def _run_node(funcs: str, body: str):
    """꺼낸 함수 + 검사 스크립트를 Node 로 실행하고 마지막 줄의 JSON 을 돌려준다."""
    with tempfile.TemporaryDirectory(prefix="hubtile_") as d:
        p = Path(d) / "t.js"
        p.write_text(funcs + "\n" + body, encoding="utf-8")
        r = subprocess.run([_NODE, str(p)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(f"node 실행 실패:\n{r.stderr[:800]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


class InjectionTest(unittest.TestCase):
    def _cfg(self, spot, tile):
        from routers.safety_core import safety_hub
        real = tuning.val

        def fake(sec, key, default, env=None):
            if (sec, key) == ("hub", "spot_video"):
                return default if spot is None else spot
            if (sec, key) == ("hub", "tile_video"):
                return default if tile is None else tile
            return real(sec, key, default, env=env)
        with mock.patch.object(tuning, "val", side_effect=fake):
            page = safety_hub()
        m = re.search(r"window\.VIGENT_HUB_CFG=(\{.*?\});</script>", page)
        self.assertIsNotNone(m, "VIGENT_HUB_CFG 주입이 없다")
        return json.loads(m.group(1))

    def test_absent_key_follows_spot_video(self):
        self.assertEqual(self._cfg("boxed", None)["tile_video"], "boxed")
        self.assertEqual(self._cfg(None, None)["tile_video"], "snapshot", "키가 둘 다 없는 프로파일은 예전 타일 그대로여야 한다")
        self.assertEqual(self._cfg("webrtc", None)["tile_video"], "snapshot")

    def test_explicit_value_wins(self):
        self.assertEqual(self._cfg("boxed", "snapshot")["tile_video"], "snapshot", "타일만 예전으로 되돌릴 수 있어야 한다")
        self.assertEqual(self._cfg("webrtc", " Boxed ")["tile_video"], "boxed")

    def test_unknown_value_follows_spot_video(self):
        self.assertEqual(self._cfg("boxed", "hls")["tile_video"], "boxed")
        self.assertEqual(self._cfg("webrtc", 1)["tile_video"], "snapshot")

    def test_spot_video_still_injected(self):
        cfg = self._cfg("boxed", "snapshot")
        self.assertEqual(cfg["spot_video"], "boxed")
        for k in ("box_extrap_cap", "box_render_tau", "spot_poll_ms"):
            self.assertIn(k, cfg)


class ConfigTest(unittest.TestCase):
    def test_global_tuning_selects_boxed_tiles(self):
        import yaml
        cfg = yaml.safe_load((_ROOT / "config" / "tuning.yaml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["hub"]["tile_video"], "boxed")

    def test_academy_profile_keeps_previous_tiles(self):
        import yaml
        p = _ROOT / "deploy" / "academy" / "tuning.academy.yaml"
        if not p.exists():
            self.skipTest("학원 프로파일 없음")
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        self.assertEqual((cfg.get("hub") or {}).get("tile_video"), "snapshot",
                         "학원 현장 프로파일의 타일이 바뀌었다 — 여러 대 기준 부하를 현장 기계에서 잰 뒤에 따로 결정해야 한다")


@unittest.skipIf(_NODE is None, "node 미설치 — JS 동작 테스트 생략")
class TilePlanTest(unittest.TestCase):
    """타일마다 무엇을 틀지 — 실제 배포 파일의 tilePlan 을 그대로 실행한다."""

    @classmethod
    def setUpClass(cls):
        cls.fn = _fn("tilePlan")

    def _plan(self, cams, st):
        return _run_node(self.fn, f"console.log(JSON.stringify(tilePlan({json.dumps(cams)}, {json.dumps(st)})));")

    @staticmethod
    def _cams(n, **over):
        cams = [{"id": f"c{i}", "online": True, "fresh": True} for i in range(1, n + 1)]
        for cid, patch in over.items():
            next(c for c in cams if c["id"] == cid).update(patch)
        return cams

    def test_every_camera_gets_boxed_video(self):
        """★요청의 본체 — 추가한 카메라가 몇 대든 전부 박스 영상(실시간 또는 박스 정지영상)이고, 예전 스냅샷은 하나도 없다."""
        for n in (1, 2, 3, 4, 9, 16):
            r = self._plan(self._cams(n), {"mode": "boxed", "hidden": False, "max": 3})
            self.assertEqual(len(r), n)
            self.assertTrue(all(v in ("live", "still") for v in r.values()), r)
            self.assertEqual(sum(v == "live" for v in r.values()), min(n, 3), r)

    def test_live_slots_go_to_first_cameras_in_grid_order(self):
        r = self._plan(self._cams(5), {"mode": "boxed", "hidden": False, "max": 3})
        self.assertEqual(r, {"c1": "live", "c2": "live", "c3": "live", "c4": "still", "c5": "still"})

    def test_offline_camera_is_hidden_and_takes_no_slot(self):
        r = self._plan(self._cams(4, c1={"online": False, "fresh": False}), {"mode": "boxed", "hidden": False, "max": 3})
        self.assertEqual(r, {"c1": "off", "c2": "live", "c3": "live", "c4": "live"})

    def test_stalled_camera_drops_to_still_and_frees_its_slot(self):
        """프레임이 멈춘 카메라는 실시간으로 두지 않는다 — <img> 는 스트림이 끝난 것을 몰라 멈춘 그림이 실시간처럼 남는다."""
        r = self._plan(self._cams(4, c2={"fresh": False}), {"mode": "boxed", "hidden": False, "max": 3})
        self.assertEqual(r, {"c1": "live", "c2": "still", "c3": "live", "c4": "live"})

    def test_hidden_tab_receives_nothing(self):
        r = self._plan(self._cams(3, c3={"online": False}), {"mode": "boxed", "hidden": True, "max": 3})
        self.assertEqual(r, {"c1": "pause", "c2": "pause", "c3": "off"})

    def test_snapshot_mode_is_the_previous_behaviour(self):
        """학원 프로파일(tile_video: snapshot) — 숨긴 탭이든 아니든 예전과 같은 스냅샷 폴링."""
        for hidden in (False, True):
            r = self._plan(self._cams(3, c2={"online": False}), {"mode": "snapshot", "hidden": hidden, "max": 3})
            self.assertEqual(r, {"c1": "snapshot", "c2": "off", "c3": "snapshot"})


@unittest.skipIf(_NODE is None, "node 미설치 — JS 동작 테스트 생략")
class FreshnessTest(unittest.TestCase):
    def test_cam_fresh(self):
        fresh_s = int(_const("FRESH_S"))
        body = f"""
        var FRESH_S={fresh_s}, SERVER_UP=true, out=[];
        out.push(camFresh({{online:true,last_frame_secs_ago:0.2}}));          // 정상
        out.push(camFresh({{online:true,last_frame_secs_ago:FRESH_S}}));      // 경계(포함)
        out.push(camFresh({{online:true,last_frame_secs_ago:FRESH_S+0.1}}));  // 멈춤
        out.push(camFresh({{online:true,last_frame_secs_ago:null}}));         // 아직 프레임 없음(워밍업)
        out.push(camFresh({{online:false,last_frame_secs_ago:0.2}}));         // 오프라인
        out.push(camFresh(null));                                             // 사라진 카메라
        SERVER_UP=false;
        out.push(camFresh({{online:true,last_frame_secs_ago:0.2}}));          // 서버에 닿지 않음
        console.log(JSON.stringify(out));
        """
        self.assertEqual(_run_node(_fn("camFresh"), body), [True, True, False, False, False, False, False])

    def test_page_drops_stream_before_server_ends_it(self):
        """페이지가 '멈췄다'고 보는 시점(FRESH_S + 폴링 주기)이 서버가 스트림을 끝내는 시점(STALL_END_S)보다 빨라야 한다.
        늦으면 서버가 먼저 끝낸 스트림을 <img> 가 모른 채 멈춘 그림으로 들고 있게 된다."""
        import boxed_view
        poll_s = float(re.search(r"setInterval\(poll,(\d+)\)", _HTML).group(1)) / 1000.0
        self.assertLess(_const("FRESH_S") + 2 * poll_s, boxed_view.STALL_END_S)


@unittest.skipIf(_NODE is None, "node 미설치 — JS 동작 테스트 생략")
class FirstFrameTest(unittest.TestCase):
    """박스 정지영상 — 서버가 실제로 내보내는 조각(boxed_view._part)에서 첫 장을 정확히 잘라 내는가."""

    def test_cuts_first_jpeg_by_content_length(self):
        import boxed_view
        # JPEG 본문 안에 빈 줄(\r\n\r\n)·경계 문자열이 들어 있어도 길이(Content-Length)로 잘라야 한다
        payload = b"\xff\xd8" + b"A\r\n\r\nB" * 40 + ("--" + boxed_view.BOUNDARY).encode() + bytes(range(256)) + b"\xff\xd9"
        part1 = boxed_view._part(payload, 1234.5)
        stream = part1 + boxed_view._part(b"\xff\xd8second\xff\xd9", 1234.6)
        head_len = part1.index(b"\r\n\r\n") + 4
        body = f"""
        var all=new Uint8Array(Buffer.from("{base64.b64encode(stream).decode()}","base64")), want="{base64.b64encode(payload).decode()}";
        var same=function(u){{ return !!u && Buffer.from(u).toString('base64')===want; }};
        var out={{full:same(mjpegFirstJpeg(all)), first:-1, firstOk:false, cuts:[]}};
        [0, 5, {head_len - 1}, {head_len}, {head_len + 10}, {head_len + len(payload) - 1}].forEach(function(n){{
          out.cuts.push(mjpegFirstJpeg(all.subarray(0,n))===null); }});
        for(var n=0;n<=all.length;n++){{ var r=mjpegFirstJpeg(all.subarray(0,n)); if(r){{ out.first=n; out.firstOk=same(r); break; }} }}
        out.noLength=mjpegFirstJpeg(new Uint8Array(Buffer.from("--x\\r\\nContent-Type: image/jpeg\\r\\n\\r\\nabcdef")))===null;
        console.log(JSON.stringify(out));
        """
        r = _run_node(_fn("mjpegFirstJpeg"), body)
        self.assertTrue(r["full"], "첫 장이 서버가 보낸 JPEG 와 다르다")
        self.assertEqual(r["cuts"], [True] * 6, "덜 받은 상태에서 값을 돌려줬다(깨진 그림)")
        self.assertEqual(r["first"], head_len + len(payload), "첫 장이 다 오기 전·후가 아닌 시점에 값을 돌려줬다")
        self.assertTrue(r["firstOk"])
        self.assertTrue(r["noLength"], "길이 헤더가 없는 조각은 자를 수 없다 → null(스냅샷으로 폴백)")


class PageWiringTest(unittest.TestCase):
    def test_tile_mode_comes_from_injected_config_and_follows_spot_by_default(self):
        self.assertIn("TILE_VIDEO=String((window.VIGENT_HUB_CFG||{}).tile_video||(SPOT_VIDEO==='boxed'?'boxed':'snapshot'))", _HTML)
        self.assertLess(_HTML.index("var SPOT_VIDEO="), _HTML.index("var TILE_VIDEO="), "TILE_VIDEO 가 SPOT_VIDEO 보다 먼저 계산된다(undefined)")

    def test_live_tiles_leave_connections_for_status_polling(self):
        """★실시간 타일 + 확대뷰 1 이 브라우저 동시 연결 한도를 다 쓰면 /cameras·/health 폴링이 멈춰 경보 배지가 얼어붙는다."""
        live = int(_const("TILE_LIVE_MAX"))
        self.assertGreaterEqual(live, 1)
        self.assertLessEqual(live + 1, _BROWSER_CONN_LIMIT - 2, "영상이 연결을 너무 많이 쓴다 — 폴링 몫으로 2개는 남길 것")
        self.assertIn("max:TILE_LIVE_MAX", _fn("tileSync"))

    def test_grid_reuses_tile_elements(self):
        """다시 그릴 때마다 <img> 를 새로 만들면 박스 영상 연결이 2초마다 끊긴다."""
        f = _fn("renderGrid")
        self.assertNotIn("g.innerHTML=", f, "그리드를 통째로 다시 만든다 — 타일 영상이 매번 끊긴다")
        self.assertIn("tileEls[c.id]", f)
        self.assertIn("tileSync()", f)
        self.assertIn("tileUnlive(tileEls[id].querySelector('img'), true)", f, "사라진 카메라의 스트림 연결을 끊지 않는다")
        self.assertNotIn("refreshThumbs", _HTML, "옛 스냅샷 전용 갱신 함수가 남아 있다")

    def test_live_tile_uses_boxed_stream_and_is_not_restarted(self):
        f = _fn("tileLive")
        self.assertIn("'/boxed.mjpg?t='", f)
        self.assertIn("encodeURIComponent(id)", f)
        self.assertLess(f.index("if(img.dataset.stream) return;"), f.index("img.src="), "재생 중인 타일을 다시 연결한다(영상 끊김)")
        self.assertIn("tileStill(img,id)", f, "실시간 연결이 실패하면 박스 정지영상으로 내려가야 한다(타일이 비지 않게)")

    def test_still_tile_takes_first_frame_and_releases_connection(self):
        f = _fn("tileStill")
        self.assertIn("'/boxed.mjpg?t='", f)
        self.assertIn("mjpegFirstJpeg(acc)", f)
        self.assertIn("ac.abort()", f, "첫 장을 받은 뒤 연결을 닫지 않으면 정지 타일도 연결을 붙잡는다")
        self.assertIn("tileSnap(img,id)", f, "박스 정지영상이 실패하면 예전 스냅샷으로 넘어가야 한다")
        self.assertIn("if(img.dataset.stream)", f, "그새 실시간으로 올라간 타일을 정지영상이 덮으면 안 된다")

    def test_tile_sync_follows_plan(self):
        f = _fn("tileSync")
        self.assertIn("tilePlan(", f)
        self.assertIn("hidden:document.hidden", f)
        self.assertIn("fresh:camFresh(c)", f)
        for mode in ("'live'", "'off'", "'still'", "'snapshot'"):
            self.assertIn(mode, f)

    def test_hidden_tab_and_interval_resync_tiles(self):
        self.assertRegex(_HTML, r"document\.addEventListener\('visibilitychange',function\(\)\{ tileSync\(\); \}\)")
        self.assertIn("setInterval(tileSync,1300)", _HTML)

    def test_spotlight_connects_without_a_click(self):
        """★사용자 보고의 원인 — 첫 카메라 자동 선택·경보 자동전환이 영상 연결(selectCam)을 거치지 않았다."""
        f = _fn("poll")
        self.assertIn("if(SPOT_VIDEO==='boxed' && spotId && spotId!==spotShown && !document.hidden && !zoneEdit) selectCam(spotId,false);", f)
        self.assertIn("else spotSync();", f)
        self.assertLess(f.index("updatePulseSpot()"), f.index("selectCam(spotId,false)"), "대상을 정한 뒤에 연결해야 한다")
        s = _fn("selectCam")
        self.assertIn("spotShown=id;", s)
        self.assertIn("spotStale=false", s)

    def test_auto_connect_never_ends_zone_edit(self):
        """★[W3] selectCam 은 편집을 끝내며 찍어둔 점을 버린다 — 자동 연결(폴링·탭 복귀)이 편집 중에 selectCam 을 부르면
        사용자가 찍던 위험구역이 말없이 사라진다. 자동 경로의 selectCam 호출은 전부 !zoneEdit 조건 뒤에 있어야 한다."""
        calls = [m.start() for m in re.finditer(r"selectCam\(spotId,false\)", _HTML)]
        self.assertEqual(len(calls), 2, "자동 연결 호출부가 늘거나 줄었다 — 새 호출부에도 편집 가드가 있는지 확인할 것")
        for i in calls:
            line = _HTML[_HTML.rfind("\n", 0, i) + 1:i]
            self.assertIn("!zoneEdit", line, "편집 가드 없는 자동 selectCam: " + line.strip()[:120])
        self.assertNotIn("endZoneEdit", _fn("spotSync"), "프레임 끊김·복구 처리가 편집 상태를 건드린다")
        self.assertNotIn("selectCam(", _fn("spotSync"))

    def test_webrtc_profile_does_not_auto_connect(self):
        """학원(webrtc)은 예전 그대로 — 자동 연결은 박스 영상 모드에서만."""
        f = _fn("poll")
        self.assertNotIn("spotWebRTC(", f)
        self.assertIn("if(SPOT_VIDEO!=='boxed'||document.hidden) return;", _fn("spotSync"))

    def test_spotlight_drops_dead_stream_and_reconnects(self):
        f = _fn("spotSync")
        self.assertLess(f.index("if(!camFresh(c))"), f.index("stopBoxed()"))
        self.assertIn("if(spotStale){ spotStale=false; if(spotMode!=='webrtc') spotBoxed(spotId); }", f)

    def test_server_unreachable_drops_streams(self):
        f = _fn("poll")
        self.assertIn("SERVER_UP=true", f)
        self.assertIn(".catch(function(){ SERVER_UP=false; tileSync(); spotSync(); })", f)


if __name__ == "__main__":
    unittest.main()
