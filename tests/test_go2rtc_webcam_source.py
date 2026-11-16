"""tests/test_go2rtc_webcam_source.py — 웹캠 번호 소스의 go2rtc WebRTC 재생 (2026-10-12).

배경: 허브 확대뷰는 go2rtc WebRTC(실시간) → 실패 시 스냅샷 폴링(1.2초)으로 폴백한다. 웹캠 번호 "0" 은
go2rtc 에 그대로 올라가 스킴이 없어 항상 실패했고, 그래서 웹캠은 늘 1.2초 주기 스냅샷으로만 보였다.
고침: _g2_register 가 웹캠 번호를 go2rtc 의 ffmpeg device 소스(dshow, H264 트랜스코딩)로 바꿔 등록하고,
ensure_go2rtc 가 저장소 bin/ffmpeg.exe 경로를 GO2RTC_FFMPEG_BIN 으로 템플릿(ffmpeg.bin)에 주입한다.
고정하는 것:
  ① "0" → ffmpeg:device?video=0&…#video=h264 / RTSP·파일·기타는 원문 불변
  ② 웹캠 카메라 등록 시 go2rtc PUT 의 src 가 변환된 값
  ③ bin/ffmpeg.exe 가 있으면 그 경로, 없으면 "ffmpeg"(PATH)
  ④ ensure_go2rtc 가 Popen env 에 GO2RTC_FFMPEG_BIN 을 넣고, 템플릿이 ${GO2RTC_FFMPEG_BIN} 을 참조한다
  ⑤ _start: 웹캠은 go2rtc 등록(PUT 2xx) → 워커 입력 rtsp://127.0.0.1:8554/<cid> / 등록 실패 → 원문 "0"(cv2 직접) /
     RTSP 소스는 예전처럼 워커 먼저·입력 원문 그대로. (cv2 와 ffmpeg 는 같은 웹캠을 동시에 못 연다 — 실측 10-12)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

from routers import cameras as C  # noqa: E402

_RTSP = "rtsp://user:pw@192.0.2.10:554/stream1"   # RFC 5737 문서용 주소


class WebcamSourceMapping(unittest.TestCase):
    def test_digit_becomes_ffmpeg_device_h264(self):
        out = C._g2_source_for("0")
        self.assertTrue(out.startswith("ffmpeg:device?video=0&"), out)
        self.assertIn("#video=h264", out, "WebRTC 는 H264 가 필요하다 — 트랜스코딩 지시가 빠졌다")
        self.assertIn("video_size=", out); self.assertIn("framerate=", out)
        self.assertTrue(C._g2_source_for(" 1 ").startswith("ffmpeg:device?video=1&"))

    def test_other_sources_unchanged(self):
        for src in (_RTSP, "rtsps://h/s", "C:/x/y.mp4", "ffmpeg:device?video=0", ""):
            self.assertEqual(C._g2_source_for(src), src, src)

    def test_register_sends_converted_src(self):
        calls = []
        def _urlopen(req, timeout=None):
            calls.append((req.get_method(), req.full_url)); return mock.MagicMock()
        with mock.patch("urllib.request.urlopen", side_effect=_urlopen), \
             mock.patch.object(C._reg, "source_of", return_value="0"):
            C._g2_register("webcam")
        self.assertEqual(len(calls), 1); method, url = calls[0]
        self.assertEqual(method, "PUT")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q.get("name"), ["webcam"])
        self.assertEqual(q.get("src"), [C._g2_source_for("0")])


class FfmpegBinInjection(unittest.TestCase):
    def test_ffmpeg_bin_prefers_repo_bin(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "bin").mkdir()
            self.assertEqual(C._ffmpeg_bin(root), "ffmpeg", "bin/ 에 없으면 PATH 의 ffmpeg 를 쓴다")
            (root / "bin" / "ffmpeg.exe").write_bytes(b"")
            self.assertEqual(C._ffmpeg_bin(root), str(root / "bin" / "ffmpeg.exe"))

    def test_template_references_env_and_ensure_injects_it(self):
        tpl = (_ROOT / "config" / "go2rtc.yaml").read_text(encoding="utf-8")
        self.assertRegex(tpl, r"(?m)^ffmpeg:\s*$"); self.assertIn("bin: ${GO2RTC_FFMPEG_BIN}", tpl)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)   # ★가장 먼저 등록 → LIFO 로 가장 나중에 지움(로그 핸들 닫힌 뒤)
        root = Path(tmp.name); (root / "bin").mkdir(); (root / "config").mkdir()
        (root / "bin" / "go2rtc.exe").write_bytes(b""); (root / "bin" / "ffmpeg.exe").write_bytes(b"")
        (root / "config" / "go2rtc.yaml").write_text(tpl, encoding="utf-8")
        envs = []
        def _popen(*a, **k):
            envs.append(k.get("env") or {}); p = mock.MagicMock(); p.pid = 4321; p.stdout = None; p.poll.return_value = 0; return p
        ps = [mock.patch.object(C, "_G2_ROOT", root), mock.patch.object(C, "_G2_PROC", None), mock.patch.object(C, "_G2_LOGF", None),
              mock.patch("subprocess.Popen", side_effect=_popen), mock.patch.object(C, "_g2_port_busy", return_value=False),
              mock.patch.object(C, "_lan_ip", return_value="127.0.0.1")]
        for p in ps: p.start(); self.addCleanup(p.stop)
        self.addCleanup(C.stop_go2rtc)          # patch 복원 전·임시폴더 삭제 전에 로그 핸들·PID 파일 정리
        self.assertTrue(C.ensure_go2rtc())
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0].get("GO2RTC_FFMPEG_BIN"), str(root / "bin" / "ffmpeg.exe"))
        self.assertIn("ffmpeg:", (root / "data" / "go2rtc.runtime.yaml").read_text(encoding="utf-8"))


class WorkerReadsGo2rtcRtsp(unittest.TestCase):
    def _run_start(self, source: str, put_ok: bool):
        order: list[str] = []
        def _urlopen(req, timeout=None):
            order.append("PUT")
            if not put_ok:
                raise OSError("go2rtc 미기동")
            r = mock.MagicMock(); r.status = 200; r.__enter__.return_value = r; return r
        def _wstart(guard, lock, cid, src, **kw):
            order.append("START"); return {"ok": True, "src": src}
        with mock.patch.object(C._reg, "get", return_value={"id": "w", "name": "w", "fps": 2.0}), \
             mock.patch.object(C._reg, "source_of", return_value=source), \
             mock.patch.object(C, "_guard", return_value=object()), \
             mock.patch("worker.manager.start", side_effect=_wstart), \
             mock.patch("urllib.request.urlopen", side_effect=_urlopen):
            res = C._start("w")
        return res, order

    def test_webcam_registers_first_then_reads_go2rtc_rtsp(self):
        res, order = self._run_start("0", put_ok=True)
        self.assertEqual(order, ["PUT", "START"], "웹캠은 go2rtc 등록이 워커보다 먼저여야 한다(장치 독점)")
        self.assertEqual(res["src"], "rtsp://127.0.0.1:8554/w")

    def test_webcam_falls_back_to_direct_when_go2rtc_down(self):
        res, order = self._run_start("0", put_ok=False)
        self.assertEqual(order, ["PUT", "START"])
        self.assertEqual(res["src"], "0", "go2rtc 가 없으면 예전처럼 cv2 가 직접 연다(규칙6 폴백)")

    def test_rtsp_source_unchanged_order_and_src(self):
        res, order = self._run_start(_RTSP, put_ok=True)
        self.assertEqual(order, ["START", "PUT"], "RTSP 카메라는 예전 순서(워커 → go2rtc) 그대로")
        self.assertEqual(res["src"], _RTSP)

    def test_rtsp_url_helper(self):
        import go2rtc_client
        self.assertEqual(go2rtc_client.rtsp_url("cam 1"), f"rtsp://{go2rtc_client.HOST}:{go2rtc_client.RTSP_PORT}/cam%201")


if __name__ == "__main__":
    unittest.main()
