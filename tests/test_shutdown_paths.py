"""A-6~A-8 종료 경로(OPEN_ISSUES #14 묶음) 재현·회귀 테스트. [2026-10-11]

A-8 (예열 스레드 vs 종료)
  ⑤ readiness.stop() 은 취소 깃발을 세우고 예열 스레드를 기다린다. 예열 중 종료되면 **on_ready(워커 자동기동)가 호출되지 않는다.**
  ⑥ 취소 뒤 다시 start_background 하면(새 기동) on_ready 가 정상 호출된다. _shutdown 은 예열 취소를 기아 감시 정지보다 앞에 둔다.
A-6 (health_watch 스레드 되살림)
  ⑦ stop() 이 시간 초과로 끝나도 참조를 버리지 않고, 그 상태에서 start() 는 두 번째 스레드를 띄우지 않는다.
  ⑧ 깃발은 스레드마다 따로 — 새 start 가 옛 스레드의 깃발을 지워 되살리지 않는다. stop→start 를 반복해도 감시 스레드는 항상 1개.
A-7 (기아 감시 vs 종료)
  ① starvation_guard.stop() 은 감시 스레드가 끝날 때까지 기다린다(예전: 깃발만).
  ② 종료 깃발이 선 뒤 _restart_worker 는 워커를 되살리지 않는다(manager.stop/start 호출 0).
  ③ 종료 깃발(begin_shutdown)은 start/autostart 를 거부한다 — 운영 중 stop_all 뒤에는 **기존처럼 다시 start 된다**(깃발 무관).
  ④ _shutdown 순서: 기아 감시 정지 → 종료 깃발 → 워커 정지(stop_all) → 그 뒤 나머지.
타이밍 경합 항목이라 이 모듈은 반복 실행(20회)으로 검증한다.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import health_watch as hw  # noqa: E402
import readiness  # noqa: E402
import starvation_guard as sg  # noqa: E402
import worker  # noqa: E402
from _isolate import isolate_alerts  # noqa: E402


class _FakeWorker:
    """start/stop 만 흉내 — 스레드·카메라 없음."""

    def __init__(self):
        self.state = {"running": True, "name": "f"}

    def is_alive(self):
        return False

    def stop(self):
        self.state["running"] = False
        return {"ok": True}

    def status(self):
        return dict(self.state)


class StarvationStopJoins(unittest.TestCase):
    def setUp(self):
        sg.stop(); sg._state.clear(); self.addCleanup(sg.stop)

    def test_stop_waits_for_loop_thread(self):
        with mock.patch.object(sg, "_INTERVAL", 0.05), mock.patch.object(sg, "_tick"):
            t = sg.start()
            time.sleep(0.12)
            self.assertTrue(t.is_alive())
            sg.stop(join_s=3.0)
            self.assertFalse(t.is_alive(), "stop() 뒤에도 감시 스레드가 살아 있다")
            self.assertIsNone(sg._thread)

    def test_restart_refused_while_stopping(self):
        calls = []
        fake_mgr = mock.Mock(stop=mock.Mock(side_effect=lambda cid: calls.append(("stop", cid)) or {"ok": True}),
                             remove=mock.Mock(side_effect=lambda cid: calls.append(("remove", cid))))
        with mock.patch("worker.manager", fake_mgr), mock.patch("camera_registry.get", return_value={"id": "c1"}), \
             mock.patch("routers.cameras._start", side_effect=lambda cid: calls.append(("start", cid))):
            sg._stop.set()
            self.assertFalse(sg._restart_worker("c1"))
        self.assertEqual(calls, [], "종료 중인데 워커를 멈추거나 되살렸다")

    def test_restart_still_works_when_not_stopping(self):
        calls = []
        fake_mgr = mock.Mock(stop=mock.Mock(side_effect=lambda cid: calls.append(("stop", cid)) or {"ok": True}))
        with mock.patch("worker.manager", fake_mgr), mock.patch("camera_registry.get", return_value={"id": "c1"}), \
             mock.patch("routers.cameras._start", side_effect=lambda cid: calls.append(("start", cid))), \
             mock.patch.object(sg.time, "sleep"):
            sg._stop.clear()
            sg._restart_worker("c1")
        self.assertEqual(calls, [("stop", "c1"), ("start", "c1")])


class ClosingFlagOnlyOnShutdown(unittest.TestCase):
    def test_stop_all_then_start_is_allowed_but_shutdown_blocks(self):
        m = worker.WorkerManager()
        m._workers = {"a": _FakeWorker(), "b": _FakeWorker()}
        r = m.stop_all(deadline_s=2.0)
        self.assertTrue(r["ok"]); self.assertFalse(m.is_closing(), "운영 중 전체 정지는 종료 깃발을 세우면 안 된다")
        with mock.patch.object(worker.Worker, "start", return_value={"ok": True, "started": True}):
            self.assertTrue(m.start(None, threading.RLock(), "a", "src")["ok"], "운영 중 전체 정지 뒤 재시작이 돼야 한다")
        m.begin_shutdown()
        self.assertTrue(m.is_closing())
        with mock.patch.object(worker.Worker, "start", return_value={"ok": True}) as ws:
            r2 = m.start(None, threading.RLock(), "c", "src")
            self.assertFalse(r2["ok"]); self.assertIn("종료 중", r2["error"]); ws.assert_not_called()
        m.reopen(); self.assertFalse(m.is_closing())


class ShutdownOrder(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts())

    def test_starvation_stopped_and_flag_set_before_workers(self):
        import main
        order: list[str] = []
        with mock.patch("readiness.stop", side_effect=lambda *a, **k: order.append("warmup_cancel")), \
             mock.patch("starvation_guard.stop", side_effect=lambda *a, **k: order.append("starvation_stop")), \
             mock.patch("worker.manager.begin_shutdown", side_effect=lambda: order.append("flag")), \
             mock.patch("worker.manager.stop_all", side_effect=lambda: order.append("workers") or {"ok": True}), \
             mock.patch.object(main._cameras_router, "stop_go2rtc", side_effect=lambda: order.append("go2rtc")), \
             mock.patch("relay.status", return_value={"enabled": False}), \
             mock.patch("alert_notify.stop"), mock.patch("alert_queue.stop"), mock.patch("retention_scheduler.stop"), mock.patch("health_watch.stop"):
            main._shutdown()
        self.assertEqual(order[:5], ["warmup_cancel", "starvation_stop", "flag", "workers", "go2rtc"], order)


class WarmupCancelOnShutdown(unittest.TestCase):
    """[A-8] 예열 중 종료 → on_ready 미호출. 예열은 mock(release 이벤트를 기다림)으로 12 s 모델 로드를 흉내 낸다."""

    def setUp(self):
        readiness.stop(join_s=1.0); readiness._cancel.clear(); readiness._thread = None
        self.addCleanup(lambda: (readiness.stop(join_s=1.0), readiness._cancel.clear()))

    def _start(self, release: threading.Event, on_ready):
        with mock.patch.object(readiness, "warmup", side_effect=lambda *a, **k: (release.wait(3.0), {"ok": True})[1]):
            t = readiness.start_background(object(), on_ready=on_ready)
            t.join(0.05)                               # warmup mock 호출이 patch 범위 안에서 시작되게 잠깐 양보
        return t

    def test_stop_during_warmup_skips_on_ready(self):
        release, called = threading.Event(), []
        t = self._start(release, lambda: called.append(1))
        self.assertIs(readiness._thread, t, "예열 스레드를 기억해야 stop 이 기다릴 수 있다")
        readiness.stop(join_s=0.2)                     # 모델 로드 중 → 시간 초과, 깃발만 남음
        self.assertTrue(readiness._cancel.is_set())
        release.set(); t.join(3.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(called, [], "종료 중인데 예열 콜백(워커 자동기동)이 호출됐다")

    def test_stop_joins_finished_thread_and_clears(self):
        release, called = threading.Event(), []
        release.set()
        t = self._start(release, lambda: called.append(1))
        t.join(3.0)
        self.assertEqual(called, [1], "평시 예열 완료 뒤 on_ready 가 호출돼야 한다")
        readiness.stop(join_s=1.0)
        self.assertIsNone(readiness._thread)

    def test_restart_after_cancel_calls_on_ready(self):
        release, called = threading.Event(), []
        release.set()
        readiness.stop(join_s=0.1)                     # 이전 종료가 남긴 취소 깃발
        self.assertTrue(readiness._cancel.is_set())
        t = self._start(release, lambda: called.append(1))   # 새 기동 → 깃발 해제
        t.join(3.0)
        self.assertEqual(called, [1], "다시 기동했는데 이전 취소 깃발이 on_ready 를 막았다")


def _hw_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "vigent-health-watch" and t.is_alive()]


class HealthWatchSingleThread(unittest.TestCase):
    """[A-6] 감시 스레드는 언제나 1개 — 재현: 판정(check_once)이 막힌 채 stop 시간 초과 → start."""

    def setUp(self):
        self.addCleanup(isolate_alerts())
        hw.reset_for_test(); self.addCleanup(hw.reset_for_test)
        self.assertEqual(_hw_threads(), [], "이전 테스트가 감시 스레드를 남겼다")
        ps = [mock.patch.object(hw, "enabled", return_value=True),
              mock.patch.object(hw, "_cfg", side_effect=lambda k, d: {"watch_interval_s": 0.02, "startup_grace_s": 0.0}.get(k, d)),
              mock.patch.object(hw, "_ready", return_value=True)]
        for p in ps:
            p.start(); self.addCleanup(p.stop)

    def test_stop_start_repeated_keeps_one_thread(self):
        with mock.patch.object(hw, "check_once"):
            for _ in range(5):
                hw.start(); time.sleep(0.05); hw.stop(join_s=2.0)
                self.assertEqual(len(_hw_threads()), 0)
            hw.start(); hw.start(); time.sleep(0.05)
            self.assertEqual(len(_hw_threads()), 1, "start 두 번에 스레드가 둘 떴다")
            hw.stop(join_s=2.0)

    def test_timed_out_stop_keeps_reference_and_start_refuses_second_thread(self):
        release = threading.Event()
        with mock.patch.object(hw, "check_once", side_effect=lambda *a, **k: release.wait(3.0)):
            hw.start(); time.sleep(0.08)               # 첫 판정에 들어가 막힘
            old = hw._thread; old_ev = hw._stop
            self.assertIsNotNone(old); self.assertTrue(old.is_alive())
            hw.stop(join_s=0.1)                        # 시간 초과
            self.assertIs(hw._thread, old, "시간 초과 뒤 참조를 버리면 다음 start 가 하나 더 띄운다")
            self.assertTrue(old_ev.is_set())
            hw.start()                                 # 옛 스레드가 살아 있으면 거부
            self.assertEqual(len(_hw_threads()), 1, "옛 스레드가 살아 있는데 두 번째 감시 스레드가 떴다")
            self.assertTrue(old_ev.is_set(), "새 start 가 옛 스레드의 깃발을 지웠다(되살림)")
            release.set(); old.join(3.0)
            self.assertFalse(old.is_alive())
        with mock.patch.object(hw, "check_once"):
            hw.start(); time.sleep(0.05)               # 옛 스레드가 끝난 뒤의 start 는 정상
            self.assertEqual(len(_hw_threads()), 1)
            self.assertIsNot(hw._thread, old); self.assertIsNot(hw._stop, old_ev, "깃발은 스레드마다 따로")
            hw.stop(join_s=2.0)
            self.assertEqual(_hw_threads(), [])


class StartupReopensBeforeWorkers(unittest.TestCase):
    """[A-7] 기동은 종료 깃발을 내리고, 그 시점이 예열(on_ready→autostart)·기아 감시보다 앞이다.
    실측(2026-10-11): TestClient(with) 로 lifespan 을 도는 테스트 6파일이 진짜 _shutdown 으로 깃발을 남겨 뒤 모듈의 start 가 거부됐다."""

    def setUp(self):
        import app_state
        self.addCleanup(isolate_alerts())
        app_state.STARTUP_WARNINGS.clear()
        self.addCleanup(worker.manager.reopen)

    def _run_startup(self, order):
        import alert_notify
        import alert_queue
        import main
        import readiness
        import retention_scheduler

        class _Cfg:
            display_name = "t"

            def summary(self):
                return {"fallback_count": 0, "disabled_count": 0}
        bundle = {"config": _Cfg(), "agents": {"Guard": object(), "Dispatcher": mock.Mock()}}
        with mock.patch.object(main, "_load_theme", return_value=bundle), mock.patch.object(main, "_install_safety_nets"),              mock.patch.object(main._cameras_router, "ensure_go2rtc", return_value=False),              mock.patch.object(main._cameras_router, "autostart_enabled", return_value=[]),              mock.patch.object(readiness, "start_background", side_effect=lambda *a, **k: order.append("warmup")),              mock.patch.object(sg, "start", side_effect=lambda: order.append("starvation")),              mock.patch.object(worker.manager, "reopen", side_effect=lambda: order.append("reopen")),              mock.patch.object(worker.manager, "autostart", side_effect=lambda *a, **k: order.append("autostart") or {"ok": True}),              mock.patch.object(alert_queue, "start"), mock.patch.object(alert_notify, "start"), mock.patch.object(retention_scheduler, "start"),              mock.patch.object(main, "_notify_startup_failure"), mock.patch("health_watch.start"):
            main._startup()

    def test_reopen_comes_first(self):
        order: list[str] = []
        self._run_startup(order)
        self.assertIn("reopen", order); self.assertIn("warmup", order); self.assertIn("starvation", order)
        self.assertEqual(order.index("reopen"), 0, order)
        self.assertLess(order.index("reopen"), order.index("warmup"), order)
        self.assertLess(order.index("reopen"), order.index("starvation"), order)

    def test_start_allowed_again_after_shutdown_then_startup(self):
        """종료(begin_shutdown) 뒤 다시 기동하면 start 가 거부되지 않는다 — lifespan 을 두 번 도는 테스트 프로세스가 바로 이 경우."""
        import main
        worker.manager.begin_shutdown()
        self.assertFalse(worker.manager.start(None, threading.RLock(), "x", "src")["ok"])
        self._run_startup([])          # reopen 은 mock 이라 깃발이 안 내려감 — 진짜 reopen 으로 확인
        worker.manager.reopen()
        self.assertFalse(worker.manager.is_closing())
        with mock.patch.object(worker.Worker, "start", return_value={"ok": True, "started": True}):
            self.assertTrue(worker.manager.start(None, threading.RLock(), "x", "src")["ok"])
        worker.manager.remove("x")
        # 진짜 _startup 경로(mock 없이 reopen)가 깃발을 내리는지는 아래에서 다시 확인
        worker.manager.begin_shutdown()
        with mock.patch.object(main, "_load_theme", side_effect=RuntimeError("stop here")),              mock.patch.object(main, "_install_safety_nets"), mock.patch.object(main, "_notify_startup_failure"):
            with self.assertRaises(RuntimeError):
                main._startup()
        self.assertFalse(worker.manager.is_closing(), "기동 첫머리(테마 로드 전)에서 깃발이 내려가야 한다")


class EveryRealShutdownTestRestoresFlag(unittest.TestCase):
    """[A-7] 진짜 `main._shutdown()` 을 돌리는 테스트 파일은 전역 '종료 중' 깃발을 원복해야 한다 — 정적 검사.
    실측(2026-10-11): test_audit_fix2_relay·test_field_fixes_20260928 이 깃발을 남겨 뒤 모듈(test_constants_config)의 start 가 거부됐다.
    TestClient 를 with/__enter__ 로 여는 파일도 lifespan 종료로 진짜 _shutdown 을 돌리므로 같은 대상(6파일, 2026-10-11 실측).
    허용: isolate_shutdown() 사용, 또는 worker.manager 전체 mock(`mock.patch("worker.manager")`), 또는 begin_shutdown 을 mock."""

    def test_static_scan(self):
        bad = []
        for f in sorted((_ROOT / "tests").glob("test_*.py")):
            s = f.read_text(encoding="utf-8", errors="replace")
            tc = "TestClient(" + "main.app)"      # 이 파일 자체가 토큰 정적 검사(test_token_isolation)에 걸리지 않게 문자열을 나눈다
            enters_lifespan = ("with " + tc) in s or (tc in s and ".__enter__()" in s)
            if "main._shutdown()" not in s and not enters_lifespan:
                continue
            if "isolate_shutdown" in s or 'mock.patch("worker.manager")' in s or "begin_shutdown" in s:
                continue
            bad.append(f.name)
        self.assertEqual(bad, [], "진짜 _shutdown 을 돌리면서 종료 깃발을 원복하지 않는다 — setUp 에 isolate_shutdown() 을 넣을 것")


if __name__ == "__main__":
    unittest.main()
