"""[2026-11-16 rfdetr 1.8.0 → 1.12.1] detectors.rfdetr_compat — 버전 차이를 조용히 넘기지 않는다.

실측 배경(같은 65장, GTX 1650 Ti): 새 버전을 예전 코드 그대로 돌리면
  · `optimize_for_inference()` 가 없어 try/except 로 넘어가 **최적화 없이** 돌고,
  · predict 안티앨리어싱이 기본 꺼짐이라 NO-Hardhat 0.81→0.30, person 0.48→0.15 처럼 **운용 임계 아래로 사라졌다**.
두 가지를 적용하면 1.8.0 과 운용 임계 박스 101개 전부 일치·신뢰도 차 0.000.

고정하는 것(가짜 모델 — 라이브러리·GPU 없이):
  ① 새 API(inference)가 있으면 예측기로 **교체**하고 torchscript 로 컴파일한다. 예전 API 만 있으면 제자리 최적화.
  ② 최적화가 실패해도 예외를 올리지 않고 원본 + "none" — 단 WARNING 을 남긴다(조용한 미최적화 금지).
  ③ predict 에 antialias 인자가 있으면 True 를 넘긴다. 없으면(예전 버전·ONNX 래퍼) 아무것도 넘기지 않는다.
  ④ 서버 실행 경로(rfdetr_adapter·rfdetr_service)는 삭제된 함수를 직접 부르지 않고 이 모듈을 쓴다(정적).
  ⑤ 설치된 rfdetr 가 requirements.txt 핀과 같다.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core"))

from detectors import rfdetr_compat as C  # noqa: E402


class _Predictor:
    class_names = ["a"]

    def predict(self, images, threshold=0.5, *, antialias=None, include_source_image=True):
        return (antialias, include_source_image)


class _NewModel:
    """rfdetr ≥ 1.12 모양: inference(compile, batch_size, *, compile_backend) → 독립 예측기."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def inference(self, compile=True, batch_size=1, *, compile_backend=None):
        self.calls.append({"compile": compile, "batch_size": batch_size, "compile_backend": compile_backend})
        if self.fail:
            raise RuntimeError("compile failed")
        return _Predictor()

    def predict(self, images, threshold=0.5, *, antialias=False, **kwargs):
        return antialias


class _Model111:
    """rfdetr 1.11 모양: inference 는 있으나 compile_backend 인자가 없다."""

    def __init__(self):
        self.calls = []

    def inference(self, compile=True, batch_size=1):
        self.calls.append({"compile": compile, "batch_size": batch_size})
        return _Predictor()


class _LegacyModel:
    """rfdetr ≤ 1.10 모양: 제자리 최적화만 있고 predict 에 antialias 가 없다."""

    def __init__(self, fail=False):
        self.fail, self.optimized = fail, False

    def optimize_for_inference(self):
        if self.fail:
            raise RuntimeError("trace failed")
        self.optimized = True

    def predict(self, images, threshold=0.5):
        return None


class OptimizeTest(unittest.TestCase):
    def test_new_api_swaps_to_predictor_compiled_with_torchscript(self):
        m = _NewModel()
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("VIGENT_RFDETR_COMPILE", None)
            obj, how = C.optimize(m)
        self.assertIsInstance(obj, _Predictor, "원본이 아니라 예측기를 돌려줘야 한다")
        self.assertEqual(how, "inference:torchscript")
        self.assertEqual(m.calls, [{"compile": True, "batch_size": 1, "compile_backend": "torchscript"}])

    def test_env_overrides_compile_backend(self):
        m = _NewModel()
        with mock.patch.dict("os.environ", {"VIGENT_RFDETR_COMPILE": "CudaGraph "}):
            _, how = C.optimize(m)
        self.assertEqual(how, "inference:cudagraph")
        self.assertEqual(m.calls[0]["compile_backend"], "cudagraph")

    def test_version_without_compile_backend_param(self):
        m = _Model111()
        obj, how = C.optimize(m)
        self.assertIsInstance(obj, _Predictor)
        self.assertEqual(m.calls, [{"compile": True, "batch_size": 1}])
        self.assertTrue(how.startswith("inference:"))

    def test_new_api_failure_falls_back_loudly(self):
        m = _NewModel(fail=True)
        with self.assertLogs("vigent.rfdetr_adapter", level="WARNING") as cm:
            obj, how = C.optimize(m)
        self.assertIs(obj, m); self.assertEqual(how, "none")
        self.assertTrue(any("최적화 실패" in line for line in cm.output))

    def test_legacy_api_optimizes_in_place(self):
        m = _LegacyModel()
        obj, how = C.optimize(m)
        self.assertIs(obj, m); self.assertTrue(m.optimized); self.assertEqual(how, "legacy")

    def test_legacy_failure_falls_back_loudly(self):
        m = _LegacyModel(fail=True)
        with self.assertLogs("vigent.rfdetr_adapter", level="WARNING"):
            obj, how = C.optimize(m)
        self.assertIs(obj, m); self.assertEqual(how, "none")


class PredictKwargsTest(unittest.TestCase):
    def test_antialias_forced_on_when_supported(self):
        self.assertEqual(C.predict_kwargs(_NewModel()), {"antialias": True})
        self.assertEqual(C.predict_kwargs(_Predictor()), {"antialias": True, "include_source_image": False})

    def test_nothing_for_versions_without_the_parameter(self):
        self.assertEqual(C.predict_kwargs(_LegacyModel()), {})

    def test_onnx_wrapper_gets_nothing(self):
        from detectors.rfdetr_adapter import _OnnxRfdetrModel
        self.assertEqual(C.predict_kwargs(_OnnxRfdetrModel.__new__(_OnnxRfdetrModel)), {},
                         "ONNX 래퍼는 안티앨리어싱을 직접 한다 — 인자를 넘기면 안 된다")

    def test_uninspectable_predict_is_tolerated(self):
        class _Weird:
            predict = 5
        self.assertEqual(C.predict_kwargs(_Weird()), {})


class ServingPathUsesCompat(unittest.TestCase):
    def test_no_direct_optimize_call_in_serving_path(self):
        for rel in ("detectors/rfdetr_adapter.py", "rfdetr_service.py"):
            src = (_ROOT / "vigent-core" / rel).read_text(encoding="utf-8")
            code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
            code = re.sub(r'""".*?"""', "", code, flags=re.S)
            self.assertNotIn(".optimize_for_inference(", code, f"{rel}: 삭제된 함수를 직접 부른다(rfdetr_compat.optimize 를 쓸 것)")
            self.assertIn("rfdetr_compat", src, rel)
            self.assertIn("_predict_kwargs", src, f"{rel}: predict 에 안티앨리어싱 인자를 넘기지 않는다")

    def test_installed_rfdetr_matches_pin(self):
        from importlib.metadata import PackageNotFoundError, version
        req = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
        m = re.search(r"^rfdetr==([0-9.]+)", req, flags=re.M)
        self.assertIsNotNone(m, "requirements.txt 에 rfdetr 핀이 없다")
        try:
            installed = version("rfdetr")
        except PackageNotFoundError:
            self.skipTest("rfdetr 미설치 환경")
        self.assertEqual(installed, m.group(1), "설치본과 requirements.txt 핀이 다르다 — 한쪽만 바뀌었다")


if __name__ == "__main__":
    unittest.main()
