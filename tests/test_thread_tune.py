"""[2026-11-16] PyTorch·OpenCV CPU 스레드 헛돌기 제한(thread_tune) — 사용자 결정으로 기본 on.

배경(이 노트북 실측, benchmarks/cpu_thread_limit_20261116.md): 검출은 GPU 가 하는데 PyTorch CPU 스레드 6개·OpenCV 스레드 12개가
대기하며 CPU 를 썼다(서버 구성 전체 약 72~77%). 스레드 수만 줄이니 약 25% 가 됐고 검출 결과는 완전히 같았다.

고정하는 것:
  ① 설정 해석: threads.torch = auto | 0 | N, threads.opencv = 0 | N. 이상한 값은 기본값(auto / 2). 환경변수가 우선.
     코드 기본값 = config/tuning.yaml 값. 학원 프로파일은 0(끔 — 그 기계에서 잰 뒤 켠다).
  ② ★auto 는 **cuda 일 때만** 1개로 줄인다. cpu·mps 는 건드리지 않는다 — CPU 로 검출하는 기계에서 1개면 검출이 약 2배 느려진다(실측).
     숫자를 직접 적으면 장치와 무관하게 그 수.
  ③ apply_*: 필요한 때만 건다(이미 그 수면 다시 걸지 않음) · 건 뒤 **다시 읽어 확인**(안 걸렸으면 applied=False + 경고) ·
     어떤 예외도 올리지 않는다(검출·기동을 막지 않는다) · 0 이면 손대지 않는다.
  ④ 배선: 검출 어댑터가 모델을 올린 장치로 apply_torch 를 부른다 · 서버 기동이 apply_opencv 를 부른다(실패해도 기동은 계속, 경고로 남음) ·
     /health 가 cpu_threads 로 상태를 보여 준다.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import thread_tune  # noqa: E402
import tuning  # noqa: E402
from _isolate import isolate_alerts  # noqa: E402


class _FakeTorch:
    """torch 대역 — 스레드 수를 기억한다. stuck=True 면 set 이 먹지 않는다(걸었는데 안 걸린 경우)."""

    def __init__(self, n: int = 6, stuck: bool = False, boom: bool = False):
        self.n, self.stuck, self.boom = n, stuck, boom
        self.set_calls: list[int] = []

    def get_num_threads(self):
        if self.boom:
            raise RuntimeError("torch 고장")
        return self.n

    def set_num_threads(self, n):
        self.set_calls.append(n)
        if not self.stuck:
            self.n = n


class _FakeCv2:
    def __init__(self, n: int = 12, stuck: bool = False, boom: bool = False):
        self.n, self.stuck, self.boom = n, stuck, boom
        self.set_calls: list[int] = []

    def getNumThreads(self):
        if self.boom:
            raise RuntimeError("cv2 고장")
        return self.n

    def setNumThreads(self, n):
        self.set_calls.append(n)
        if not self.stuck:
            self.n = n


def _setting(torch_value=thread_tune.DEFAULT_TORCH, opencv_value=thread_tune.DEFAULT_OPENCV):
    """설정 파일·환경변수와 무관하게 두 값을 고정한다."""
    def _val(sec, key, default, env=None):
        return {("threads", "torch"): torch_value, ("threads", "opencv"): opencv_value}.get((sec, key), default)
    return mock.patch.object(thread_tune.tuning, "val", side_effect=_val)


class _StateCase(unittest.TestCase):
    """thread_tune 의 전역 상태를 테스트마다 원복한다."""

    def setUp(self):
        saved = {k: dict(v) for k, v in thread_tune._state.items()}

        def _restore():
            thread_tune._state.clear(); thread_tune._state.update(saved)
        self.addCleanup(_restore)


class ParseTest(unittest.TestCase):
    def test_torch_values(self):
        for raw, want in (("auto", "auto"), (" AUTO ", "auto"), (0, 0), ("0", 0), (1, 1), ("3", 3), (4.0, 4),
                          (False, 0), ("off", 0), (True, "auto")):
            with _setting(torch_value=raw):
                self.assertEqual(thread_tune.torch_setting(), want, f"{raw!r}")

    def test_torch_invalid_falls_back_to_auto(self):
        for raw in ("abc", -1, float("nan"), float("inf"), 10**6, [1], {"a": 1}, ""):
            with _setting(torch_value=raw):
                self.assertEqual(thread_tune.torch_setting(), "auto", f"{raw!r}")

    def test_opencv_values(self):
        for raw, want in ((2, 2), ("2", 2), (0, 0), (1, 1), (8.0, 8), (False, 0), (True, 2)):
            with _setting(opencv_value=raw):
                self.assertEqual(thread_tune.opencv_setting(), want, f"{raw!r}")

    def test_opencv_invalid_falls_back_to_default(self):
        for raw in ("auto", "x", -3, float("nan"), 10**6, [2]):
            with _setting(opencv_value=raw):
                self.assertEqual(thread_tune.opencv_setting(), thread_tune.DEFAULT_OPENCV, f"{raw!r}")

    def test_reads_documented_keys_and_env_names(self):
        seen = []

        def _val(sec, key, default, env=None):
            seen.append((sec, key, env))
            return default
        with mock.patch.object(thread_tune.tuning, "val", side_effect=_val):
            thread_tune.torch_setting(); thread_tune.opencv_setting()
        self.assertEqual(seen, [("threads", "torch", "VIGENT_TORCH_THREADS"), ("threads", "opencv", "VIGENT_OPENCV_THREADS")])

    def test_env_overrides_file(self):
        with mock.patch.dict(os.environ, {"VIGENT_TORCH_THREADS": "0", "VIGENT_OPENCV_THREADS": "1"}):
            self.assertEqual(thread_tune.torch_setting(), 0)
            self.assertEqual(thread_tune.opencv_setting(), 1)
        with mock.patch.dict(os.environ, {"VIGENT_TORCH_THREADS": "4", "VIGENT_OPENCV_THREADS": "oops"}):
            self.assertEqual(thread_tune.torch_setting(), 4)
            self.assertEqual(thread_tune.opencv_setting(), thread_tune.DEFAULT_OPENCV)

    def test_code_defaults_match_global_tuning_yaml(self):
        """코드 기본값과 설정 파일 값이 다르면, 파일에서 키가 빠질 때 조용히 다른 동작이 된다."""
        th = tuning.load_file(_ROOT / "config" / "tuning.yaml").get("threads") or {}
        self.assertEqual(th.get("torch"), thread_tune.DEFAULT_TORCH)
        self.assertEqual(th.get("opencv"), thread_tune.DEFAULT_OPENCV)

    def test_academy_profile_keeps_it_off_until_measured_there(self):
        th = tuning.load_file(_ROOT / "deploy" / "academy" / "tuning.academy.yaml").get("threads") or {}
        self.assertEqual((th.get("torch"), th.get("opencv")), (0, 0),
                         "학원 프로파일은 그 기계에서 잰 뒤에 켠다(deploy/academy/profile_intent.yaml)")


class TargetTest(unittest.TestCase):
    def test_auto_limits_only_on_cuda(self):
        with _setting(torch_value="auto"):
            for dev in ("cuda", "cuda:0", "CUDA", " cuda "):
                self.assertEqual(thread_tune.torch_target(dev), 1, dev)
            for dev in ("cpu", "mps", "", None, "onnx-cpu"):
                self.assertEqual(thread_tune.torch_target(dev), 0,
                                 f"{dev!r} — cuda 가 아닌 장치는 건드리면 안 된다(CPU 검출은 스레드 1개면 약 2배 느림)")

    def test_explicit_number_applies_on_any_device(self):
        with _setting(torch_value=3):
            self.assertEqual(thread_tune.torch_target("cpu"), 3)
            self.assertEqual(thread_tune.torch_target("cuda"), 3)

    def test_zero_never_limits(self):
        with _setting(torch_value=0):
            self.assertEqual(thread_tune.torch_target("cuda"), 0)


class ApplyTorchTest(_StateCase):
    def _run(self, fake, device, **setting):
        with _setting(**setting), mock.patch.object(thread_tune, "_torch", return_value=fake):
            return thread_tune.apply_torch(device)

    def test_cuda_auto_limits_to_one_and_verifies(self):
        fake = _FakeTorch(6)
        st = self._run(fake, "cuda")
        self.assertEqual(fake.set_calls, [1])
        self.assertEqual((st["applied"], st["before"], st["after"], st["setting"], st["device"]), (True, 6, 1, "auto", "cuda"))
        self.assertEqual(thread_tune.status()["torch"]["after"], 1)

    def test_called_per_slot_sets_only_once(self):
        fake = _FakeTorch(6)
        for _ in range(3):                                    # person·ppe·fire_smoke 슬롯마다 불린다
            st = self._run(fake, "cuda")
        self.assertEqual(fake.set_calls, [1], "이미 그 수인데 다시 걸었다")
        self.assertTrue(st["applied"])

    def test_cpu_device_is_left_alone(self):
        fake = _FakeTorch(6)
        st = self._run(fake, "cpu")
        self.assertEqual(fake.set_calls, [])
        self.assertEqual(fake.n, 6)
        self.assertFalse(st["applied"])
        self.assertIn("cpu", st["note"])

    def test_zero_setting_is_left_alone(self):
        fake = _FakeTorch(6)
        st = self._run(fake, "cuda", torch_value=0)
        self.assertEqual(fake.set_calls, [])
        self.assertFalse(st["applied"])

    def test_explicit_number(self):
        fake = _FakeTorch(6)
        st = self._run(fake, "cpu", torch_value=2)
        self.assertEqual(fake.set_calls, [2])
        self.assertEqual((st["applied"], st["after"]), (True, 2))

    def test_not_applied_is_reported_not_hidden(self):
        """걸었는데 안 걸렸다 — 성공으로 적지 않는다(규칙 11)."""
        fake = _FakeTorch(6, stuck=True)
        with self.assertLogs("vigent.thread_tune", level="WARNING"):
            st = self._run(fake, "cuda")
        self.assertFalse(st["applied"])
        self.assertEqual((st["before"], st["after"]), (6, 6))
        self.assertIn("요청 1개", st["note"])

    def test_exception_never_propagates(self):
        with self.assertLogs("vigent.thread_tune", level="WARNING"):
            st = self._run(_FakeTorch(boom=True), "cuda")
        self.assertFalse(st["applied"])
        self.assertIn("적용 실패", st["note"])


class ApplyOpencvTest(_StateCase):
    def _run(self, fake, **setting):
        with _setting(**setting), mock.patch.object(thread_tune, "_cv2", return_value=fake):
            return thread_tune.apply_opencv()

    def test_default_limits_to_two_and_verifies(self):
        fake = _FakeCv2(12)
        st = self._run(fake)
        self.assertEqual(fake.set_calls, [2])
        self.assertEqual((st["applied"], st["before"], st["after"], st["setting"]), (True, 12, 2, 2))
        self.assertEqual(thread_tune.status()["opencv"]["after"], 2)

    def test_already_at_target_is_not_set_again(self):
        fake = _FakeCv2(2)
        st = self._run(fake)
        self.assertEqual(fake.set_calls, [])
        self.assertTrue(st["applied"])

    def test_zero_setting_is_left_alone(self):
        fake = _FakeCv2(12)
        st = self._run(fake, opencv_value=0)
        self.assertEqual(fake.set_calls, [])
        self.assertEqual(fake.n, 12)
        self.assertFalse(st["applied"])

    def test_not_applied_is_reported_not_hidden(self):
        with self.assertLogs("vigent.thread_tune", level="WARNING"):
            st = self._run(_FakeCv2(12, stuck=True))
        self.assertFalse(st["applied"])
        self.assertIn("요청 2개", st["note"])

    def test_exception_never_propagates(self):
        with self.assertLogs("vigent.thread_tune", level="WARNING"):
            st = self._run(_FakeCv2(boom=True))
        self.assertFalse(st["applied"])


class _FakeNano:
    """rfdetr.RFDETRNano 대역 — 어댑터 생성자가 쓰는 것만."""
    class _Cfg:
        resolution = 320
    model_config = _Cfg()
    class_names: list = []

    def __init__(self, **kw):
        self.kw = kw

    def optimize_for_inference(self):
        return None

    def predict(self, image, threshold=0.5):
        return None


class DetectorWiringTest(_StateCase):
    """검출 어댑터가 '모델이 올라간 장치'로 apply_torch 를 부른다 — 장치를 아는 곳이 거기라서."""

    def _build(self, device, apply):
        import device as device_mod
        from detectors.rfdetr_adapter import RfdetrDetector
        fake_rfdetr = types.ModuleType("rfdetr")
        fake_rfdetr.RFDETRNano = _FakeNano           # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"rfdetr": fake_rfdetr}), \
                mock.patch.object(device_mod, "pick_device", return_value=device), \
                mock.patch.dict(os.environ, {"VIGENT_DETECT_BACKEND": "torch"}), \
                mock.patch.object(thread_tune, "apply_torch", side_effect=apply) as ap:
            det = RfdetrDetector("", {}, set(), resolution=320)
        return det, ap

    def test_calls_apply_torch_with_cuda(self):
        det, ap = self._build("cuda", lambda dev: {})
        ap.assert_called_once_with("cuda")
        self.assertEqual(det.device, "cuda")

    def test_calls_apply_torch_with_cpu_so_auto_can_skip(self):
        _det, ap = self._build("cpu", lambda dev: {})
        ap.assert_called_once_with("cpu")

    def test_apply_failure_does_not_break_model_load(self):
        def _boom(dev):
            raise RuntimeError("thread_tune 고장")
        det, ap = self._build("cuda", _boom)
        ap.assert_called_once()
        self.assertEqual(det.device, "cuda", "스레드 제한 실패가 모델 로드를 막았다")


class StartupAndHealthWiringTest(_StateCase):
    def setUp(self):
        super().setUp()
        import alert_notify
        import alert_queue
        import app_state
        import main
        import readiness
        import retention_scheduler
        import starvation_guard
        self.main, self.app_state = main, app_state
        self.addCleanup(isolate_alerts())
        app_state.STARTUP_WARNINGS.clear()
        self.addCleanup(app_state.STARTUP_WARNINGS.clear)

        class _Cfg:
            display_name = "t"

            def summary(self):
                return {"fallback_count": 0, "disabled_count": 0}

        class _Disp:
            def channels_configured(self):
                return False

            def dispatch(self, *a, **k):
                return {}

            def _dispatch_now(self, *a, **k):
                return {}
        for p in (mock.patch.object(main, "_load_theme", return_value={"config": _Cfg(), "agents": {"Guard": object(), "Dispatcher": _Disp()}}),
                  mock.patch.object(main, "_install_safety_nets"),
                  mock.patch.object(main._cameras_router, "ensure_go2rtc", return_value=False),
                  mock.patch.object(readiness, "start_background"),
                  mock.patch.object(starvation_guard, "start"),
                  mock.patch.object(alert_queue, "start"),
                  mock.patch.object(alert_notify, "start"),
                  mock.patch.object(retention_scheduler, "start"),
                  mock.patch.object(main, "_notify_startup_failure"),
                  mock.patch.object(main._cameras_router, "autostart_enabled", return_value=[])):
            p.start(); self.addCleanup(p.stop)

    def test_startup_applies_opencv_limit_once(self):
        with mock.patch.object(thread_tune, "apply_opencv", return_value={}) as ap:
            self.main._startup()
        ap.assert_called_once_with()
        self.assertEqual(self.app_state.STARTUP_WARNINGS, [])

    def test_startup_survives_failure_and_leaves_a_warning(self):
        with mock.patch.object(thread_tune, "apply_opencv", side_effect=RuntimeError("cv2 고장")):
            self.main._startup()                              # 예외가 올라오면 안 된다(선택 서비스)
        self.assertTrue(any(w.startswith("startup:thread_tune:") for w in self.app_state.STARTUP_WARNINGS),
                        self.app_state.STARTUP_WARNINGS)

    def test_health_shows_cpu_threads(self):
        from fastapi.testclient import TestClient
        with _setting(), mock.patch.object(thread_tune, "_torch", return_value=_FakeTorch(6)), \
                mock.patch.object(thread_tune, "_cv2", return_value=_FakeCv2(12)):
            thread_tune.apply_torch("cuda"); thread_tune.apply_opencv()
        saved = self.main._API_TOKEN
        self.main._API_TOKEN = ""
        try:
            body = TestClient(self.main.app).get("/health").json()
        finally:
            self.main._API_TOKEN = saved
        ct = body["cpu_threads"]
        self.assertEqual((ct["torch"]["applied"], ct["torch"]["after"], ct["torch"]["device"]), (True, 1, "cuda"))
        self.assertEqual((ct["opencv"]["applied"], ct["opencv"]["after"]), (True, 2))


if __name__ == "__main__":
    unittest.main()
