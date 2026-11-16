"""routers/safety_core.py — safety 도메인의 **비전 핵심** 라우트(워커·이벤트 조회·센서·설정·관제 화면·테마 페이지). main 미import.
[②-c, 2026-10-12 MERGE_PLAN] 에이전트·LLM 층 라우트 49개 작업(홈 허브·위험성평가·자동처리·브레인·음성·안내·평가·데모·견적)은
routers/agentic.py 로 옮겼다가 ⑤ 에서 삭제했다. 캐치올 /{theme} 도 ⑤-3 에서 삭제(없는 주소 = 일반 404).

홈·리포트·워커·브레인·이벤트·자동처리·음성·데모·설정·테마페이지 등 safety 핵심 라우트.
※ `/` 루트는 app.version(FastAPI 인스턴스)을 참조하므로 순환 방지 위해 main.py 에 잔류.
"""
import json as _json
import time as _time

import data_engine
from app_state import _START_TS, DEFAULT_THEME, STATE
from app_state import DETECT_LOCK as _DETECT_LOCK
from app_state import load_theme as _load_theme
from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.responses import HTMLResponse
from web_util import (
    _ROOT,
)

router = APIRouter()


@router.get("/favicon.ico")
def favicon():
    """브라우저 자동요청 favicon — 없어서 나던 404 콘솔 노이즈 제거(내용은 비움)."""
    return Response(status_code=204)

# [정리 0, 2026-10-12] /evidence/search(Copilot 법령 인용)는 ⑤-2 뒤 Copilot 이 없어 500 을 내던 죽은 라우트 — 삭제(호출처 0).

@router.post("/worker/start")
def worker_start(payload: dict = Body(...), theme: str = DEFAULT_THEME):
    """카메라 1대 워커 시작. payload={id?, source(RTSP/비디오/이미지/웹캠번호), name?, fps?, zone?}.
    위험 감지 시 data_engine 기록 → 자동처리 콘솔 자동 노출."""
    import camera_registry as _reg
    import worker as _w
    bundle = STATE.get(theme) or _load_theme(theme)
    src = str(payload.get("source", "")).strip()
    if not src:
        raise HTTPException(status_code=400, detail="source(RTSP/비디오/이미지 경로 또는 웹캠번호) 필요")
    cid = str(payload.get("id", "cam1"))
    name = str(payload.get("name", "")) or cid
    fps = float(payload.get("fps", 2.0))
    # ★[2026-08-26] 시작한 워커를 **등록부에도 남긴다.**
    #   이전에는 manager.start 를 직접 불러 등록부를 거치지 않았다. 그렇게 만든 워커는
    #   `/cameras` 에는 없는데 `/health.cameras` 에는 보이고, `DELETE /cameras/{id}` 가
    #   404 라 **지울 방법이 재시작뿐**이었다 — 워커를 되살리는 세 번째 입구이자 유령의 원형.
    #   등록부에 넣어 두면 정규 삭제·비활성 경로가 그대로 듣는다(동작은 그대로, 회수만 가능해짐).
    try:   # [1단계 M-4·M-5] source 허용목록(camera_registry.validate_source) 위반 → 400
        _reg.upsert(cid, name=name, source=src, enabled=True, fps=fps, zone=payload.get("zone"))
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from None
    return _w.manager.start(bundle["agents"].get("Guard"), _DETECT_LOCK, cid, src,
                            name=name, fps=fps, zone=payload.get("zone"))

@router.post("/worker/stop")
def worker_stop(payload: dict = Body(default={})):
    """워커 중지. payload={id} 면 그 카메라만, 없으면 전체."""
    import worker as _w
    cid = payload.get("id")
    return _w.manager.stop(cid) if cid else _w.manager.stop_all()

@router.get("/status")
@router.get("/worker/status")
@router.get("/workers")
def worker_status():
    """워커 단위 세부 상태(2단계) — 카메라별 running·last_frame_secs_ago·hang·frames·fps·restarts·reconnects·error.
    /health(프로세스 생존)와 분리. 워치독의 2차 감시(hang 판정)와 대시보드가 이 엔드포인트를 쓴다."""
    import worker as _w
    st = _w.manager.status()
    cams = st.get("cameras", {})
    st["worker_count"] = len(cams)
    st["any_hang"] = any(c.get("hang") for c in cams.values())      # 하나라도 hang → 워치독 2차 트리거 근거
    st["running_count"] = sum(1 for c in cams.values() if c.get("running"))
    st["uptime_s"] = round(_time.time() - _START_TS, 1)
    return st

@router.post("/workers/start-all")
def workers_start_all(theme: str = DEFAULT_THEME):
    """config/site.yaml 의 모든 카메라로 워커 일괄 시작(헤드리스/USB 부팅용)."""
    import worker as _w
    bundle = STATE.get(theme) or _load_theme(theme)
    return _w.manager.autostart(bundle["agents"].get("Guard"), _DETECT_LOCK)

@router.post("/workers/stop-all")
def workers_stop_all():
    """모든 워커 중지."""
    import worker as _w
    return _w.manager.stop_all()

# [M3-2] 센서별 임계 상태(초과 중인가) — "진입 전이"에서만 통보하기 위한 메모리 상태(재기동 시 초기화 = 다음 초과가 전이).
import logging

_LOG = logging.getLogger("vigent.safety_core")
_SENSOR_DANGER: dict[str, bool] = {}


@router.post("/safety/sensor")
def safety_sensor(payload: dict = Body(...), theme: str = DEFAULT_THEME):
    """IoT 센서 값 수신 → 임계 초과 시 위험 기록 + 알림.
    카메라로 못 보는 영역(질식·가스·온열). 외부 센서가 주기적으로 값을 POST.
    payload: {type:'o2'|'co'|'h2s'|'gas'|'temp', value:float, site?, threshold?}
    """
    stype = str(payload.get("type") or "").lower()
    try:
        value = float(payload.get("value"))  # type: ignore[arg-type]  # 오탐: None 이면 아래 except (TypeError) 로 처리(방어적)
    except (TypeError, ValueError):
        return {"ok": False, "error": "value(숫자) 필요"}
    site = payload.get("site") or "현장"
    th = payload.get("threshold")
    spec = {
        "o2":   ("asphyxiation", lambda v: v < (th or 18.0) or v > 23.5, "산소농도 {v}% (안전 18~23.5%)"),
        "co":   ("gas_alarm",    lambda v: v >= (th or 30),  "일산화탄소(CO) {v}ppm"),
        "h2s":  ("asphyxiation", lambda v: v >= (th or 10),  "황화수소(H2S) {v}ppm"),
        "gas":  ("gas_alarm",    lambda v: v >= (th or 10),  "가연성가스 {v}%LEL"),
        "temp": ("heat_stress",  lambda v: v >= (th or 33),  "체감온도/WBGT {v}℃"),
    }
    if stype not in spec:
        return {"ok": False, "error": f"지원 센서: {', '.join(spec)}"}
    rule, danger_fn, msg_t = spec[stype]
    danger = bool(danger_fn(value))
    result = {"ok": True, "type": stype, "value": value, "danger": danger, "rule": rule}
    # ★[CODE_REVIEW M3-2, 2026-09-06] 예전엔 임계 초과 POST **마다** dispatcher.dispatch("critical") 을 직접
    #   불러 통보+relay 가 센서 주기마다 반복됐다(억제 관문 0). 이제 ①기록은 매 POST(G7 유지) ②통보·relay 는
    #   "임계 진입 **전이**"에서만 1회 — alert_notify.submit(출처 키 sensor:<종류>, edge=True: 쿨다운 건너뜀·
    #   시간당 상한 유지) ③임계 아래로 내려갔다 다시 넘으면 새 전이. 응답 alert_sent 는 "통보 큐 적재" 의미.
    key = f"{stype}|{site}"
    was_danger = _SENSOR_DANGER.get(key, False)
    # ★[CODE_AUDIT_20260928 #1-①] 예전엔 여기서 _SENSOR_DANGER[key]=danger 를 **먼저** 기록했다. 그러면 submit 이 게이트(시간당
    #   상한·disabled·queue_full)나 예외로 막혀도 전이는 이미 소비돼, 위험 구간에 머무는 동안 critical 이 다시는 나가지 않았다.
    #   이제 전이 확정(True 기록)은 **submit 이 queued=True 를 돌려줄 때만**. 막히면 미확정으로 남겨 다음 POST 가 다시 시도한다.
    if not danger:
        _SENSOR_DANGER[key] = False
    result["transition"] = bool(danger and not was_danger)
    if danger:
        msg = msg_t.format(v=value) + " — 위험 임계 초과"
        try:
            data_engine.log_event(rule, level="critical", score=value, site=site, note=msg)
        except Exception:  # noqa: BLE001
            _LOG.error("센서 위험 기록 실패(%s) — 통보는 계속 시도", key, exc_info=True)
        result["alert_sent"] = False
        if result["transition"]:
            try:
                import alert_notify
                n = alert_notify.submit(cam=f"sensor:{stype}", rule=rule, level="critical",
                                        message=f"[{site}] {msg}", meta={"sensor": stype, "value": value},
                                        edge=True)
                result["alert_sent"] = bool(n.get("queued"))
                result["gate"] = n.get("reason")
                if result["alert_sent"]:
                    _SENSOR_DANGER[key] = True          # 통보 큐에 실제로 들어갔을 때만 전이 확정
                else:
                    _LOG.warning("센서 전이 통보가 막힘(%s, %s) — 전이 미확정, 다음 POST 에 재시도", key, n.get("reason"))
            except Exception:  # noqa: BLE001
                _LOG.error("센서 전이 통보 예외(%s) — 전이 미확정, 다음 POST 에 재시도", key, exc_info=True)
        result["message"] = msg
    return result

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(theme: str = DEFAULT_THEME, style: str = "default"):
    """테마별 경영 대시보드 — 실제 이벤트 통계. style=terminal 이면 모던 터미널 스킨."""
    import dashboard as _dash
    fn = _dash.render_terminal if style == "terminal" else _dash.render
    return fn(theme)   # [⑤-2] 비전 이벤트 통계만(위험성평가서·TBM·승인 집계는 에이전트 층과 함께 삭제)

@router.get("/safety/setup", response_class=HTMLResponse)
def safety_setup():
    """현장 운영 설정 페이지 — site.yaml·notify.yaml 손편집 없이 화면에서 구성·운영."""
    import setup_console
    return setup_console.render()

@router.get("/site/config")
def site_config_get():
    import setup_console
    return setup_console.read_site()

@router.post("/site/config")   # [1단계 M-3] write_site 가 저장 거부(ok:False)를 돌려주면 400
def site_config_post(payload: dict = Body(...)):
    import setup_console
    out = setup_console.write_site(payload)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error") or "현장 설정 저장 거부")
    return out

@router.get("/notify/config")
def notify_config_get():
    import setup_console
    return setup_console.read_notify_masked()

@router.post("/notify/config")
def notify_config_post(payload: dict = Body(...)):
    import setup_console
    out = setup_console.write_notify(payload)
    if not out.get("ok"):   # [1단계 H-4] 웹훅 목적지 미허용 등 저장 거부 → 400(화면이 오류로 표시)
        raise HTTPException(status_code=400, detail=out.get("error") or "알림 설정 저장 거부")
    return out

@router.post("/alerts/test")
def alerts_test(payload: dict = Body(default={}), theme: str = DEFAULT_THEME):
    """Dispatcher 경보 테스트. payload={level, message}. 키 없으면 폴백(로그)로 동작.

    ★[CODE_REVIEW M3-3] **의도된 게이트 우회 경로** — alert_gate(쿨다운·백오프·시간당 상한)를 거치지 않고
    dispatcher.dispatch 를 직접 부른다. 설정 콘솔의 "채널 연결 시험" 전용이며 자동 발화 경로에서 쓰지 말 것.
    (큐에는 남으므로 채널 미설정이면 pending→dead 로 흐른다 — 모듈 4 ①.)"""
    bundle = STATE.get(theme) or _load_theme(theme)
    dispatcher = bundle["agents"].get("Dispatcher")
    return dispatcher.dispatch(payload.get("level", "high"),
                               payload.get("message", "VIGENT 경보 테스트"))

@router.get("/alerts/status")
def stub_alerts_status():
    return {"ok": True, "alerts": []}

@router.get("/safety-pro", response_class=HTMLResponse)
def safety_pro():
    """rf-detr permissive 백엔드를 쓰는 VIGENT 콘솔 화면(탐지·위험구역·VLM)."""
    p = _ROOT / "themes" / "safety" / "index_rfdetr.html"
    return p.read_text(encoding="utf-8")

@router.get("/safety-hub", response_class=HTMLResponse)
def safety_hub():
    """스마트 카메라 관제 대시보드(3.0) — 멀티카메라 그리드·KPI·이벤트 스트림.
    [2026-10-12 설정 통합] 확대뷰 박스 표시 옵션(config/tuning.yaml hub.box_*)을 window.VIGENT_HUB_CFG 로 주입 —
    HTML 을 고치지 않고 tuning.yaml 한 파일로 바꿀 수 있게. 값이 없으면 페이지의 폴백 기본값을 쓴다."""
    import tuning
    html = (_ROOT / "themes" / "safety" / "index_hub.html").read_text(encoding="utf-8")
    cfg = {"box_extrap_cap": float(tuning.val("hub", "box_extrap_cap", 2.0)),
           "box_render_tau": float(tuning.val("hub", "box_render_tau", 40.0)),
           "spot_poll_ms": int(tuning.val("hub", "spot_poll_ms", 50)),
           # [2026-11-16] 확대뷰 영상: "webrtc"(기본 = 예전 그대로) | "boxed"(서버가 박스를 그린 MJPEG, /cameras/{id}/boxed.mjpg)
           "spot_video": ("boxed" if str(tuning.val("hub", "spot_video", "webrtc")).strip().lower() == "boxed" else "webrtc")}
    inject = "<script>window.VIGENT_HUB_CFG=" + _json.dumps(cfg) + ";</script>"
    return html.replace("</head>", inject + "</head>", 1)

# [⑤-3, 2026-10-12] /{theme} 캐치올·/safety/judge·/safety/posture·/safety-local 은 브라우저 화면(index.html·index_local.html)과 함께
#   삭제됐다. 없는 주소는 FastAPI 기본 404(JSON {"detail":"Not Found"}). /theme/{theme}/raw(삭제 화면이 읽던 vision.yaml 원본)도 마무리 커밋에서 삭제.
