"""[2026-11-16 전 프레임 캡처] worker._StreamCapture._grab_latest — 밀림이 없으면 프레임을 버리지 않는다.

배경: 예전 캡처 루프는 첫 grab 뒤 **항상** 확인용 grab 을 한 번 더 했다. 큐가 비어 있으면 그 grab 이 다음 라이브 프레임을
기다렸다 가져오므로 매번 2장을 꺼내 1장만 썼다 — 30장 웹캠이 15장으로 들어왔다(실측 read_ms 63.7 ms).

고정하는 것(실제 시계·카메라 없이 — 가짜 시계와 가짜 캡처):
  ① 라이브(첫 grab 이 기다렸다): grab 1회로 끝 — 그 프레임을 그대로 쓴다.
  ② 밀림(첫 grab 이 곧바로 돌아왔다): 예전처럼 큐를 비우고 마지막 것만 쓴다(지연 누적 방지 보장 유지).
  ③ 첫 grab 실패 → False. 드레인 도중 실패 → True(앞서 grab 한 프레임은 살아 있다 — 예전 동작과 같다).
  ④ 드레인 상한 60 — 끝없이 빠른 grab 에도 멈춘다.
  ⑤ 되돌리기 스위치(_CAP_DRAIN_ALWAYS): 라이브여도 확인용 grab 을 한 번 더 한다(예전 동작).
"""
from __future__ import annotations

import sys
import time as _real_time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import worker as W  # noqa: E402

LIVE = 0.033      # 다음 라이브 프레임을 기다린 grab(30 fps)
FAST = 0.001      # 큐에 쌓여 있던 프레임을 꺼낸 grab


class _Clock:
    """worker 가 보는 time 모듈 대역 — time() 만 가짜, 나머지(sleep 등)는 진짜로 넘긴다(다른 스레드가 써도 안전)."""

    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(_real_time, name)


class _Cap:
    """grab() 마다 정해진 시간이 걸리고 정해진 결과를 돌려주는 가짜 캡처. plan: [(걸린 시간, 성공 여부), ...]"""

    def __init__(self, clock, plan, repeat=None):
        self.clock, self.plan, self.repeat, self.grabs = clock, list(plan), repeat, 0

    def grab(self):
        dur, ok = self.plan.pop(0) if self.plan else self.repeat
        self.clock.t += dur
        self.grabs += 1
        return ok


class GrabLatestTest(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        p = mock.patch.object(W, "time", self.clock); p.start(); self.addCleanup(p.stop)
        q = mock.patch.object(W, "_CAP_DRAIN_ALWAYS", False); q.start(); self.addCleanup(q.stop)
        self.sc = W._StreamCapture("rtsp://192.0.2.1/s", "t")

    def test_live_frame_is_used_without_extra_grab(self):
        cap = _Cap(self.clock, [(LIVE, True)], repeat=(LIVE, True))
        self.assertTrue(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 1, "밀림이 없는데 확인용 grab 으로 한 장을 더 꺼냈다(= 2장 중 1장만 사용)")

    def test_thirty_live_frames_are_all_delivered(self):
        cap = _Cap(self.clock, [], repeat=(LIVE, True))
        for _ in range(30):
            self.assertTrue(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 30, "30장이 들어왔으면 30번만 꺼내야 한다")

    def test_backlog_is_drained_to_latest(self):
        cap = _Cap(self.clock, [(FAST, True), (FAST, True), (FAST, True), (LIVE, True)], repeat=(LIVE, True))
        self.assertTrue(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 4, "밀린 3장을 비우고 라이브 1장까지 가야 한다")

    def test_first_grab_failure_returns_false(self):
        cap = _Cap(self.clock, [(FAST, False)], repeat=(FAST, False))
        self.assertFalse(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 1)

    def test_failure_during_drain_keeps_previous_frame(self):
        cap = _Cap(self.clock, [(FAST, True), (FAST, False)], repeat=(FAST, False))
        self.assertTrue(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 2)

    def test_drain_is_capped(self):
        cap = _Cap(self.clock, [], repeat=(FAST, True))
        self.assertTrue(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 61, "첫 grab 1 + 드레인 상한 60")

    def test_stop_ends_drain(self):
        self.sc._stop.set()
        cap = _Cap(self.clock, [], repeat=(FAST, True))
        self.assertTrue(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 1)

    def test_rollback_switch_restores_old_behaviour(self):
        with mock.patch.object(W, "_CAP_DRAIN_ALWAYS", True):
            cap = _Cap(self.clock, [], repeat=(LIVE, True))
            self.assertTrue(self.sc._grab_latest(cap))
            self.assertEqual(cap.grabs, 2, "되돌리기 스위치가 켜지면 예전처럼 확인용 grab 을 한 번 더 한다")

    def test_threshold_boundary(self):
        # 정확히 20 ms 는 '기다린 것'이 아니다(초과만) — 예전 드레인 판정과 같은 기준
        cap = _Cap(self.clock, [(W._DRAIN_LIVE_S, True), (LIVE, True)], repeat=(LIVE, True))
        self.assertTrue(self.sc._grab_latest(cap))
        self.assertEqual(cap.grabs, 2)


class _PtsCap:
    """CAP_PROP_POS_MSEC(프레임 PTS)만 돌려주는 가짜 캡처."""

    def __init__(self):
        self.pts = 0.0

    def get(self, prop):
        return self.pts


class MeasureLagTest(unittest.TestCase):
    """[2026-11-16 캡처 밀림 계측] 벽시계 − PTS 의 증가분 = 밀림. '영상이 실제보다 느리다'는 보고를 눈대중이 아니라 값으로 가린다."""

    def setUp(self):
        self.clock = _Clock()
        p = mock.patch.object(W, "time", self.clock); p.start(); self.addCleanup(p.stop)
        self.sc = W._StreamCapture("rtsp://192.0.2.1/s", "t")
        self.cap = _PtsCap()

    def _step(self, wall_ms, pts_ms):
        self.clock.t += wall_ms / 1000.0
        self.cap.pts += pts_ms
        return self.sc._measure_lag(self.cap)

    def test_first_frame_has_no_estimate(self):
        self.assertIsNone(self._step(33.3, 33.3))

    def test_keeping_up_means_near_zero_lag(self):
        self._step(33.3, 33.3)
        for _ in range(90):
            lag = self._step(33.3, 33.3)
        self.assertIsNotNone(lag)
        self.assertLess(lag, 1.0)

    def test_falling_behind_grows_lag(self):
        self._step(33.3, 33.3); self._step(33.3, 33.3)
        for _ in range(30):                      # 프레임은 33.3 ms 분량인데 꺼내는 데 40 ms 씩 걸린다 → 장당 6.7 ms 밀림
            lag = self._step(40.0, 33.3)
        self.assertGreater(lag, 150.0)
        self.assertGreaterEqual(self.sc.lag_max_ms, lag)

    def test_catching_up_brings_lag_back(self):
        self._step(33.3, 33.3); self._step(33.3, 33.3)
        for _ in range(30):
            self._step(40.0, 33.3)
        lag = self._step(1.0, 200.0)             # 밀린 프레임을 한 번에 건너뛰었다(드레인)
        self.assertLess(lag, 20.0)

    def test_slow_clock_drift_is_absorbed(self):
        self._step(33.3, 33.3); self._step(33.3, 33.3)
        for _ in range(3000):                    # 100 초 동안 프레임당 0.03 ms(= 초당 약 1 ms) 어긋남 → 누적 약 90 ms
            lag = self._step(33.33, 33.3)
        self.assertLess(lag, 5.0, "느린 시계 어긋남이 밀림으로 잡혔다(기준선 감쇠가 흡수해야 한다)")

    def test_pts_reset_clears_baseline(self):
        self._step(33.3, 33.3); self._step(33.3, 33.3)
        self.clock.t += 0.033; self.cap.pts = 0.0                # 되감김(재연결 등)
        self.assertIsNone(self.sc._measure_lag(self.cap))
        self.assertIsNone(self.sc._pts_base)

    def test_source_without_pts_gives_none(self):
        class _NoPts:
            def get(self, prop):
                raise RuntimeError("unsupported")
        self.assertIsNone(self.sc._measure_lag(_NoPts()))
        self.assertIsNone(self.sc.lag_ms)

    def test_reset_lag(self):
        self._step(33.3, 33.3); self._step(33.3, 33.3)
        self.sc._reset_lag()
        self.assertIsNone(self.sc.lag_ms); self.assertIsNone(self.sc._pts_last)


if __name__ == "__main__":
    unittest.main()
