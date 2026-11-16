"""[2026-11-16 박스 일치 보기 2단계] boxed_view + GET /cameras/{cid}/boxed.mjpg.

고정하는 것:
  ① render 는 **사본**에 그린다 — 원본 프레임은 1픽셀도 바뀌지 않는다(비식별화가 꺼져 있어도).
  ② 박스 테두리 색: 사람·미착용(NO-*)·그 외가 허브 화면과 같은 색.
  ③ jpeg_for: 디코드 가능한 JPEG, 크기 = 원본 크기. 같은 카메라·같은 시각은 **한 번만** 인코딩(시청자 수 무관).
  ④ frames: 새 묶음마다 1조각 · 같은 묶음은 다시 보내지 않음 · 서버 종료(closing)·프레임 정지(STALL_END_S)면 끝난다.
  ⑤ 표시 필터(keep)로 걸러진 검출은 그려지지 않는다.
  ⑥ 라우트: 없는 카메라 404 · 첫 추론 전 503 · 정상이면 multipart 응답에 JPEG 가 들어 있다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import boxed_view  # noqa: E402
import privacy  # noqa: E402
from _isolate import isolate_alerts, isolate_no_token, isolate_shutdown, isolate_warmup  # noqa: E402

_PERSON = {"class": "person", "score": 0.91, "bbox": [0.25, 0.25, 0.75, 0.75], "id": 3, "stale": False}
_NOHAT = {"class": "NO-Hardhat", "score": 0.8, "bbox": [0.1, 0.1, 0.2, 0.2], "id": 5, "stale": False}
_VEST = {"class": "Safety-Vest", "score": 0.7, "bbox": [0.8, 0.8, 0.95, 0.95], "id": 6, "stale": False}


def _frame(v: int = 40):
    return np.full((240, 320, 3), v, dtype=np.uint8)


def _no_privacy():
    return mock.patch.object(privacy, "enabled", return_value=False)


class RenderTest(unittest.TestCase):
    def setUp(self):
        p = _no_privacy(); p.start(); self.addCleanup(p.stop)

    def test_draws_on_copy_never_on_original(self):
        f = _frame(); before = f.copy()
        out = boxed_view.render(f, [_PERSON])
        self.assertIsNot(out, f)
        self.assertTrue(np.array_equal(f, before), "원본 프레임이 바뀌었다")
        self.assertFalse(np.array_equal(out, before), "사본에 아무것도 그려지지 않았다")

    def test_box_edge_colors_match_hub(self):
        out = boxed_view.render(_frame(), [_PERSON, _NOHAT, _VEST])
        # 왼쪽 변의 한가운데 픽셀(라벨 글자와 겹치지 않는 자리)
        self.assertEqual(tuple(int(v) for v in out[120, 80]), boxed_view._COL_PERSON)       # x=0.25*320, y=중앙
        self.assertEqual(tuple(int(v) for v in out[36, 32]), boxed_view._COL_MISSING)       # x=0.1*320, y=0.15*240
        self.assertEqual(tuple(int(v) for v in out[210, 256]), boxed_view._COL_OTHER)       # x=0.8*320, y=0.875*240

    def test_bad_bbox_is_skipped_not_raised(self):
        out = boxed_view.render(_frame(), [{"class": "person", "score": 0.5, "bbox": None}, _PERSON])
        self.assertEqual(tuple(int(v) for v in out[120, 80]), boxed_view._COL_PERSON)

    def test_copy_even_when_privacy_enabled(self):
        f = _frame(); before = f.copy()
        with mock.patch.object(privacy, "enabled", return_value=True):
            out = boxed_view.render(f, [_PERSON])
        self.assertIsNot(out, f)
        self.assertTrue(np.array_equal(f, before))


class JpegAndCacheTest(unittest.TestCase):
    def setUp(self):
        p = _no_privacy(); p.start(); self.addCleanup(p.stop)
        boxed_view.forget("t1"); self.addCleanup(boxed_view.forget, "t1")

    def test_jpeg_decodes_to_same_size(self):
        jpg = boxed_view.jpeg_for("t1", (_frame(), [_PERSON], 1.0))
        self.assertIsNotNone(jpg)
        self.assertEqual(jpg[:2], b"\xff\xd8")
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape, (240, 320, 3))

    def test_same_ts_encoded_once(self):
        view = (_frame(), [_PERSON], 5.0)
        with mock.patch.object(boxed_view.cv2, "imencode", wraps=cv2.imencode) as enc:
            a = boxed_view.jpeg_for("t1", view); b = boxed_view.jpeg_for("t1", view)
            self.assertEqual(enc.call_count, 1, "같은 프레임을 두 번 인코딩했다")
            self.assertIs(a, b)
            boxed_view.jpeg_for("t1", (_frame(), [_PERSON], 6.0))
            self.assertEqual(enc.call_count, 2)

    def test_encode_failure_returns_none(self):
        with mock.patch.object(boxed_view.cv2, "imencode", return_value=(False, None)):
            self.assertIsNone(boxed_view.jpeg_for("t1", (_frame(), [], 9.0)))

    def test_keep_filter_hides_boxes(self):
        plain = boxed_view.jpeg_for("t1", (_frame(), [], 1.0))
        hidden = boxed_view.jpeg_for("t1", (_frame(), [_PERSON], 2.0), keep=lambda d: False)
        shown = boxed_view.jpeg_for("t1", (_frame(), [_PERSON], 3.0), keep=lambda d: True)
        self.assertEqual(hidden, plain, "걸러진 검출이 그려졌다")
        self.assertNotEqual(shown, plain)


class FramesGeneratorTest(unittest.TestCase):
    def setUp(self):
        p = _no_privacy(); p.start(); self.addCleanup(p.stop)
        boxed_view.forget("t2"); self.addCleanup(boxed_view.forget, "t2")
        for name, val in (("POLL_S", 0.001), ("KEEPALIVE_S", 0.02), ("STALL_END_S", 0.08)):
            q = mock.patch.object(boxed_view, name, val); q.start(); self.addCleanup(q.stop)

    def test_one_part_per_new_view_then_ends_on_stall(self):
        views = [(_frame(10), [_PERSON], 1.0), (_frame(20), [_PERSON], 2.0), (_frame(30), [], 3.0)]
        calls = {"n": 0}

        def get_view():
            calls["n"] += 1
            return views[min(len(views) - 1, calls["n"] // 3)]      # 같은 묶음이 여러 번 보인다(폴링이 더 잦다)

        parts = list(boxed_view.frames("t2", get_view))
        jpgs = [p[p.index(b"\r\n\r\n") + 4:-2] for p in parts]
        distinct = []
        for j in jpgs:
            if not distinct or distinct[-1] != j:
                distinct.append(j)
        self.assertEqual(len(distinct), 3, "묶음 3개 → 서로 다른 장면 3장이어야 한다")
        for p in parts:
            self.assertTrue(p.startswith(b"--" + boxed_view.BOUNDARY.encode()))
            self.assertIn(b"Content-Type: image/jpeg", p)
        # 생성기가 스스로 끝났다(list() 가 돌아왔다) = STALL_END_S 종료 조건이 작동했다

    def test_keepalive_resends_last_frame(self):
        view = (_frame(), [_PERSON], 1.0)
        parts = list(boxed_view.frames("t2", lambda: view))
        self.assertGreaterEqual(len(parts), 2, "프레임이 멈춘 동안 직전 장면을 다시 보내야 한다")
        self.assertEqual(len(set(parts)), 1)

    def test_each_part_carries_capture_timestamp(self):
        """받는 쪽이 '수신 시각 − X-Timestamp' 로 서버 구간 지연을 잴 수 있다(2026-11-16 실측 중앙값 48 ms)."""
        parts = list(boxed_view.frames("t2", lambda: (_frame(), [], 1234.5)))
        self.assertIn(b"X-Timestamp: 1234.500\r\n\r\n", parts[0])

    def test_closing_ends_immediately(self):
        self.assertEqual(list(boxed_view.frames("t2", lambda: (_frame(), [], 1.0), closing=lambda: True)), [])

    def test_no_view_yet_ends_on_stall_without_parts(self):
        self.assertEqual(list(boxed_view.frames("t2", lambda: None)), [])


class _Cap:
    """worker._StreamCapture 대역 — read_latest() 가 (프레임, 캡처 시각)."""

    def __init__(self, frame, ts):
        self.frame, self.ts = frame, ts

    def read_latest(self):
        return self.frame, self.ts


class LatestViewTest(unittest.TestCase):
    """[B안, 2026-11-16 사용자 결정] 모든 캡처 프레임 + 가장 최근 박스."""

    def _wk(self, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(**kw)

    def test_uses_capture_frame_with_latest_boxes(self):
        det_frame, live = _frame(10), _frame(99)
        wk = self._wk(_last_view=(det_frame, [_PERSON], 100.0), _streamcap=_Cap(live, 100.1))
        frame, dets, ts = boxed_view.latest_view(wk, now=100.2)
        self.assertIs(frame, live, "영상은 검출 프레임이 아니라 가장 최근 캡처 프레임이어야 한다")
        self.assertEqual(dets, [_PERSON])
        self.assertEqual(ts, 100.1, "시각은 캡처 시각 — 새 캡처마다 새 장이 나가야 한다")

    def test_ts_follows_capture_not_detection(self):
        cap = _Cap(_frame(1), 100.10)
        wk = self._wk(_last_view=(_frame(0), [_PERSON], 100.0), _streamcap=cap)
        a = boxed_view.latest_view(wk, now=100.2)
        cap.frame, cap.ts = _frame(2), 100.13                 # 검출은 그대로, 캡처만 다음 장
        b = boxed_view.latest_view(wk, now=100.2)
        self.assertNotEqual(a[2], b[2]); self.assertEqual(a[1], b[1])

    def test_old_boxes_are_dropped_but_video_continues(self):
        live = _frame(99)
        wk = self._wk(_last_view=(_frame(10), [_PERSON], 100.0), _streamcap=_Cap(live, 105.0))
        frame, dets, _ = boxed_view.latest_view(wk, now=100.0 + boxed_view.BOX_MAX_AGE_S + 0.1)
        self.assertIs(frame, live)
        self.assertEqual(dets, [], "검출이 멈춘 뒤의 옛 박스가 살아 있는 영상에 남으면 안 된다")

    def test_video_without_boxes_before_first_inference(self):
        live = _frame(99)
        frame, dets, ts = boxed_view.latest_view(self._wk(_last_view=None, _streamcap=_Cap(live, 7.0)), now=7.1)
        self.assertIs(frame, live); self.assertEqual(dets, []); self.assertEqual(ts, 7.0)

    def test_falls_back_to_detected_frame_bundle_without_capture_thread(self):
        bundle = (_frame(10), [_PERSON], 100.0)
        self.assertIs(boxed_view.latest_view(self._wk(_last_view=bundle, _streamcap=None)), bundle)
        # 캡처 스레드는 있으나 아직 첫 프레임 전
        self.assertIs(boxed_view.latest_view(self._wk(_last_view=bundle, _streamcap=_Cap(None, 0.0))), bundle)

    def test_none_when_nothing_available(self):
        self.assertIsNone(boxed_view.latest_view(self._wk(_last_view=None, _streamcap=None)))
        self.assertIsNone(boxed_view.latest_view(None))

    def test_capture_error_falls_back(self):
        class _Bad:
            def read_latest(self):
                raise RuntimeError("closing")
        bundle = (_frame(10), [], 1.0)
        self.assertIs(boxed_view.latest_view(self._wk(_last_view=bundle, _streamcap=_Bad())), bundle)


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts()); self.addCleanup(isolate_shutdown())
        self.addCleanup(isolate_no_token()); self.addCleanup(isolate_warmup())
        p = _no_privacy(); p.start(); self.addCleanup(p.stop)
        boxed_view.forget("camv"); self.addCleanup(boxed_view.forget, "camv")
        for name, val in (("POLL_S", 0.001), ("KEEPALIVE_S", 0.02), ("STALL_END_S", 0.08)):
            q = mock.patch.object(boxed_view, name, val); q.start(); self.addCleanup(q.stop)

    def _client(self):
        import main
        from fastapi.testclient import TestClient
        return TestClient(main.app)

    def test_unknown_camera_404(self):
        with self._client() as c:
            self.assertEqual(c.get("/cameras/no-such-id/boxed.mjpg").status_code, 404)

    def test_before_first_inference_503(self):
        import worker
        fake = mock.Mock(); fake._last_view = None; fake._streamcap = None
        with mock.patch.dict(worker.manager._workers, {"camv": fake}, clear=False), self._client() as c:
            self.assertEqual(c.get("/cameras/camv/boxed.mjpg").status_code, 503)

    def test_stream_contains_jpeg_and_hides_non_display_labels(self):
        import worker
        train = {"class": "train", "score": 0.9, "bbox": [0.5, 0.5, 0.9, 0.9], "id": 9, "stale": False}
        stale = {**_PERSON, "stale": True}
        fake = mock.Mock(); fake._last_view = (_frame(), [train, stale], 1.0); fake._streamcap = None
        with mock.patch.dict(worker.manager._workers, {"camv": fake}, clear=False), self._client() as c:
            r = c.get("/cameras/camv/boxed.mjpg")        # 프레임이 멈춰 있으므로 STALL_END_S 뒤 스트림이 끝난다
        self.assertEqual(r.status_code, 200)
        self.assertIn("multipart/x-mixed-replace", r.headers["content-type"])
        body = r.content
        start = body.index(b"\xff\xd8"); end = body.index(b"\r\n--", start) if b"\r\n--" in body[start:] else len(body) - 2
        img = cv2.imdecode(np.frombuffer(body[start:end], np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(img.shape, (240, 320, 3))
        # 표시 대상이 아닌 train·stale 박스는 그려지지 않아 화면이 단색 그대로다(JPEG 손실 허용 ±3)
        self.assertLessEqual(int(np.abs(img.astype(int) - 40).max()), 3, "걸러져야 할 박스가 그려졌다")

    def test_stream_uses_live_capture_frame_with_latest_boxes(self):
        """[B안] 캡처 스레드가 있으면 영상은 캡처 프레임(밝기 200), 박스는 마지막 검출의 것."""
        import time as _t

        import worker
        fake = mock.Mock()
        fake._last_view = (_frame(40), [_PERSON], _t.time())      # 검출한 프레임(밝기 40) + 박스
        fake._streamcap = _Cap(_frame(200), _t.time())            # 그 뒤에 들어온 최신 캡처 프레임
        with mock.patch.dict(worker.manager._workers, {"camv": fake}, clear=False), self._client() as c:
            r = c.get("/cameras/camv/boxed.mjpg")
        self.assertEqual(r.status_code, 200)
        body = r.content
        start = body.index(b"\xff\xd8"); end = body.index(b"\r\n--", start) if b"\r\n--" in body[start:] else len(body) - 2
        img = cv2.imdecode(np.frombuffer(body[start:end], np.uint8), cv2.IMREAD_COLOR)
        self.assertGreater(int(img[10, 10].mean()), 150, "배경이 캡처 프레임(밝기 200)이 아니다")
        # 사람 박스 왼쪽 변 한가운데: 사람 색(BGR 255,208,57)은 빨강 채널이 낮다 — 배경(200)과 뚜렷이 달라야 한다.
        #   (1픽셀 선은 JPEG 색 번짐으로 정확한 색이 안 나온다 → 정확한 색은 RenderTest 가, 여기서는 '그려졌는가'만 본다)
        self.assertLess(int(img[120, 80][2]), 150, "최신 박스가 그려지지 않았다")
        self.assertGreater(int(img[120, 160][2]), 180, "박스 안쪽(선이 없는 자리)은 배경 그대로여야 한다")


if __name__ == "__main__":
    unittest.main()
