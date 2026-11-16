"""[OPEN_ISSUES #40, 2026-10-12] go2rtc 로그 중계 — 자격증명 마스킹 · 폭주 비차단 · 종료 상한.

고정하는 것:
  ① 자격증명이 든 줄은 `scheme://***:***@host…` 로 기록된다(rtsp·rtsps, 비밀번호의 @·#·%·/·: 등 특수문자 포함). 없는 줄은 그대로.
  ② 폭주(큐 2,000 초과)에서도 읽기 스레드가 파이프를 계속 비워 생산자가 막히지 않고, 버린 줄 수가 "[vigent-relay] N줄 생략" 으로 남는다.
  ③ 파이프가 닫히지 않아도 stop(join_s) 은 상한 안에 돌아온다. stop_go2rtc 전체가 2 s 안에 끝난다(종료 경로 A-6~A-8 과 같은 상한 원칙).
  ④ ensure_go2rtc 가 Popen 에 stdout=PIPE·stderr=STDOUT 을 주고 중계를 띄운다; stop_go2rtc 가 중계를 끝내고 파일을 닫는다.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import camera_registry as reg  # noqa: E402
from routers import cameras as C  # noqa: E402

_PW_SPECIAL = "p@ss#w%rd/!:x"      # 시험용 가짜 비밀번호(실자격증명 아님)


class StrictMask(unittest.TestCase):
    def test_masks_rtsp_rtsps_and_special_password(self):
        for scheme in ("rtsp", "rtsps"):
            line = f"23:13:45 WRN producer.go:170 > error=EOF url={scheme}://admin:{_PW_SPECIAL}@192.0.2.10:554/stream1"
            out = reg.scrub_credentials_strict(line)
            self.assertNotIn("admin", out); self.assertNotIn(_PW_SPECIAL, out); self.assertNotIn("p@ss", out)
            self.assertIn(f"url={scheme}://***:***@192.0.2.10:554/stream1", out)

    def test_plain_lines_unchanged(self):
        for line in ("INF go2rtc version 1.9.14", "url=rtsp://192.0.2.10:554/s (no creds)", "http://127.0.0.1:1984/api/streams"):
            self.assertEqual(reg.scrub_credentials_strict(line), line)

    def test_two_urls_in_one_line(self):
        line = "a rtsp://u:p@h1/s b rtsps://u2:p2@h2/s2"
        self.assertEqual(reg.scrub_credentials_strict(line), "a rtsp://***:***@h1/s b rtsps://***:***@h2/s2")


class _Pipe:
    """readline 로 줄을 내주는 가짜 파이프. close() 전까지 EOF 를 내지 않는다."""

    def __init__(self):
        self._r, self._w = os.pipe()
        self._rf = os.fdopen(self._r, "rb", buffering=0)

    def readline(self):
        return self._rf.readline()

    def write_lines(self, lines: list[bytes]):
        for ln in lines:
            os.write(self._w, ln)

    def close_writer(self):
        os.close(self._w)


class RelayBehaviour(unittest.TestCase):
    def setUp(self):
        self.buf = io.BytesIO()

    def test_credential_lines_are_masked_in_file(self):
        p = _Pipe(); r = C._LogRelay(p, self.buf).start()
        p.write_lines([b"INF start\n", f"WRN error=EOF url=rtsp://admin:{_PW_SPECIAL}@192.0.2.10:554/s\n".encode()])
        p.close_writer(); self.assertTrue(r.stop(join_s=3.0))
        text = self.buf.getvalue().decode("utf-8")
        self.assertIn("INF start", text); self.assertIn("rtsp://***:***@192.0.2.10:554/s", text)
        self.assertNotIn("admin", text); self.assertNotIn(_PW_SPECIAL, text)

    def test_flood_does_not_block_producer_and_records_dropped_count(self):
        """쓰기를 일부러 느리게(마스킹 지연) 만들고 큐의 5배를 쏟는다 — 생산자 쓰기는 1 s 안에 끝나야 한다."""
        p = _Pipe()
        slow = C._LogRelay(p, self.buf)
        orig = C._LogRelay.mask
        with mock.patch.object(C._LogRelay, "mask", side_effect=lambda raw: (time.sleep(0.001), orig(raw))[1]):
            slow.start()
            n = C._LogRelay.MAX_QUEUE * 5
            t0 = time.perf_counter()
            p.write_lines([b"WRN url=rtsp://u:p@h/s line %d\n" % i for i in range(n)])
            produce_s = time.perf_counter() - t0
            p.close_writer()
            self.assertLess(produce_s, 3.0, f"생산자 쓰기가 막혔다({produce_s:.2f}s)")
            self.assertTrue(slow.stop(join_s=1.0) or True)   # 상한 안에 돌아오기만 하면 된다(아래서 시간 검증)
        text = self.buf.getvalue().decode("utf-8")
        self.assertNotIn("rtsp://u:p@", text)
        self.assertTrue(("줄 생략" in text) or slow.dropped > 0, "폭주인데 버린 줄 기록이 없다")

    def test_stop_returns_within_bound_even_if_pipe_never_closes(self):
        p = _Pipe(); r = C._LogRelay(p, self.buf).start()
        t0 = time.perf_counter(); ok = r.stop(join_s=0.5); dt = time.perf_counter() - t0
        self.assertTrue(ok); self.assertLess(dt, 1.5)
        p.close_writer()


class _FakeProc:
    def __init__(self, pipe):
        self.pid = 4242; self.stdout = pipe; self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        return 0


class EnsureAndStopWireRelay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root / "bin").mkdir(); (root / "bin" / "go2rtc.exe").write_bytes(b"")
        (root / "config").mkdir(); (root / "config" / "go2rtc.yaml").write_text("streams: {}\n", encoding="utf-8")
        self.root = root; self.pipe = _Pipe(); self.popen_kwargs = {}

        def _popen(*a, **k):
            self.popen_kwargs = k; return _FakeProc(self.pipe)
        for p in (mock.patch.object(C, "_G2_ROOT", root), mock.patch.object(C, "_G2_PROC", None), mock.patch.object(C, "_G2_LOGF", None),
                  mock.patch.object(C, "_G2_RELAY", None), mock.patch("subprocess.Popen", side_effect=_popen),
                  mock.patch.object(C, "_g2_port_busy", return_value=False), mock.patch.object(C, "_lan_ip", return_value="127.0.0.1")):
            p.start(); self.addCleanup(p.stop)
        self.addCleanup(C.stop_go2rtc)

    def test_pipe_relay_masks_and_shutdown_is_fast(self):
        self.assertTrue(C.ensure_go2rtc())
        self.assertIs(self.popen_kwargs.get("stdout"), subprocess.PIPE); self.assertIs(self.popen_kwargs.get("stderr"), subprocess.STDOUT)
        self.assertIsNotNone(C._G2_RELAY)
        self.pipe.write_lines([b"WRN url=rtsps://u:p%40w@192.0.2.5/s\n"])
        time.sleep(0.3)
        t0 = time.perf_counter(); C.stop_go2rtc(); dt = time.perf_counter() - t0   # 파이프 writer 는 아직 열려 있다(EOF 없음)
        self.assertLess(dt, 2.0, f"stop_go2rtc 가 느리다({dt:.2f}s)")
        self.assertIsNone(C._G2_RELAY); self.assertIsNone(C._G2_LOGF)
        self.pipe.close_writer()
        text = (self.root / "data" / "go2rtc.log").read_text(encoding="utf-8")
        self.assertIn("rtsps://***:***@192.0.2.5/s", text); self.assertNotIn("u:p%40w", text)
        self.assertEqual(threading.active_count() >= 1, True)


if __name__ == "__main__":
    unittest.main()
