"""boxed_view.py — 박스 일치 보기: 검출에 쓴 **바로 그 프레임**에 박스를 그려 연속 JPEG(MJPEG)로 내보낸다.

[2026-11-16] 배경: 관제 확대뷰는 영상(go2rtc WebRTC)과 박스(/cameras/{id}/detections 폴링)가 서로 다른 길로 도착해
박스가 사람을 뒤따라 보였다(실측: 확대뷰 박스 갱신 간격 중앙값 110 ms vs 영상 33 ms). 여기서는 워커가 추론을 끝낸
프레임과 그 프레임의 박스를 한 묶음(Worker._last_view)으로 받아 서버에서 합친다 → 영상과 박스가 항상 같은 순간이다.
대가: 영상 장수가 검출 속도로 떨어진다(30장 → 검출 주기).

★[2026-11-16 B안 전환 — 사용자 결정] 위 방식(A안: 검출한 프레임만 = 실측 초당 7.2장)은 "재생이 너무 느리다"는
  판단으로, **카메라가 주는 모든 프레임에 가장 최근 박스를 얹는** 방식(B안, latest_view)이 기본이 됐다.
  영상은 카메라 속도(웹캠 30장)로 부드럽지만 **박스는 검출이 끝난 시점의 위치**라 움직이는 대상을 추론 시간만큼
  뒤따른다(예측 보정 없음). A안 묶음(Worker._last_view)은 캡처 스레드가 없는 소스(파일·이미지·sync 모드)의 폴백으로 남는다.

원칙:
  · **원본 프레임에는 그리지 않는다** — 항상 사본에 그린다(증거 JPEG·학습 수집은 깨끗한 원본이어야 한다).
  · 표시 전용 — 검출·추적·경보 판정은 이 모듈을 부르지 않는다.
  · 서버가 만들어 내보내는 이미지이므로 스냅샷(/cameras/{id}/snapshot)과 같은 얼굴 비식별화 정책(privacy)을 따른다.
  · 같은 프레임은 한 번만 인코딩한다(보는 사람이 여럿이어도 카메라당 1회) — 인코딩 결과를 카메라별로 1장 보관.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import cv2

BOUNDARY = "vigentframe"
MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"
JPEG_QUALITY = 80
POLL_S = 0.01          # 새 묶음 확인 주기(초)
KEEPALIVE_S = 1.0      # 새 프레임이 없어도 이 간격으로 직전 장면을 다시 보낸다(끊긴 시청자 감지용)
STALL_END_S = 10.0     # 새 프레임이 이만큼 없으면 스트림을 끝낸다 — 멈춘 그림이 '실시간'처럼 남아 있으면 안 된다
BOX_MAX_AGE_S = 1.5    # [B안] 박스가 이보다 오래됐으면 그리지 않는다 — 검출이 멈췄는데 옛 박스가 살아 있는 영상 위에 남으면 안 된다

# 허브 화면(themes/safety/index_hub.html drawSpot)과 같은 색 — BGR
_COL_MISSING = (70, 57, 230)     # #e63946 미착용(NO-*)
_COL_PERSON = (255, 208, 57)     # #39d0ff 사람
_COL_OTHER = (113, 191, 47)      # #2fbf71 그 외(보호구 착용 등)

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, bytes]] = {}     # 카메라 id → (프레임 시각, JPEG)


def _color(cls: str) -> tuple[int, int, int]:
    c = str(cls or "")
    if c.lower().startswith("no-"):
        return _COL_MISSING
    return _COL_PERSON if c.lower() == "person" else _COL_OTHER


def render(frame: Any, dets: list[dict[str, Any]]) -> Any:
    """프레임 **사본**에 박스·라벨을 그려 돌려준다(원본 불변). dets: [{"class","score","bbox":[x1,y1,x2,y2] 정규화}]."""
    import privacy
    persons = [d.get("bbox") for d in dets if str(d.get("class", "")).lower() == "person"]
    img = privacy.anonymize_faces(frame, persons)        # 켜져 있으면 사본을 돌려준다
    if img is frame:                                     # 꺼져 있거나 실패 → 원본이 그대로 왔다. 그리기 전에 반드시 복사
        img = frame.copy()
    h, w = img.shape[:2]
    thick = max(1, round(min(w, h) / 320))
    scale = max(0.4, min(w, h) / 960)
    for d in dets:
        try:
            x1, y1, x2, y2 = (float(v) for v in list(d.get("bbox") or [])[:4])
        except (TypeError, ValueError):
            continue
        p1 = (int(round(x1 * w)), int(round(y1 * h)))
        p2 = (int(round(x2 * w)), int(round(y2 * h)))
        col = _color(str(d.get("class", "")))
        cv2.rectangle(img, p1, p2, col, thick)
        label = f"{d.get('class', '')} {round(float(d.get('score') or 0) * 100)}%"
        ty = p1[1] - 4 if p1[1] - 4 > 12 else p1[1] + 14          # 위가 잘리면 박스 안쪽에
        cv2.putText(img, label, (p1[0] + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.putText(img, label, (p1[0] + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, scale, col, thick, cv2.LINE_AA)
    return img


def jpeg_for(cid: str, view: tuple, keep: Callable[[dict], bool] | None = None) -> bytes | None:
    """묶음 (frame, dets, ts) → JPEG. 같은 카메라·같은 ts 는 보관본을 돌려준다(시청자 수와 무관하게 1회 인코딩)."""
    frame, dets, ts = view
    with _cache_lock:
        hit = _cache.get(cid)
        if hit is not None and hit[0] == ts:
            return hit[1]
        shown = [d for d in (dets or []) if keep is None or keep(d)]
        ok, buf = cv2.imencode(".jpg", render(frame, shown), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:                       # imencode 는 실패해도 예외가 아니라 False 다(규칙 11)
            return None
        jpg = bytes(buf.tobytes())
        _cache[cid] = (ts, jpg)
        return jpg


def latest_view(wk: Any, now: float | None = None) -> tuple | None:
    """[B안] 워커 → (가장 최근 **캡처** 프레임, 가장 최근 박스, 캡처 시각). 없으면 None.

    · 캡처 스레드(worker._StreamCapture, 스트림+thread 모드)가 있으면 그 최신 프레임 = 카메라 속도(검출 주기와 무관).
      박스는 마지막 추론 결과를 그대로 얹는다 — BOX_MAX_AGE_S 보다 오래됐으면 박스 없이 영상만 보낸다.
    · 캡처 스레드가 없으면(파일·이미지·sync 모드) A안 묶음(_last_view: 검출한 프레임 + 그 박스)을 그대로 돌려준다.
    """
    bundle = getattr(wk, "_last_view", None)
    bundle = bundle if isinstance(bundle, tuple) and len(bundle) == 3 else None
    sc = getattr(wk, "_streamcap", None)
    frame, ts = None, 0.0
    if sc is not None:
        try:
            frame, ts = sc.read_latest()
        except Exception:  # noqa: BLE001  캡처가 정리되는 순간 등 — 폴백으로
            frame = None
    if frame is None or not hasattr(frame, "shape") or not ts:
        return bundle
    dets: list = []
    if bundle is not None:
        age = (time.time() if now is None else now) - float(bundle[2])
        if age <= BOX_MAX_AGE_S:
            dets = bundle[1]
    return (frame, dets, float(ts))


def forget(cid: str) -> None:
    """카메라가 지워질 때 보관본을 버린다."""
    with _cache_lock:
        _cache.pop(cid, None)


def _part(jpg: bytes, ts: float | None = None) -> bytes:
    """MJPEG 조각 1개. X-Timestamp = 그 프레임의 캡처(또는 검출 프레임) 시각(epoch 초) — 받는 쪽이 '수신 시각 − 이 값'으로
    서버 구간 지연을 잴 수 있게 한다(규칙 11: 잴 수 있는 것은 잰다). 브라우저는 모르는 조각 헤더를 무시한다."""
    stamp = f"X-Timestamp: {ts:.3f}\r\n" if ts else ""
    return (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpg)}\r\n{stamp}\r\n".encode("ascii")
            + jpg + b"\r\n")


def frames(cid: str, get_view: Callable[[], tuple | None],
           closing: Callable[[], bool] = lambda: False,
           keep: Callable[[dict], bool] | None = None) -> Iterator[bytes]:
    """MJPEG 조각 생성기. get_view() 가 돌려주는 묶음의 ts 가 바뀔 때마다 1장.
    끝나는 조건: 서버 종료 중(closing) · 새 프레임이 STALL_END_S 동안 없음. 시청자가 끊기면 서버가 이 생성기를 닫는다."""
    last_ts: float | None = None
    last_jpg: bytes | None = None
    last_new = last_sent = time.monotonic()
    while not closing():
        view = get_view()
        now = time.monotonic()
        if view is not None and view[2] != last_ts:
            jpg = jpeg_for(cid, view, keep)
            if jpg is not None:
                last_ts, last_jpg, last_new, last_sent = view[2], jpg, now, now
                yield _part(jpg, last_ts)
                continue
        if now - last_new >= STALL_END_S:
            return
        if last_jpg is not None and now - last_sent >= KEEPALIVE_S:
            last_sent = now
            yield _part(last_jpg, last_ts)
            continue
        time.sleep(POLL_S)
