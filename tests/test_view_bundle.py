"""[2026-11-16 박스 일치 보기 1단계] Worker._last_view — 같은 프레임의 (영상, 박스, 시각) 묶음.

배경: 관제 화면은 영상(go2rtc)과 박스(/cameras/{id}/detections)가 따로 도착해 박스가 사람을 뒤따라 보였다.
서버가 '검출에 쓴 바로 그 프레임'에 박스를 그려 내보내려면, 프레임과 박스가 **같은 순간의 짝**으로 보관돼야 한다.

  ① 추론이 끝나면 묶음의 프레임이 **이번에 추론한 그 프레임 객체**이고, 박스·시각도 이번 것이다.
  ② 다음 프레임을 처리하면 묶음이 통째로 바뀐다(이전 프레임 + 새 박스 같은 섞임이 없다).
  ③ 추론이 예외로 끝나면 묶음은 직전 값 그대로다(반쯤 갱신된 짝이 생기지 않는다).
  ④ 묶음 저장은 원본 프레임을 바꾸지 않는다(증거·수집용 원본 보존).
  ⑤ start() 는 이전 실행의 묶음을 비운다.
"""
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vigent-core"))

import data_engine  # noqa: E402
import worker  # noqa: E402
from _isolate import isolate_alerts  # noqa: E402


def _ctx():
    mtrack = type("M", (), {"update": lambda self, d, t: []})()
    etrack = type("E", (), {"update": lambda self, f, t, b: []})()
    return worker._FrameCtx(["person"], [], mtrack, etrack,
                            False, 30.0, ROOT / "data" / "dataset" / "images", "TESTCAM", "test://src")


class _SeqGuard:
    """호출마다 다른 위치의 person 1명을 돌려준다 — 묶음이 '이번 프레임 것'인지 구분하기 위해."""

    def __init__(self):
        self.n = 0

    def detect(self, frame, detectors=None, track_key="default"):
        self.n += 1
        x = 0.1 * self.n
        return {"signals": {}, "person_count": 1,
                "detections": [{"label": "person", "conf": 0.9, "bbox": [x, 0.2, x + 0.1, 0.8], "tid": 7, "detector": "person"}]}


class _RaisingGuard:
    def detect(self, frame, detectors=None, track_key="default"):
        raise RuntimeError("boom")


class TestViewBundle(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts())
        self.lock = threading.Lock()
        self.worker = worker.Worker()
        p = mock.patch.object(data_engine, "log_event", lambda **kw: None)
        p.start()
        self.addCleanup(p.stop)

    def test_initially_none(self):
        self.assertIsNone(self.worker._last_view)

    def test_bundle_pairs_same_frame_boxes_and_ts(self):
        f1 = np.full((48, 64, 3), 10, dtype=np.uint8)
        self.worker._process_frame(f1, 100.0, _SeqGuard(), self.lock, _ctx())
        view = self.worker._last_view
        self.assertIsNotNone(view)
        frame, dets, ts = view
        self.assertIs(frame, f1, "묶음의 프레임은 이번에 추론한 바로 그 프레임이어야 한다")
        self.assertEqual(ts, 100.0)
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0]["class"], "person")
        self.assertAlmostEqual(dets[0]["bbox"][0], 0.1, places=3)

    def test_bundle_replaced_as_a_whole_on_next_frame(self):
        g, ctx = _SeqGuard(), _ctx()
        f1 = np.full((48, 64, 3), 10, dtype=np.uint8)
        f2 = np.full((48, 64, 3), 20, dtype=np.uint8)
        self.worker._process_frame(f1, 100.0, g, self.lock, ctx)
        v1 = self.worker._last_view
        self.worker._process_frame(f2, 100.5, g, self.lock, ctx)
        v2 = self.worker._last_view
        self.assertIsNot(v1, v2)
        self.assertIs(v2[0], f2)
        self.assertEqual(v2[2], 100.5)
        self.assertAlmostEqual(v2[1][0]["bbox"][0], 0.2, places=3, msg="새 프레임에는 새 박스가 짝이어야 한다")
        # 이전 묶음은 그대로다(읽던 쪽이 들고 있어도 내용이 바뀌지 않는다)
        self.assertIs(v1[0], f1)
        self.assertAlmostEqual(v1[1][0]["bbox"][0], 0.1, places=3)

    def test_bundle_kept_when_inference_raises(self):
        ctx = _ctx()
        f1 = np.full((48, 64, 3), 10, dtype=np.uint8)
        self.worker._process_frame(f1, 100.0, _SeqGuard(), self.lock, ctx)
        v1 = self.worker._last_view
        self.worker._process_frame(np.zeros((48, 64, 3), dtype=np.uint8), 100.5, _RaisingGuard(), self.lock, ctx)
        self.assertIs(self.worker._last_view, v1, "추론 실패 프레임은 묶음을 건드리지 않는다")

    def test_original_frame_not_modified(self):
        f1 = np.full((48, 64, 3), 10, dtype=np.uint8)
        before = f1.copy()
        self.worker._process_frame(f1, 100.0, _SeqGuard(), self.lock, _ctx())
        self.assertTrue(np.array_equal(f1, before), "원본 프레임에 무엇도 그려지면 안 된다")

    def test_start_clears_previous_bundle(self):
        self.worker._last_view = (np.zeros((2, 2, 3), dtype=np.uint8), [], 1.0)
        with mock.patch.object(worker.threading, "Thread") as th:
            th.return_value = mock.Mock()
            self.worker.start(guard=None, lock=self.lock, source="test://src", name="TESTCAM")
        self.assertIsNone(self.worker._last_view)
        self.worker.state["running"] = False


if __name__ == "__main__":
    unittest.main()
