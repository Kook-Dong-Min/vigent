"""go2rtc_client.py — go2rtc 주소 **한 곳**. [OPEN_ISSUES_20261008 #16]

예전엔 `http://127.0.0.1:1984` 가 routers/cameras 2곳·routers/tapo 5곳·starvation_guard 1곳에 리터럴로 흩어져 있었고
포트 상수(_G2_PORT)는 cameras 에만 있었다. 주소를 바꾸면 세 파일을 같이 고쳐야 했다.
환경변수 VIGENT_GO2RTC_URL(예: http://127.0.0.1:1984)로 덮어쓸 수 있다 — config/go2rtc.yaml api.listen 과 같아야 한다.
"""
from __future__ import annotations

import os
import urllib.parse

BASE: str = os.environ.get("VIGENT_GO2RTC_URL", "http://127.0.0.1:1984").rstrip("/")
_u = urllib.parse.urlparse(BASE)
HOST: str = _u.hostname or "127.0.0.1"
PORT: int = int(_u.port or 1984)


def url(path: str, **query: str) -> str:
    """HTTP URL. query 는 urlencode. 예: url("/api/streams", src="cam1")"""
    q = ("?" + urllib.parse.urlencode(query)) if query else ""
    return BASE + ("/" + path.lstrip("/")) + q


def ws_url(path: str, **query: str) -> str:
    """WebSocket URL(ws://). 예: ws_url("/api/ws", src="cam1")"""
    return url(path, **query).replace("http://", "ws://", 1).replace("https://", "wss://", 1)


# [2026-10-12 웹캠 WebRTC] go2rtc 내부 RTSP 재배포(루프백) — config/go2rtc.yaml rtsp.listen "127.0.0.1:8554" 과 같아야 한다.
#   웹캠 번호 카메라는 go2rtc(ffmpeg)가 장치를 한 번만 열고, 검출 워커는 이 주소로 다시 읽는다(cv2 와 ffmpeg 는 같은 웹캠을
#   동시에 못 연다 — 실측 10-12). 환경변수 VIGENT_GO2RTC_RTSP_PORT 로 덮어쓸 수 있다.
RTSP_PORT: int = int(os.environ.get("VIGENT_GO2RTC_RTSP_PORT", "8554"))


def rtsp_url(name: str) -> str:
    """go2rtc 가 재배포하는 스트림의 RTSP 주소. 예: rtsp_url("webcam") → rtsp://127.0.0.1:8554/webcam"""
    return f"rtsp://{HOST}:{RTSP_PORT}/{urllib.parse.quote(name, safe='')}"
