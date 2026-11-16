"""[OPEN_ISSUES #41, 2026-10-12] 로컬 영상 파일 소스는 go2rtc 에 등록하지 않는다 — RTSP·웹캠 경로는 불변.

실기 사고: 파일 경로가 go2rtc 에 그대로 올라가 허브 확대 보기가 "검은 영상 + 서버 박스" 가 됐다.
고정하는 것:
  ① 실재하는 .mp4 경로 → go2rtc PUT 호출 0회(워커 시작은 그대로).
  ② rtsp://id:pw@host/… → PUT 1회, src 쿼리에 자격증명 포함 원문 그대로(예전 동작 불변).
  ③ 웹캠 번호 "0"·없는 파일 경로·rtsps:// → 파일로 보지 않는다(기존 경로 유지).
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

from routers import cameras as cams  # noqa: E402

_RTSP = "rtsp://user:pw@192.0.2.10:554/stream1"   # 문서용 예시 주소(RFC 5737) — 실자격증명 아님


class FileSourceSkipsGo2rtc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False); self.tmp.write(b"x"); self.tmp.close()
        self.addCleanup(lambda: Path(self.tmp.name).unlink(missing_ok=True))

    def test_is_local_file_source(self):
        self.assertTrue(cams._is_local_file_source(self.tmp.name))
        for src in (_RTSP, "rtsps://h/s", "0", "", "D:/없는/파일.mp4", "http://h/x.mp4"):
            self.assertFalse(cams._is_local_file_source(src), src)

    def test_file_source_not_registered_but_rtsp_is(self):
        calls = []
        def _urlopen(req, timeout=None):
            calls.append((req.get_method(), req.full_url)); return mock.MagicMock()
        with mock.patch("urllib.request.urlopen", side_effect=_urlopen):
            with mock.patch.object(cams._reg, "source_of", return_value=self.tmp.name):
                cams._g2_register("file_cam")
            self.assertEqual(calls, [], "파일 소스가 go2rtc 에 등록됐다")
            with mock.patch.object(cams._reg, "source_of", return_value=_RTSP):
                cams._g2_register("rtsp_cam")
        self.assertEqual(len(calls), 1); method, url = calls[0]
        self.assertEqual(method, "PUT")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q.get("name"), ["rtsp_cam"]); self.assertEqual(q.get("src"), [_RTSP], "RTSP 원문이 그대로 가야 한다")

    def test_start_of_file_camera_still_starts_worker(self):
        with mock.patch.object(cams._reg, "get", return_value={"id": "f", "name": "f", "fps": 2.0}), \
             mock.patch.object(cams._reg, "source_of", return_value=self.tmp.name), \
             mock.patch.object(cams, "_guard", return_value=object()), \
             mock.patch("worker.manager.start", return_value={"ok": True}) as ms, \
             mock.patch("urllib.request.urlopen") as uo:
            r = cams._start("f")
        self.assertTrue(r["ok"]); ms.assert_called_once(); uo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
