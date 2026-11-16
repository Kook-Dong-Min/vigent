"""detectors.rfdetr_compat — rfdetr 버전 차이를 한 곳에서 흡수한다(서버 실행 경로 공용).

[2026-11-16 rfdetr 1.8.0 → 1.12.1] 버전을 올리며 실측으로 확인한 두 가지 변화. 둘 다 **조용히** 결과·속도를 바꾼다:

  ① 추론 최적화: 1.11.0 에서 `optimize_for_inference()` 가 삭제됐다. 예전 호출부는 `try: ... except: pass` 라 예외 없이
     넘어가고 **최적화 없이** 돈다(라이브러리 경고 "Model is not optimized for inference" 만 남는다).
     → `inference()` 가 돌려주는 독립 예측기로 교체(optimize).
  ② 입력 전처리: 1.12.0 에서 `predict()` 의 축소 보간 안티앨리어싱이 **기본 꺼짐**이 됐다. 가중치는 안티앨리어싱을 켠 입력
     (1.8.0 기본)으로 학습·튜닝됐다 — 끈 채로 돌리면 같은 사진에서 신뢰도가 크게 달라진다
     (실측 65장: NO-Hardhat 0.81→0.30, person 0.48→0.15 등 운용 임계 아래로 사라짐).
     → `predict(antialias=True)` 를 항상 넘긴다(predict_kwargs).

  두 가지를 적용한 뒤 1.8.0 과 비교(같은 65장, 해상도 320, GTX 1650 Ti): 운용 임계 박스 101개 전부 일치·신뢰도 차 0.000,
  슬롯당 추론 중앙값 약 35 → 24 ms.

★이 모듈을 쓰지 않고 rfdetr 모델을 직접 부르는 스크립트(benchmarks/·scripts/eval/·training/·tools/)는 1.11 이상에서
  ①② 가 적용되지 않는다 — 그 상태로 잰 수치는 서버 실행 경로와 다르다.
"""
from __future__ import annotations

import inspect
import logging
import os
from typing import Any

_LOG = logging.getLogger("vigent.rfdetr_adapter")

# 컴파일 방식: 예전(1.8.0) 기본과 같은 torchscript 로 고정. 1.12.0 의 새 CUDA 기본값(cudagraph)은 이 프로젝트에서 검증한 적이 없다.
DEFAULT_COMPILE_BACKEND = "torchscript"


def optimize(model: Any) -> tuple[Any, str]:
    """추론 최적화(고정 해상도 컴파일). 반환: (이후 predict 를 부를 객체, 적용된 방식 이름).

    · rfdetr ≥ 1.11: `model.inference(...)` 의 독립 예측기를 돌려준다. 예측기는 가중치 사본을 따로 가지므로 호출자는
      **원본 모델 참조를 버려야** GPU 메모리를 두 배로 쓰지 않는다(class_names·model_config 는 그 전에 읽어 둘 것).
    · rfdetr ≤ 1.10: 예전 방식 `optimize_for_inference()`(제자리 최적화) — 같은 객체를 돌려준다.
    · 실패: 원본 그대로 + "none"(추론은 되지만 느릴 수 있다 — WARNING 로그).
    컴파일 방식은 환경변수 VIGENT_RFDETR_COMPILE 로 바꿀 수 있다(기본 torchscript)."""
    if hasattr(model, "inference"):
        backend = (os.environ.get("VIGENT_RFDETR_COMPILE") or DEFAULT_COMPILE_BACKEND).strip().lower()
        try:
            kw: dict[str, Any] = {"compile": True, "batch_size": 1}
            if "compile_backend" in inspect.signature(model.inference).parameters:
                kw["compile_backend"] = backend
            return model.inference(**kw), f"inference:{backend}"
        except Exception as ex:  # noqa: BLE001  최적화 실패해도 추론은 가능(원본 모델로)
            _LOG.warning("RF-DETR 추론 최적화 실패(%s: %s) — 최적화 없이 진행(느릴 수 있다)", type(ex).__name__, ex)
            return model, "none"
    try:
        model.optimize_for_inference()
        return model, "legacy"
    except Exception as ex:  # noqa: BLE001
        _LOG.warning("RF-DETR 추론 최적화 실패(%s: %s) — 최적화 없이 진행(느릴 수 있다)", type(ex).__name__, ex)
        return model, "none"


def predict_kwargs(model: Any) -> dict[str, Any]:
    """`model.predict(image, threshold=..., **predict_kwargs(model))` 에 넘길 추가 인자.
    인자가 없는 버전·ONNX 래퍼(_OnnxRfdetrModel — 안티앨리어싱을 직접 한다)에는 아무것도 넘기지 않는다."""
    out: dict[str, Any] = {}
    try:
        params = inspect.signature(model.predict).parameters
    except (TypeError, ValueError):
        return out
    if "antialias" in params:
        out["antialias"] = True
    if "include_source_image" in params:
        out["include_source_image"] = False      # 결과에 원본 RGB 를 붙이지 않는다(안 씀 — 복사 비용만)
    return out
