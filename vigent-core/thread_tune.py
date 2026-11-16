"""thread_tune.py — PyTorch·OpenCV CPU 스레드 헛돌기 제한. [2026-11-16, 사용자 결정으로 **기본 on**]

★왜: 검출은 GPU 가 하는데 PyTorch 의 CPU 스레드(인텔 OpenMP, 이 노트북 6개)와 OpenCV 의 스레드(Windows Concurrency, 12개)가
  일이 없을 때도 대기하며 CPU 를 썼다. onnxruntime 에서 같은 병을 고친 전례가 `ort_tune.py`(Q9/Q10)다.
  이 노트북(i7-10750H + GTX 1650 Ti) 실측 — `benchmarks/cpu_thread_limit_20261116.md`:
    · 검출 1슬롯을 초당 8회: 5.1코어 → 0.4코어(스레드 6 → 1), 검출 시간은 43 → 41 ms 로 같다
    · 얼굴 찾기(YuNet) 640×360 을 초당 30회: 4.7코어 → 1.1코어(스레드 12 → 2), 시간은 22 → 23 ms 로 같다
    · 서버 구성 전체(1920×1080·30장/초·박스 영상 시청 중, 전·후 3쌍 교대): CPU 58~66 % → 25 %, 검출 갱신 4.9~8.3 → 9.5~9.8 회/초,
      박스 영상 13~19 → 18~20 장/초. 허브를 닫은 상태는 52 % → 12.5 %. (측정 스크립트 기준 · 배경 부하 약 2코어분이 있던 상태)
    · 검출 결과는 스레드 수와 무관하게 **완전히 같다**(사진 65장 × 3슬롯 박스 4,851개, 신뢰도·좌표 차 0.0)
    · 실제 서버·브라우저·실카메라·카메라 여러 대로는 **미측정**

설정 — `config/tuning.yaml` `threads:` (서버 재시작 후 반영, 환경변수가 우선)
    torch:  auto   # auto = 검출 모델이 **GPU(cuda)** 에서 돌 때만 1개로. 0 = 건드리지 않음(예전 동작). 숫자 = 그 수로 고정.   VIGENT_TORCH_THREADS
    opencv: 2      # OpenCV 스레드 수. 0 = 건드리지 않음(예전 동작).                                                        VIGENT_OPENCV_THREADS

★`torch: auto` 가 장치를 보는 이유: **CPU 로 검출하는 기계**에서는 그 스레드들이 실제 추론을 한다. 이 노트북에서 장치를 cpu 로
  강제하고 재니 스레드 1개면 검출 1회가 167 → 355 ms(640×480)로 약 2배 느려졌다. 그래서 auto 는 cuda 일 때만 줄이고,
  그 밖(cpu·mps·onnx-cpu 백엔드)은 **재지 않았으므로 건드리지 않는다.** 그런 기계에서 줄이려면 그 기계에서 재고 숫자를 직접 적는다.
★OpenCV 스레드는 검출 입력에 영향이 없다(검출 전처리에서 쓰는 OpenCV 연산은 색 변환 하나이고 결과가 같다). 줄어드는 것은
  얼굴 찾기·크기 조절의 병렬도다 — 원본 크기 얼굴 찾기(증거 JPEG, 규칙당 30초에 1회)는 1920×1080 에서 190 → 약 230~270 ms 로 느려진다.

원칙: 적용 실패가 검출·기동을 막지 않는다(예외를 올리지 않는다). 대신 **적용됐는지 다시 읽어 확인**하고(규칙 11),
  결과를 status() 로 남긴다 — /health 의 cpu_threads 가 그대로 보여 준다.
"""
from __future__ import annotations

import threading
from typing import Any

import tuning
import vlog

_LOG = vlog.get("vigent.thread_tune")

DEFAULT_TORCH: str = "auto"      # config/tuning.yaml threads.torch 와 같은 값(tests/test_thread_tune.py 가 대조)
DEFAULT_OPENCV: int = 2          # config/tuning.yaml threads.opencv 와 같은 값
AUTO_TORCH_THREADS: int = 1      # auto 가 cuda 에서 거는 수
MAX_THREADS: int = 256

_lock = threading.Lock()
_NOT_YET = "아직 적용 전"
_state: dict[str, dict[str, Any]] = {
    "torch": {"setting": None, "device": None, "applied": False, "before": None, "after": None, "note": _NOT_YET + "(검출 모델 로드 전)"},
    "opencv": {"setting": None, "applied": False, "before": None, "after": None, "note": _NOT_YET + "(서버 기동 전)"},
}


def _parse(v: Any, default: Any, allow_auto: bool) -> Any:
    """설정값 → 'auto'(allow_auto 일 때) | 0(건드리지 않음) | 1..MAX_THREADS. 알 수 없는 값은 default.
    false = 0(끔), true = default(켬 — 수는 기본값)."""
    if isinstance(v, bool):
        return default if v else 0
    if isinstance(v, str):
        s = v.strip().lower()
        if allow_auto and s == "auto":
            return "auto"
        if s in ("false", "off", "no"):
            return 0
        try:
            v = float(s)
        except ValueError:
            return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f < 0 or f > MAX_THREADS:
        return default
    return int(f)


def torch_setting() -> Any:
    """threads.torch → 'auto' | 0 | N."""
    return _parse(tuning.val("threads", "torch", DEFAULT_TORCH, env="VIGENT_TORCH_THREADS"), DEFAULT_TORCH, allow_auto=True)


def opencv_setting() -> int:
    """threads.opencv → 0 | N."""
    return int(_parse(tuning.val("threads", "opencv", DEFAULT_OPENCV, env="VIGENT_OPENCV_THREADS"), DEFAULT_OPENCV, allow_auto=False))


def torch_target(device: str) -> int:
    """이 장치에서 걸 PyTorch CPU 스레드 수. 0 = 건드리지 않는다."""
    s = torch_setting()
    if s == "auto":
        return AUTO_TORCH_THREADS if str(device or "").strip().lower().startswith("cuda") else 0
    return int(s)


def _torch() -> Any:
    import torch
    return torch


def _cv2() -> Any:
    import cv2
    return cv2


def apply_torch(device: str) -> dict[str, Any]:
    """검출 모델이 올라간 장치를 받아 PyTorch CPU 스레드 수를 건다. 슬롯마다 불려도 된다(이미 그 수면 아무 일도 안 한다)."""
    with _lock:
        setting = torch_setting()
        want = torch_target(device)
        prev = _state["torch"]
        st: dict[str, Any] = {"setting": setting, "device": str(device), "applied": False, "before": None, "after": None, "note": ""}
        if want <= 0:
            st["note"] = ("설정으로 끔(threads.torch: 0) — 건드리지 않는다" if setting != "auto"
                          else f"auto: 검출 장치가 cuda 가 아니다({device}) — CPU 스레드가 추론을 하므로 건드리지 않는다")
            if prev.get("note") != st["note"]:
                _LOG.info("PyTorch CPU 스레드 제한 안 함 — %s", st["note"])
            _state["torch"] = st
            return dict(st)
        try:
            t = _torch()
            before = int(t.get_num_threads())
            if before != want:
                t.set_num_threads(want)
            after = int(t.get_num_threads())
        except Exception as ex:  # noqa: BLE001  제한 실패가 검출을 막으면 안 된다
            st["note"] = f"적용 실패: {type(ex).__name__}: {ex}"
            _LOG.warning("PyTorch CPU 스레드 제한 실패(검출은 계속): %s", st["note"])
            _state["torch"] = st
            return dict(st)
        st.update(before=before, after=after, applied=(after == want))
        if after != want:                          # 걸었는데 안 걸렸다 — 조용히 넘기지 않는다
            st["note"] = f"요청 {want}개인데 실제 {after}개"
            _LOG.warning("PyTorch CPU 스레드 제한이 적용되지 않았다: %s", st["note"])
        else:
            st["note"] = f"{want}개로 제한(threads.torch: {setting}, 검출 장치 {device})"
            if before != want:
                _LOG.info("PyTorch CPU 스레드 %d → %d (threads.torch: %s, 검출 장치 %s) — 검출 결과 무영향, 대기 중 CPU 사용만 줄인다",
                          before, after, setting, device)
        _state["torch"] = st
        return dict(st)


def apply_opencv() -> dict[str, Any]:
    """OpenCV 스레드 수를 건다 — 서버 기동 때 1회(main._startup). 다시 불려도 된다."""
    with _lock:
        want = opencv_setting()
        st: dict[str, Any] = {"setting": want, "applied": False, "before": None, "after": None, "note": ""}
        if want <= 0:
            st["note"] = "설정으로 끔(threads.opencv: 0) — 건드리지 않는다"
            _LOG.info("OpenCV 스레드 제한 안 함 — %s", st["note"])
            _state["opencv"] = st
            return dict(st)
        try:
            c = _cv2()
            before = int(c.getNumThreads())
            if before != want:
                c.setNumThreads(want)
            after = int(c.getNumThreads())
        except Exception as ex:  # noqa: BLE001  제한 실패가 기동을 막으면 안 된다
            st["note"] = f"적용 실패: {type(ex).__name__}: {ex}"
            _LOG.warning("OpenCV 스레드 제한 실패(기동은 계속): %s", st["note"])
            _state["opencv"] = st
            return dict(st)
        st.update(before=before, after=after, applied=(after == want))
        if after != want:
            st["note"] = f"요청 {want}개인데 실제 {after}개"
            _LOG.warning("OpenCV 스레드 제한이 적용되지 않았다: %s", st["note"])
        else:
            st["note"] = f"{want}개로 제한(threads.opencv: {want})"
            if before != want:
                _LOG.info("OpenCV 스레드 %d → %d (threads.opencv) — 얼굴 찾기·크기 조절의 대기 중 CPU 사용을 줄인다", before, after)
        _state["opencv"] = st
        return dict(st)


def status() -> dict[str, dict[str, Any]]:
    """{torch: {setting, device, applied, before, after, note}, opencv: {setting, applied, before, after, note}} — /health 용."""
    with _lock:
        return {k: dict(v) for k, v in _state.items()}
