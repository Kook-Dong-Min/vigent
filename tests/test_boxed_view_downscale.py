"""[2026-11-16] 박스 영상을 **화면 표시 크기로 줄여서** 만든다 — 웹캠보다 해상도가 높은 영상에서 CPU 가 치솟던 문제의 수정.

배경(이 노트북 실측, benchmarks/boxed_view_resolution_cpu_20261116.md): 박스 영상 1장마다 원본 크기 그대로 얼굴 찾기(YuNet)를
돌렸고 그 비용이 화소 수에 비례했다(640×480 30 ms/장 → 1920×1080 212 ms/장 = 계산상 초당 약 4.7장, 30장/초 카메라를 못 따라간다).

고정하는 것:
  ① fit: **긴 변**이 상한을 넘으면 가로·세로 비를 유지해 줄인다 — 가로 영상·세로 영상·4:3 모두 같은 규칙. 넘지 않으면 그 배열 그대로
     (키우지 않는다). 0 = 끔. 원본은 바뀌지 않는다.
  ② 설정: hub.boxed_max_side / hub.boxed_face_side. 이상한 값(문자열·음수·NaN·참/거짓·너무 작은 양수)은 기본값, 0 은 끔,
     환경변수가 우선. 코드 기본값 = config/tuning.yaml 값.
  ③ render/jpeg_for: 큰 프레임은 줄인 그림으로 나가고 박스는 같은 (정규화) 자리에 그려진다. 상한 0 이면 예전처럼 원본 크기.
     얼굴 찾기에는 줄인 그림과 face_side 가 전달된다.
  ④ privacy.anonymize_faces(face_max_side=…): 줄인 사본에서 얼굴을 찾고, 좌표를 원래 크기로 되돌려 **원래 크기 그림**에 모자이크.
     인자를 안 주면(증거 JPEG·스냅샷 경로) 예전과 똑같이 프레임 크기 그대로 찾는다.
  ⑤ 라우트: 캡처 스레드가 있는 소스(웹캠·RTSP)와 없는 소스(파일·sync) **둘 다** 줄인 영상이 나온다.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import boxed_view  # noqa: E402
import privacy  # noqa: E402
import tuning  # noqa: E402
from _isolate import isolate_alerts, isolate_no_token, isolate_shutdown, isolate_warmup  # noqa: E402

_PERSON = {"class": "person", "score": 0.91, "bbox": [0.25, 0.25, 0.75, 0.75], "id": 3, "stale": False}


def _frame(h: int, w: int, v: int = 40):
    return np.full((h, w, 3), v, dtype=np.uint8)


def _textured(h: int, w: int):
    return np.random.default_rng(7).integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def _sides(max_side: int, face_side: int):
    """설정 파일과 무관하게 두 상한을 고정한다(테스트는 기계·설정 상태에 기대지 않는다)."""
    a = mock.patch.object(boxed_view, "max_side", return_value=max_side)
    b = mock.patch.object(boxed_view, "face_side", return_value=face_side)
    return a, b


class FitTest(unittest.TestCase):
    def test_landscape_long_side_capped(self):
        self.assertEqual(boxed_view.fit(_frame(1080, 1920), 1280).shape, (720, 1280, 3))
        self.assertEqual(boxed_view.fit(_frame(1296, 2304), 1280).shape, (720, 1280, 3))
        self.assertEqual(boxed_view.fit(_frame(2160, 3840), 1280).shape, (720, 1280, 3))

    def test_portrait_capped_by_height(self):
        """세로 영상(폰 등)은 세로가 긴 변이다 — 가로만 보면 1080 < 1280 이라 줄지 않는다."""
        self.assertEqual(boxed_view.fit(_frame(1920, 1080), 1280).shape, (1280, 720, 3))

    def test_four_by_three_keeps_aspect(self):
        self.assertEqual(boxed_view.fit(_frame(1944, 2592), 1280).shape, (960, 1280, 3))

    def test_within_limit_returns_same_array(self):
        for h, w in ((480, 640), (720, 1280), (1280, 720)):
            f = _frame(h, w)
            self.assertIs(boxed_view.fit(f, 1280), f, f"{w}x{h} 는 상한 이하 — 복사·확대 없이 그대로여야 한다")

    def test_zero_or_negative_disables(self):
        f = _frame(1080, 1920)
        self.assertIs(boxed_view.fit(f, 0), f)
        self.assertIs(boxed_view.fit(f, -1), f)

    def test_original_not_modified_and_new_array_returned(self):
        f = _textured(1080, 1920); before = f.copy()
        out = boxed_view.fit(f, 1280)
        self.assertIsNot(out, f)
        self.assertTrue(np.array_equal(f, before), "원본 프레임이 바뀌었다")


class SettingTest(unittest.TestCase):
    def _with(self, value):
        return mock.patch.object(boxed_view.tuning, "val", side_effect=lambda sec, key, default, env=None: value)

    def test_valid_values(self):
        for raw, want in ((1280, 1280), (960, 960), ("960", 960), (960.0, 960), (0, 0), ("0", 0), (160, 160)):
            with self._with(raw):
                self.assertEqual(boxed_view.max_side(), want, f"{raw!r}")
                self.assertEqual(boxed_view.face_side(), want, f"{raw!r}")

    def test_invalid_values_fall_back_to_default(self):
        for raw in ("abc", -5, True, False, float("nan"), float("inf"), [1280], {"a": 1}, 50, 159, 10**9):
            with self._with(raw):
                self.assertEqual(boxed_view.max_side(), boxed_view.DEFAULT_MAX_SIDE, f"{raw!r}")
                self.assertEqual(boxed_view.face_side(), boxed_view.DEFAULT_FACE_SIDE, f"{raw!r}")

    def test_absent_key_uses_code_default(self):
        with mock.patch.object(boxed_view.tuning, "val", side_effect=lambda sec, key, default, env=None: default), \
                mock.patch.dict(os.environ, {}, clear=False):
            self.assertEqual(boxed_view.max_side(), 1280)
            self.assertEqual(boxed_view.face_side(), 640)

    def test_reads_the_documented_keys(self):
        seen = []

        def _val(sec, key, default, env=None):
            seen.append((sec, key, env))
            return default
        with mock.patch.object(boxed_view.tuning, "val", side_effect=_val):
            boxed_view.max_side(); boxed_view.face_side()
        self.assertEqual(seen, [("hub", "boxed_max_side", "VIGENT_BOXED_MAX_SIDE"),
                                ("hub", "boxed_face_side", "VIGENT_BOXED_FACE_SIDE")])

    def test_env_overrides_file(self):
        """재시작 없이 되돌리기·전후 비교 측정용 — 환경변수가 파일 값보다 우선."""
        with mock.patch.dict(os.environ, {"VIGENT_BOXED_MAX_SIDE": "0", "VIGENT_BOXED_FACE_SIDE": "480"}):
            self.assertEqual(boxed_view.max_side(), 0)
            self.assertEqual(boxed_view.face_side(), 480)
        with mock.patch.dict(os.environ, {"VIGENT_BOXED_MAX_SIDE": "oops"}):
            self.assertEqual(boxed_view.max_side(), boxed_view.DEFAULT_MAX_SIDE)

    def test_code_defaults_match_tuning_yaml(self):
        """코드 기본값과 설정 파일 값이 다르면, 파일이 없을 때 조용히 다른 동작이 된다(CODE_AUDIT #6 과 같은 병)."""
        hub = tuning.load_file(_ROOT / "config" / "tuning.yaml").get("hub") or {}
        self.assertEqual(hub.get("boxed_max_side"), boxed_view.DEFAULT_MAX_SIDE)
        self.assertEqual(hub.get("boxed_face_side"), boxed_view.DEFAULT_FACE_SIDE)


class RenderDownscaleTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(privacy, "enabled", return_value=False); p.start(); self.addCleanup(p.stop)

    def _fix(self, max_side: int, face_side: int = 640):
        for p in _sides(max_side, face_side):
            p.start(); self.addCleanup(p.stop)

    def test_large_frame_is_rendered_small_and_original_untouched(self):
        self._fix(1280)
        f = _frame(1080, 1920); before = f.copy()
        out = boxed_view.render(f, [_PERSON])
        self.assertEqual(out.shape, (720, 1280, 3))
        self.assertTrue(np.array_equal(f, before), "원본 프레임이 바뀌었다")

    def test_box_lands_on_same_normalized_spot(self):
        """박스 좌표는 정규화라 줄인 그림에서도 같은 자리다 — 사람 박스 왼쪽 변(x=0.25) 한가운데."""
        self._fix(1280)
        out = boxed_view.render(_frame(1080, 1920), [_PERSON])
        self.assertEqual(tuple(int(v) for v in out[360, 320]), boxed_view._COL_PERSON)      # x=0.25*1280, y=0.5*720
        self.assertEqual(tuple(int(v) for v in out[360, 640]), (40, 40, 40), "박스 안쪽은 배경 그대로여야 한다")

    def test_portrait_frame(self):
        self._fix(1280)
        out = boxed_view.render(_frame(1920, 1080), [_PERSON])
        self.assertEqual(out.shape, (1280, 720, 3))
        self.assertEqual(tuple(int(v) for v in out[640, 180]), boxed_view._COL_PERSON)      # x=0.25*720, y=0.5*1280

    def test_zero_keeps_original_size(self):
        """되돌리기 경로: hub.boxed_max_side: 0 → 예전과 같은 원본 크기."""
        self._fix(0)
        f = _frame(1080, 1920); before = f.copy()
        out = boxed_view.render(f, [_PERSON])
        self.assertEqual(out.shape, (1080, 1920, 3))
        self.assertIsNot(out, f)
        self.assertTrue(np.array_equal(f, before))

    def test_small_frame_not_enlarged(self):
        self._fix(1280)
        self.assertEqual(boxed_view.render(_frame(480, 640), [_PERSON]).shape, (480, 640, 3))

    def test_face_search_gets_small_image_and_face_side(self):
        self._fix(1280, 640)
        got = {}

        def _spy(frame, boxes=None, face_max_side=0):
            got.update(shape=frame.shape, boxes=boxes, face=face_max_side)
            return frame.copy()
        with mock.patch.object(privacy, "anonymize_faces", side_effect=_spy):
            boxed_view.render(_frame(1296, 2304), [_PERSON])
        self.assertEqual(got["shape"], (720, 1280, 3), "얼굴 찾기에 원본 크기가 들어갔다")
        self.assertEqual(got["face"], 640)
        self.assertEqual(got["boxes"], [_PERSON["bbox"]])

    def test_privacy_failure_on_unshrunk_frame_still_copies(self):
        """비식별화가 실패해 받은 그림을 그대로 돌려줘도(= 원본) 원본에 그리지 않는다."""
        self._fix(1280)
        f = _frame(480, 640); before = f.copy()
        with mock.patch.object(privacy, "anonymize_faces", side_effect=lambda fr, boxes=None, face_max_side=0: fr):
            out = boxed_view.render(f, [_PERSON])
        self.assertIsNot(out, f)
        self.assertTrue(np.array_equal(f, before))


class JpegDownscaleTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(privacy, "enabled", return_value=False); p.start(); self.addCleanup(p.stop)
        boxed_view.forget("big"); self.addCleanup(boxed_view.forget, "big")

    def _decode(self, jpg):
        return cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)

    def test_jpeg_is_small_and_smaller_in_bytes(self):
        f = _textured(1080, 1920)
        a, b = _sides(1280, 640)
        with a, b:
            small = boxed_view.jpeg_for("big", (f, [_PERSON], 1.0))
        a, b = _sides(0, 0)
        with a, b:
            full = boxed_view.jpeg_for("big", (f, [_PERSON], 2.0))
        self.assertEqual(self._decode(small).shape, (720, 1280, 3))
        self.assertEqual(self._decode(full).shape, (1080, 1920, 3))
        self.assertLess(len(small), len(full))


class _FakeYuNet:
    """cv2.FaceDetectorYN 대역 — 받은 입력 크기를 기록하고, 정해 둔 얼굴 1개를 (찾은 그림 좌표로) 돌려준다."""

    def __init__(self, face_xywh):
        self.face = face_xywh
        self.input_size = None
        self.seen_shape = None

    def setInputSize(self, size):
        self.input_size = tuple(size)

    def detect(self, img):
        self.seen_shape = img.shape
        row = np.zeros((1, 15), dtype=np.float32)
        row[0, :4] = self.face
        return 1, row


def _var(img, x1, y1, x2, y2):
    return float(np.var(img[y1:y2, x1:x2].astype(np.float32)))


class AnonymizeFaceSideTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(privacy, "enabled", return_value=True); p.start(); self.addCleanup(p.stop)

    def _run(self, frame, fake, **kw):
        with mock.patch.object(privacy, "_get_cascade", return_value=fake):
            return privacy.anonymize_faces(frame, [], **kw)

    def test_searches_on_small_copy_and_mosaics_at_full_scale(self):
        f = _textured(1080, 1920); before = f.copy()
        fake = _FakeYuNet((100, 50, 40, 40))                     # 640×360 그림에서의 얼굴
        out = self._run(f, fake, face_max_side=640)
        self.assertEqual(fake.input_size, (640, 360))
        self.assertEqual(fake.seen_shape, (360, 640, 3))
        self.assertEqual(out.shape, f.shape, "돌려주는 그림은 원래 크기여야 한다(얼굴 찾기만 작게)")
        self.assertTrue(np.array_equal(f, before), "원본 프레임이 바뀌었다")
        # 얼굴은 3배 되돌린 자리 (300,150)-(420,270) — 그 안쪽은 뭉개지고, 먼 곳은 원본 그대로
        self.assertLess(_var(out, 310, 160, 410, 260), _var(f, 310, 160, 410, 260) * 0.6, "되돌린 자리가 뭉개지지 않았다")
        self.assertTrue(np.array_equal(out[600:1000, 900:1800], f[600:1000, 900:1800]), "얼굴과 먼 곳이 바뀌었다")
        # 줄인 좌표 그대로(100,50)에 걸렸다면 여기가 뭉개진다 — 그러면 좌표를 안 되돌린 것
        self.assertTrue(np.array_equal(out[55:85, 105:135], f[55:85, 105:135]), "좌표를 원래 크기로 되돌리지 않았다")

    def test_default_keeps_full_size_search(self):
        """증거 JPEG·스냅샷 경로(인자 없음)는 예전과 같아야 한다 — 프레임 크기 그대로 찾고, 좌표도 그대로 쓴다."""
        f = _textured(1080, 1920)
        fake = _FakeYuNet((300, 150, 120, 120))
        out = self._run(f, fake)
        self.assertEqual(fake.input_size, (1920, 1080))
        self.assertEqual(fake.seen_shape, (1080, 1920, 3))
        self.assertLess(_var(out, 310, 160, 410, 260), _var(f, 310, 160, 410, 260) * 0.6)

    def test_never_enlarges_for_search(self):
        for kw in ({"face_max_side": 640}, {"face_max_side": 4000}, {"face_max_side": 0}):
            fake = _FakeYuNet((10, 10, 20, 20))
            self._run(_textured(480, 640), fake, **kw)
            self.assertEqual(fake.input_size, (640, 480), f"{kw} — 긴 변이 상한 이하면 그대로 찾아야 한다")

    def test_portrait_frame_scaled_by_long_side(self):
        fake = _FakeYuNet((10, 10, 20, 20))
        self._run(_textured(1920, 1080), fake, face_max_side=640)
        self.assertEqual(fake.input_size, (360, 640))

    def test_real_detector_accepts_small_copy(self):
        """실제 YuNet 으로 한 번 — 줄인 입력 크기를 받아들이고 원래 크기 그림을 돌려준다(모델 파일이 없는 환경은 건너뜀)."""
        real = privacy._get_cascade()
        if real is None:
            self.skipTest("YuNet 모델 파일 없음(vigent-core/weights/face_detection_yunet.onnx)")

        class _Wrap:                       # 진짜 검출기에 그대로 넘기되, 받은 크기와 예외를 적어 둔다(privacy 는 얼굴 찾기 예외를 삼킨다)
            size = None
            err = None

            def setInputSize(self, size):
                _Wrap.size = tuple(size)
                real.setInputSize(size)

            def detect(self, img):
                try:
                    return real.detect(img)
                except Exception as ex:  # noqa: BLE001
                    _Wrap.err = ex
                    raise
        f = _textured(1080, 1920); before = f.copy()
        out = self._run(f, _Wrap(), face_max_side=640)
        self.assertIsNone(_Wrap.err, f"줄인 사본으로 얼굴 찾기가 실패했다: {_Wrap.err!r}")
        self.assertEqual(_Wrap.size, (640, 360))
        self.assertEqual(out.shape, f.shape)
        self.assertTrue(np.array_equal(f, before))


class _Cap:
    """worker._StreamCapture 대역."""

    def __init__(self, frame, ts):
        self.frame, self.ts = frame, ts

    def read_latest(self):
        return self.frame, self.ts


class RouteDownscaleTest(unittest.TestCase):
    """어떤 소스로 들어오든 같은 규칙 — 캡처 스레드 유무 두 경로 모두 줄인 영상이 나온다."""

    def setUp(self):
        self.addCleanup(isolate_alerts()); self.addCleanup(isolate_shutdown())
        self.addCleanup(isolate_no_token()); self.addCleanup(isolate_warmup())
        p = mock.patch.object(privacy, "enabled", return_value=False); p.start(); self.addCleanup(p.stop)
        for q in _sides(1280, 640):
            q.start(); self.addCleanup(q.stop)
        boxed_view.forget("camhd"); self.addCleanup(boxed_view.forget, "camhd")
        for name, val in (("POLL_S", 0.001), ("KEEPALIVE_S", 0.02), ("STALL_END_S", 0.08)):
            q = mock.patch.object(boxed_view, name, val); q.start(); self.addCleanup(q.stop)

    def _first_jpeg_shape(self, fake):
        import main
        import worker
        from fastapi.testclient import TestClient
        with mock.patch.dict(worker.manager._workers, {"camhd": fake}, clear=False), TestClient(main.app) as c:
            r = c.get("/cameras/camhd/boxed.mjpg")
        self.assertEqual(r.status_code, 200)
        body = r.content
        start = body.index(b"\xff\xd8")
        end = body.index(b"\r\n--", start) if b"\r\n--" in body[start:] else len(body) - 2
        return cv2.imdecode(np.frombuffer(body[start:end], np.uint8), cv2.IMREAD_COLOR).shape

    def test_file_or_sync_source_without_capture_thread(self):
        fake = mock.Mock(); fake._last_view = (_frame(1080, 1920), [_PERSON], 1.0); fake._streamcap = None
        self.assertEqual(self._first_jpeg_shape(fake), (720, 1280, 3))

    def test_stream_source_with_capture_thread(self):
        fake = mock.Mock()
        fake._last_view = (_frame(1296, 2304), [_PERSON], time.time())
        fake._streamcap = _Cap(_frame(1296, 2304, 200), time.time())
        self.assertEqual(self._first_jpeg_shape(fake), (720, 1280, 3))


if __name__ == "__main__":
    unittest.main()
