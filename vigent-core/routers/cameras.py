"""routers/cameras.py — 카메라 등록·관리 CRUD + 워커 연동(3.0 Phase 1).

자격증명은 camera_registry 가 secrets 로 분리·마스킹 → 응답엔 마스킹된 source 만 노출.
enable=워커 start(cam_id=track_key 격리), disable=stop. startup 자동복원은 autostart_enabled().
"""
import subprocess

import camera_registry as _reg
from app_state import DEFAULT_THEME, STATE
from app_state import DETECT_LOCK as _DETECT_LOCK
from app_state import load_theme as _load_theme
from fastapi import APIRouter, Body, HTTPException, Response
from web_util import is_safety_label as _is_safety

router = APIRouter()


def _worker(cid: str):
    import worker as _w
    return _w.manager._workers.get(cid)


def _guard():
    bundle = STATE.get(DEFAULT_THEME) or _load_theme(DEFAULT_THEME)
    return bundle["agents"].get("Guard")


def _is_local_file_source(src: str) -> bool:
    """[OPEN_ISSUES #41, 2026-10-12] 로컬 영상 파일 소스인가(벤치·시험용). rtsp(s)://·웹캠 번호·그 외 스킴은 False.
    파일은 go2rtc 에 올리지 않는다 — 실기(10-12 03:0x): 경로가 `ffmpeg:` 접두 없이 그대로 올라가 WebRTC 가 '성공' 으로
    판정된 뒤 영상은 검게 남고 서버 박스만 그려졌다(워커는 되감기로 계속 검출). 파일은 허브가 바로 스냅샷 모드를 쓴다."""
    s = (src or "").strip()
    if not s or s.isdigit() or "://" in s:
        return False
    try:
        return _Path(s).is_file()          # _Path 는 모듈 하단(287행)에서 import — 호출 시점엔 로드돼 있다
    except (OSError, ValueError):
        return False


def _g2_register(cid: str) -> bool:
    """go2rtc(localhost:1984)에 카메라 스트림을 동적 등록 — 대시보드 WebRTC 저지연 재생용.
    go2rtc 미실행이면 조용히 무시(스냅샷 폴링으로 폴백 — 규칙6 무중단). 자격증명은 로그에 남기지 않음.
    [#41] 로컬 파일 소스는 등록하지 않는다(위 _is_local_file_source) — RTSP 등 그 외 소스는 예전과 동일.
    반환 True = go2rtc 가 등록을 받았다(2xx). [웹캠 WebRTC] _start 가 웹캠 워커의 입력을 go2rtc RTSP 로 돌릴지 이 값으로 정한다."""
    try:
        import urllib.parse
        import urllib.request
        src = _reg.source_of(cid)
        if not src:
            return False
        if _is_local_file_source(src):
            _LOG.info("go2rtc 등록 건너뜀(파일 소스, 스냅샷 모드): %s", cid)
            return False
        import go2rtc_client
        url = go2rtc_client.url("/api/streams", name=cid, src=_g2_source_for(src))
        with urllib.request.urlopen(urllib.request.Request(url, method="PUT"), timeout=3) as r:
            return 200 <= int(getattr(r, "status", 200) or 200) < 300
    except Exception:  # noqa: BLE001  go2rtc 미실행/실패 — WebRTC 없이 폴백
        return False


# [2026-10-12 웹캠 WebRTC] 웹캠 번호("0")는 go2rtc 가 그대로는 열 수 없다(스킴 없음 → 스트림 실패 → 허브 스냅샷 폴백 1.2초 주기).
#   go2rtc 의 ffmpeg device 소스로 바꿔 등록한다: dshow(Windows) 로 N번 장치를 열고(ffmpeg 가 인덱스→장치명 변환),
#   WebRTC 가 요구하는 H264 로 트랜스코딩(#video=h264). 실측(이 노트북 Integrated Webcam): 640x480 29.8fps, 검출 워커와
#   동시에 열어도 두 쪽 모두 프레임 수신(dshow 공유 가능) → 워커(cv2) 와 go2rtc(ffmpeg) 가 같은 웹캠을 나눠 쓴다.
#   RTSP·파일 등 그 외 소스는 원문 그대로(불변). 크기·fps 는 보수적 기본(640x480@30) — 장치가 지원하지 않으면 ffmpeg 가 실패해
#   스냅샷 폴백으로 남는다(화면이 죽지는 않는다).
_WEBCAM_G2_SIZE = "640x480"
_WEBCAM_G2_FPS = 30


def _g2_source_for(src: str) -> str:
    """등록부 source → go2rtc 에 넘길 src. 웹캠 번호만 ffmpeg:device 로 변환, 나머지는 원문.
    [2026-10-12 검토·보류] 웹캠 WebRTC 인코딩을 GPU(NVENC)로 돌리는 #hardware 를 시도했으나, go2rtc 의 #hardware 는 **device(dshow) 소스에
    픽셀포맷 변환(yuyv→nv12)을 끼워주지 않아** NVENC 가 "Could not open encoder" 로 실패했다(이 노트북 실측). 직접 ffmpeg 에 -vf format=nv12
    를 주면 되지만 go2rtc source 문자열로는 그 필터를 전달할 수 없고, exec: 우회는 보안 허용목록(M-5)에 막힌다. 한편 실제 배포는 RTSP 카메라라
    go2rtc 가 H264 를 **그대로 통과**(transcode 없음)시켜 인코드 CPU 자체가 없다 — 웹캠(개발용)만의 문제라 가치가 낮아 libx264 유지."""
    s = (src or "").strip()
    if s.isdigit():
        return f"ffmpeg:device?video={s}&video_size={_WEBCAM_G2_SIZE}&framerate={_WEBCAM_G2_FPS}#video=h264"
    return src


def _ffmpeg_bin(root) -> str:
    """go2rtc 에 줄 ffmpeg 경로 — 저장소 bin/ffmpeg.exe(go2rtc.exe 와 같은 자리, gitignore) 가 있으면 그것, 없으면 "ffmpeg"(PATH)."""
    for name in ("ffmpeg.exe", "ffmpeg"):
        cand = root / "bin" / name
        if cand.exists():
            return str(cand)
    return "ffmpeg"


def _g2_unregister(cid: str) -> None:
    """go2rtc 스트림 해제(카메라 중지·삭제 시). 미실행이면 무시."""
    try:
        import urllib.parse
        import urllib.request

        import go2rtc_client
        url = go2rtc_client.url("/api/streams", src=cid)
        urllib.request.urlopen(urllib.request.Request(url, method="DELETE"), timeout=3)
    except Exception:  # noqa: BLE001
        pass


def _start(cid: str):
    """레지스트리 원본 source 로 워커 시작(자격증명은 워커 내부만, 로그는 마스킹).
    동시에 go2rtc 에도 스트림 등록(있으면 WebRTC, 없으면 스냅샷 폴백)."""
    import worker as _w
    c = _reg.get(cid)
    if not c:
        raise HTTPException(status_code=404, detail="없는 카메라")
    src = _reg.source_of(cid)
    if not src:
        raise HTTPException(status_code=400, detail="source 미등록")
    # [2026-10-12 웹캠 WebRTC] 웹캠 번호 소스는 go2rtc(ffmpeg)가 장치를 **한 번만** 열고, 워커는 go2rtc 의 루프백 RTSP 를
    #   읽는다(cv2 와 ffmpeg 는 같은 웹캠을 동시에 못 연다 — 실측: 워커가 잡고 있으면 ffmpeg "I/O error"). 그래서 워커보다
    #   go2rtc 등록을 **먼저** 하고, 등록이 됐을 때만 입력을 바꾼다. go2rtc·ffmpeg 가 없으면 예전처럼 cv2 직접(WebRTC 없음,
    #   스냅샷 폴백) — 규칙6 무중단. RTSP·파일 소스는 순서·입력 모두 예전과 같다(워커 시작 → go2rtc 등록).
    run_src = src
    webcam = src.strip().isdigit()
    if webcam:
        if _g2_register(cid):
            import go2rtc_client
            run_src = go2rtc_client.rtsp_url(cid)
            _LOG.info("웹캠 '%s'(장치 %s) → go2rtc 경유: 워커 입력 %s (WebRTC 가능)", cid, src.strip(), run_src)
        else:
            _LOG.warning("웹캠 '%s' go2rtc 등록 실패/미기동 → cv2 직접 열기(WebRTC 없음, 스냅샷 폴백)", cid)
    res = _w.manager.start(_guard(), _DETECT_LOCK, cid, run_src,
                           name=c.get("name") or cid, fps=float(c.get("fps", 2.0)),
                           zone=c.get("zone"), overrides=c.get("overrides"))   # [R15] 카메라별 override
    if not webcam:
        _g2_register(cid)
    return res


@router.get("/cameras")
def cameras_list():
    """등록 카메라 목록(마스킹) + 워커 라이브 상태 병합."""
    import worker as _w
    st = _w.manager.status().get("cameras", {})
    out = []
    for c in _reg.list_cameras():
        w = st.get(c["id"], {})
        out.append({**c, "online": bool(w.get("running")), "fps_live": w.get("fps"),
                    "reconnects": w.get("reconnects"), "last_frame_secs_ago": w.get("last_frame_secs_ago")})
    return {"cameras": out, "count": len(out)}


@router.post("/cameras")
def cameras_add(payload: dict = Body(...)):
    """등록/수정. payload={id, name?, source(rtsp/파일/웹캠번호), fps?, zone?, enabled?, overrides?}. source 는 마스킹 저장.
    overrides: [R15] 카메라별 설정 — 지금은 {"motion": {"immobile_s": 초}} 1키(앉아 작업 현장의 무동작 오경보 방지). {} 로 해제."""
    import worker as _w
    cid = str(payload.get("id") or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="id 필요")
    prev = _reg.get(cid) or {}
    try:
        c = _reg.upsert(cid, name=payload.get("name"), source=payload.get("source"),
                        fps=payload.get("fps"), zone=payload.get("zone"), enabled=payload.get("enabled"),
                        overrides=payload.get("overrides"))
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from None
    # [2026-10-12] fps 변경은 워커를 재시작해야 반영된다 — 예전엔 upsert 로 저장만 되고 아래 _start 가 "이미 실행 중" 을
    #   조용히 돌려줘 fps_live 가 그대로였다(화면은 fps_live 우선 표시라 "항상 2" 로 보임). 구역 저장(cameras_zone_set)과
    #   같은 stop→_start 패턴. [CODE_AUDIT #8] stop 미완료면 이중 워커를 만들지 않고 restart_error 로 사실대로 알린다.
    #   set_fps(루프 간격만) 로는 부족하다 — 풀세트 주기(_full_interval)가 루프 시작 시 fps 로 굳기 때문.
    restarted = False
    stop_error = ""
    fps_changed = prev and abs(float(prev.get("fps", 2.0)) - float(c.get("fps", 2.0))) > 1e-9
    live = _worker(cid)
    if fps_changed and c.get("enabled") and live is not None and (live.state.get("running") or live.is_alive()):
        r = _w.manager.stop(cid)
        if r.get("ok", True):
            restarted = True
        else:
            stop_error = str(r.get("error") or "stop 실패")
            _LOG.error("fps 변경(%s→%s) 후 워커 '%s' 재기동 보류 — %s", prev.get("fps"), c.get("fps"), cid, stop_error)
    worker = _start(cid) if c.get("enabled") and not stop_error else None
    out: dict = {"ok": True, "camera": c, "worker": worker, "restarted": restarted}
    if stop_error:
        out["restart_error"] = stop_error
    return out


@router.post("/cameras/{cid}/enable")
def cameras_enable(cid: str):
    if _reg.set_enabled(cid, True) is None:
        raise HTTPException(status_code=404, detail="없는 카메라")
    return {"ok": True, "worker": _start(cid)}


@router.post("/cameras/{cid}/disable")
def cameras_disable(cid: str):
    import worker as _w
    if _reg.set_enabled(cid, False) is None:
        raise HTTPException(status_code=404, detail="없는 카메라")
    _g2_unregister(cid)
    return {"ok": True, "worker": _w.manager.stop(cid)}


@router.delete("/cameras/{cid}")
def cameras_delete(cid: str):
    import worker as _w
    # ★[2026-08-26] **등록부를 가장 먼저 지운다** — 순서가 안전의 핵심이다.
    #   이전 순서(remove → g2_unregister → reg.delete)에는 경합 창이 있었다:
    #   `_g2_unregister` 는 go2rtc 가 안 떠 있으면 **최대 3초를 기다린다**(timeout=3).
    #   그 3초 동안 등록부에는 카메라가 살아 있으므로, 기아 감시(starvation_guard)가
    #   그 틈에 `_start(cid)` 를 부르면 **워커가 되살아난다**. 이후 reg.delete 가 등록부만
    #   지우므로 "등록부에 없는데 돌고 있는 워커"가 남아, 죽은 주소로 15초마다 재접속하며
    #   /health 를 영구 unhealthy 로 만든다(2026-08-26 소크 준비 중 실제 발생).
    #   등록부를 먼저 지우면 그 사이 어떤 경로가 _start 를 불러도 "없는 카메라"로 거부된다.
    ok = _reg.delete(cid)
    _w.manager.remove(cid)
    _g2_unregister(cid)
    # 등록부 삭제와 워커 제거 사이에 되살아났을 수 있다(위 3초 창의 잔여분) — 한 번 더 거둔다.
    _w.manager.remove(cid)
    import boxed_view
    boxed_view.forget(cid)                 # [박스 일치 보기] 지워진 카메라의 마지막 장면 보관본도 버린다
    return {"ok": ok}


@router.get("/cameras/{cid}/detections")
def cameras_detections(cid: str):
    """경량 오버레이용 — 워커 최신 검출(정규화 bbox)·인원·신호. 프론트가 화면크기로 복원."""
    wk = _worker(cid)
    if wk is None:
        return {"online": False, "detections": [], "person_count": 0, "signals": {}, "fired": [], "ts": 0}
    # safety_only(표시 단): 잡동사니(tv·laptop·의자 등) 제거 → 사람·보호구(NO-*)·위험물·차량·화재만.
    #   워커 이벤트/신호는 guard.detect 내부 out 으로 계산되므로 이 필터는 응답·오버레이 표시에만 영향(회귀 0).
    #   ts(3.8): 검출 갱신 시각(ms) — 확대뷰가 새 배치일 때만 BoxTracker.ingest 하도록. id 는 트랙 매칭용.
    # [T-E2E 유령박스] stale(미매칭 코스팅 트랙) 제외 — 표시는 실검출만. 이동 시 옛 위치에
    #   동결 박스가 TTL(1.2s)까지 잔류하던 유령의 서버측 차단(계측 근거: track_debug.jsonl).
    #   신호·판정은 워커 내부 out 기준이라 불변(이 경로는 오버레이 전용).
    dets = [d for d in wk._last_dets if _is_safety(d.get("class")) and not d.get("stale")]
    return {"online": bool(wk.state.get("running")), "detections": dets, "ts": int(wk._last_det_ts * 1000),
            "person_count": wk._last_pc, "signals": wk._last_sig, "fired": wk._last_fired}


@router.post("/cameras/{cid}/focus")
def cameras_focus(cid: str, payload: dict = Body(default={})):
    """확대뷰 포커스 부스트(3.8) — 선택 중 워커 fps 를 focus_fps(tuning hub.focus_fps, 기본5)로 임시 상향(등록 fps 가 더 높으면 그대로),
    해제/탭이탈 시 등록 fps 복원. 확대뷰는 워커 결과 재사용(자체 추론 없음)이라 다중 시청자여도
    워커 1개 → 부하 불변(중복 추론과의 차이). 재시작 없이 루프 간격만 바꿈."""
    import tuning
    import worker as _w
    c = _reg.get(cid)
    if not c:
        raise HTTPException(status_code=404, detail="없는 카메라")
    on = bool(payload.get("on"))
    base = float(c.get("fps", 2.0))
    # [B안③] payload.fps 로 부스트 값 임시 지정 가능(없으면 tuning 값) — fps 5↔8 A/B 실측용.
    #   dict Body 라 OpenAPI 스키마 불변. 남용 방지로 0.5~15 클램프(set_fps 자체엔 상한 없음).
    focus = float(payload.get("fps") or tuning.val("hub", "focus_fps", 5.0))
    focus = max(0.5, min(15.0, focus))
    # ★[2026-10-12 실측] 포커스는 **부스트**다 — 등록 fps 가 focus_fps 보다 높으면(웹캠 fps 30 등록) 예전 코드는 확대뷰를 열 때
    #   오히려 5 fps 로 **내렸다**(갱신 간격 119→254 ms, 박스가 더 뚝뚝 끊김). 등록 fps 를 하한으로 둔다(payload.fps A/B 도 동일).
    focus = max(focus, base)
    return _w.manager.set_fps(cid, focus if on else base)


@router.get("/cameras/{cid}/boxed.mjpg")
def cameras_boxed_stream(cid: str):
    """[2026-11-16 박스 그린 영상] 서버가 프레임에 박스를 그려 내보내는 연속 JPEG(MJPEG) — <img src> 로 바로 재생.
    ★B안(사용자 결정): 카메라가 주는 **모든 프레임**에 가장 최근 박스를 얹는다 — 영상은 카메라 속도, 박스는 추론 시간만큼
    뒤따른다. 캡처 스레드가 없는 소스(파일·sync 모드)는 A안(검출한 프레임 + 그 박스, 영상 장수 = 검출 주기)으로 폴백.
    표시 전용 — 판정·경보 무영향. 워커 없음 404 · 아직 프레임 없음 503. 표시 필터는 /detections 와 같다(안전 라벨·stale 제외)."""
    import boxed_view
    import worker as _w
    from fastapi.responses import StreamingResponse
    wk = _worker(cid)
    if wk is None:
        raise HTTPException(status_code=404, detail="없는 카메라/오프라인")
    if boxed_view.latest_view(wk) is None:
        raise HTTPException(status_code=503, detail="프레임 없음(워밍업)")

    def _view():                         # 워커가 재시작으로 바뀌어도 따라간다(매번 등록부에서 찾는다)
        return boxed_view.latest_view(_worker(cid))

    def _keep(d: dict) -> bool:
        return bool(_is_safety(d.get("class")) and not d.get("stale"))

    return StreamingResponse(boxed_view.frames(cid, _view, closing=_w.manager.is_closing, keep=_keep),
                             media_type=boxed_view.MEDIA_TYPE, headers={"Cache-Control": "no-store"})


@router.get("/cameras/{cid}/snapshot")
def cameras_snapshot(cid: str):
    """워커 최신 프레임 JPEG(파일소스 카드/미리보기 — WebRTC 미가용 시 폴링용)."""
    import cv2
    wk = _worker(cid)
    fr = getattr(wk, "_last_frame", None) if wk else None
    if fr is None:
        raise HTTPException(status_code=404, detail="프레임 없음(오프라인/워밍업)")
    # [P1a] 스냅샷은 화면·외부로 나가는 이미지 → 얼굴 비식별화. 워커의 최신 person 박스를 넘겨
    #   머리 영역을 확실히 가린다(원본 _last_frame 은 수정되지 않는다 — 검출 무영향).
    import privacy
    _pb = [d.get("bbox") for d in (getattr(wk, "_last_dets", None) or [])
           if d.get("class") == "person"]
    fr = privacy.anonymize_faces(fr, _pb)
    ok, buf = cv2.imencode(".jpg", cv2.resize(fr, (640, 360)), [cv2.IMWRITE_JPEG_QUALITY, 70])
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@router.post("/cameras/{cid}/test")
def cameras_test(cid: str):
    """연결 테스트 — source 에서 1프레임 잡기 성공 여부(+스냅샷 미리보기). 자격증명은 응답에 노출 안 함."""
    import base64
    import threading

    import cv2
    import worker as _w
    src = _reg.source_of(cid)
    if not src:
        raise HTTPException(status_code=404, detail="source 미등록")
    # [CODE_REVIEW M5-2] 예전엔 요청 스레드가 죽은 주소에 최대 123s(실측) 멈췄다. 워커와 같은 타임아웃 상수로
    #   열기·읽기를 걸고, 그래도 넘기면 응답을 먼저 돌려준다(캡처 스레드는 타임아웃 후 스스로 끝난다).
    box: dict = {}

    def _grab() -> None:
        cap = _w._open_capture(str(src))
        try:
            ok, fr = cap.read()
            box["ok"], box["fr"] = bool(ok), fr
        finally:
            cap.release()
    t = threading.Thread(target=_grab, daemon=True)
    t.start()
    t.join(timeout=_w._RTSP_TIMEOUT_MS / 1000.0 * 2 + 1.0)      # 열기 + 읽기 타임아웃 합 + 여유
    if t.is_alive():
        return {"ok": False, "error": f"연결 시간 초과({_w._RTSP_TIMEOUT_MS}ms) — 주소·네트워크 확인"}
    ok, fr = box.get("ok"), box.get("fr")
    if not ok or fr is None:
        return {"ok": False, "error": "프레임을 못 잡음(연결 실패/경로 오류)"}
    # [P1a] 연결테스트 미리보기도 응답으로 나가는 이미지다(person 박스 없음 → haar 만)
    import privacy
    fr = privacy.anonymize_faces(fr)
    _, buf = cv2.imencode(".jpg", cv2.resize(fr, (480, 270)), [cv2.IMWRITE_JPEG_QUALITY, 65])
    return {"ok": True, "snapshot": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()}


@router.get("/cameras/{cid}/zone")
def cameras_zone_get(cid: str):
    """카메라별 위험구역 폴리곤(정규화 좌표). 비어 있으면 워커는 전역 구역으로 폴백."""
    c = _reg.get(cid)
    if not c:
        raise HTTPException(status_code=404, detail="없는 카메라")
    pts = c.get("zone") or []
    return {"points": [{"x": float(p[0]), "y": float(p[1])} for p in pts], "count": len(pts)}


@router.post("/cameras/{cid}/zone")
def cameras_zone_set(cid: str, payload: dict = Body(...)):
    """카메라별 위험구역 저장. payload={"points":[{"x":..,"y":..}, ...]}(정규화 0~1).
    비우면 전역 구역 폴백. 실행 중 카메라는 워커를 재시작해 즉시 반영한다."""
    import worker as _w
    c = _reg.get(cid)
    if not c:
        raise HTTPException(status_code=404, detail="없는 카메라")
    pts = payload.get("points") or []
    zone = [[float(p["x"]), float(p["y"])] for p in pts]
    _reg.upsert(cid, zone=zone)
    restarted = False
    stop_error = ""
    if _worker(cid) is not None and (_reg.get(cid) or {}).get("enabled"):
        r = _w.manager.stop(cid)
        if r.get("ok", True):
            _start(cid)
            restarted = True
        else:                                             # [CODE_AUDIT #8] 정지 미완료면 이중 워커를 만들지 않는다
            stop_error = str(r.get("error") or "stop 실패")
            _LOG.error("구역 저장 후 워커 '%s' 재기동 보류 — %s", cid, stop_error)
    out = {"ok": True, "count": len(zone), "restarted": restarted}
    if stop_error:
        out["restart_error"] = stop_error
    return out


def _lan_ip() -> str:
    """이 머신의 아웃바운드 LAN IP(패킷 전송 없이 소켓 트릭). go2rtc WebRTC 후보용 — 감지 실패 시 127.0.0.1."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


# ── [CODE_REVIEW M5-3, 2026-09-06] go2rtc 수명 관리 ──────────────────────────────────────
#   예전엔 Popen 핸들을 버려 서버 종료 후 go2rtc 가 고아로 남았고(C4 실측 2개), 다음 기동은 포트가 잡혀 있으면
#   옛 프로세스를 그대로 썼다 — 런타임 yaml 은 매 기동 템플릿으로 덮어써도 옛 프로세스는 다시 읽지 않는다.
#   이제 ①우리가 띄운 프로세스는 핸들 + data/go2rtc.pid 로 추적 ②포트 점유자가 우리 PID 파일의 살아 있는
#   프로세스면 종료 후 재기동(yaml 재로드 대신) ③남의 프로세스면 손대지 않고 재사용 + 경고 ④서버 shutdown 에서
#   우리 것만 종료. 테스트: tests/test_go2rtc_lifecycle.py(전부 모킹).
import logging as _logging
import queue as _queue
import threading as _threading
from pathlib import Path as _Path

_LOG = _logging.getLogger("vigent.cameras")
_G2_ROOT = _Path(__file__).resolve().parent.parent.parent      # 테스트가 임시 루트로 바꾼다
_G2_PROC = None                                                 # 우리가 띄운 Popen 핸들(이 프로세스 수명)
_G2_RELAY = None                                                # [#40] go2rtc 출력 → 마스킹 → 파일 중계 스레드(_LogRelay)
_G2_LOGF = None                                                 # go2rtc stdout/stderr 파일 핸들(예전엔 열고 닫지 않았다)
import go2rtc_client as _g2c  # noqa: E402

_G2_PORT = _g2c.PORT   # [OPEN_ISSUES #16] 한 곳


class _LogRelay:
    """[OPEN_ISSUES #40, 2026-10-12] go2rtc stdout/stderr 파이프 → 자격증명 마스킹 → 로그 파일. **비차단.**

    실측 사고: go2rtc 1.9.14 가 producer 경고에 `url=rtsp://id:pw@…` 를 그대로 찍어 data/go2rtc.log 35,266줄에 평문이 남았다.
    설계:
      · 읽기 스레드는 파이프를 **항상** 비운다(큐가 꽉 차면 줄을 버리고 센다) → go2rtc 가 파이프 역압으로 멈추는 일이 없다.
      · 쓰기 스레드가 큐에서 꺼내 마스킹(camera_registry.scrub_credentials_strict)해 파일에 쓴다. 버린 줄이 있으면
        다음 줄 앞에 "[vigent-relay] N줄 생략(폭주)" 한 줄을 남긴다.
      · stop(join_s)은 종료 깃발 + 쓰기 스레드 join(상한) — 파이프가 안 닫혀도 서버 종료를 막지 않는다(읽기 스레드는 daemon,
        파이프 EOF 에서 끝난다). 파일 닫기는 호출자(_close_logf)가 한다.
    """
    MAX_QUEUE = 2000
    NOTICE = "[vigent-relay] %d줄 생략(폭주 — 큐 %d 초과)\n"

    def __init__(self, pipe, logf, name: str = "go2rtc") -> None:
        self._pipe, self._logf = pipe, logf
        self._q: _queue.Queue = _queue.Queue(maxsize=self.MAX_QUEUE)
        self._dropped = 0
        self._lock = _threading.Lock()
        self._stop = _threading.Event()
        self.written = 0
        self._reader = _threading.Thread(target=self._read, name=f"vigent-{name}-read", daemon=True)
        self._writer = _threading.Thread(target=self._write, name=f"vigent-{name}-write", daemon=True)

    def start(self) -> "_LogRelay":
        self._reader.start(); self._writer.start()
        return self

    def _read(self) -> None:
        try:
            for raw in iter(self._pipe.readline, b""):
                try:
                    self._q.put_nowait(raw)
                except _queue.Full:
                    with self._lock:
                        self._dropped += 1
        except Exception:  # noqa: BLE001  파이프 오류 → 중계 종료(go2rtc 자체는 무영향)
            pass
        finally:
            try:
                self._q.put_nowait(None)          # EOF 표식(꽉 차 있으면 writer 가 stop 깃발로 끝난다)
            except _queue.Full:
                pass

    @staticmethod
    def mask(raw: bytes) -> bytes:
        from camera_registry import scrub_credentials_strict
        return scrub_credentials_strict(raw.decode("utf-8", errors="replace")).encode("utf-8")

    def _flush_notice(self) -> None:
        with self._lock:
            n, self._dropped = self._dropped, 0
        if n:
            self._logf.write((self.NOTICE % (n, self.MAX_QUEUE)).encode("utf-8"))

    def _write(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=0.2)
            except _queue.Empty:
                if self._stop.is_set():
                    break
                continue
            if item is None:
                break
            try:
                self._flush_notice()
                self._logf.write(self.mask(item)); self.written += 1
                if self.written % 50 == 0:
                    self._logf.flush()
            except Exception:  # noqa: BLE001  파일 오류(디스크 풀 등) → 줄만 버린다
                pass
        try:
            self._flush_notice(); self._logf.flush()
        except Exception:  # noqa: BLE001
            pass

    def stop(self, join_s: float = 1.0) -> bool:
        """종료 깃발 → 쓰기 스레드 join(최대 join_s). True = 깔끔히 끝남."""
        self._stop.set()
        self._writer.join(join_s)
        return not self._writer.is_alive()

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped


def _close_logf() -> None:
    """[#40] 중계 스레드를 먼저 멈추고(상한 1 s) 파일을 닫는다 — 종료 경로(stop_go2rtc)에서 호출."""
    global _G2_LOGF, _G2_RELAY
    r = _G2_RELAY
    if r is not None:
        try:
            if not r.stop(join_s=1.0):
                _LOG.warning("go2rtc 로그 중계 스레드가 1 s 안에 끝나지 않음(파일은 닫는다)")
        except Exception:  # noqa: BLE001
            pass
        _G2_RELAY = None
    try:
        if _G2_LOGF is not None:
            _G2_LOGF.close()
    except Exception:  # noqa: BLE001
        pass
    _G2_LOGF = None


def _g2_pidfile() -> _Path:
    return _G2_ROOT / "data" / "go2rtc.pid"


def _g2_port_busy() -> bool:
    import socket
    try:
        with socket.create_connection((_g2c.HOST, _G2_PORT), timeout=0.5):
            return True
    except Exception:  # noqa: BLE001
        return False


def _pid_alive(pid: int) -> bool:
    """PID 가 살아 있는 go2rtc 인가. psutil 있으면 이름까지 대조, 없으면 tasklist(Windows)/ps.
    ★os.kill(pid, 0) 은 Windows 에서 TerminateProcess 를 부르므로 절대 쓰지 않는다."""
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and "go2rtc" in (p.name() or "").lower()
    except ImportError:
        return _pid_alive_fallback(pid)      # [OPEN_ISSUES #12] psutil 없으면 tasklist/ps 로 (예전엔 무조건 False → 고아 방치)
    except Exception:  # noqa: BLE001  권한/소멸 → 보수적으로 False(남의 것으로 간주)
        return False


def _pid_alive_fallback(pid: int) -> bool:
    """psutil 없는 환경: Windows `tasklist /FI "PID eq N"`, 그 외 `ps -p N -o comm=`. 이름에 go2rtc 가 있어야 True."""
    import subprocess
    import sys
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=5).stdout
        else:
            out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=5).stdout
        return "go2rtc" in out.lower()
    except Exception:  # noqa: BLE001
        return False


def _terminate_pid(pid: int) -> bool:
    try:
        import psutil
        p = psutil.Process(pid)
        if "go2rtc" not in (p.name() or "").lower():
            return False
        p.terminate()
        p.wait(timeout=5)
        return True
    except Exception:  # noqa: BLE001
        return False


def _read_pidfile() -> int | None:
    try:
        return int(_g2_pidfile().read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        return None


def ensure_go2rtc() -> bool:
    """go2rtc(WebRTC 변환기)가 안 떠 있으면 백그라운드로 기동. 바이너리 없으면 조용히 건너뜀(스냅샷 폴백).
    서버 startup 에서 1회 호출 → 카메라 확대뷰 실시간 재생 준비. 실패해도 서버·검출은 무중단.
    GO2RTC_LAN_IP 를 주입 → go2rtc.yaml 이 WebRTC 후보에 실제 UDP 바인딩 주소를 광고(127.0.0.1
    후보만으론 실브라우저 ICE 가 UDP 바인딩 불일치로 실패)."""
    global _G2_PROC, _G2_LOGF, _G2_RELAY
    import os
    if _g2_port_busy():
        old = _read_pidfile()
        if old is not None and _pid_alive(old):
            # 우리가 이전 서버 수명에서 띄운 go2rtc — 런타임 yaml 을 새로 쓰므로 재기동한다(재로드 API 대신)
            _LOG.warning("go2rtc 옛 인스턴스(pid %d) 발견 — 종료 후 재기동(런타임 설정 갱신)", old)
            _terminate_pid(old)
        else:
            _LOG.warning("포트 %d 를 다른 프로세스가 점유 중(우리 PID 파일 없음/불일치) — 손대지 않고 재사용. "
                         "확대뷰 스트림 등록이 옛 설정을 볼 수 있다", _G2_PORT)
            return True
    try:
        import subprocess
        root = _G2_ROOT
        # [S3] Windows 배포는 bin/go2rtc.exe(확장자 필수 — 무확장자 파일은 CreateProcess가 실행
        #   파일로 인식 못 함). 확장자 없는 bin/go2rtc(맥/리눅스)도 계속 지원 — 존재하는 쪽을 쓴다.
        binp = root / "bin" / "go2rtc.exe"
        if not binp.exists():
            binp = root / "bin" / "go2rtc"
        template = root / "config" / "go2rtc.yaml"
        runtime = root / "data" / "go2rtc.runtime.yaml"   # gitignore(data/) — 동적 스트림·비번은 여기에만 기록
        if not binp.exists():
            return False
        try:                                              # 매 기동 템플릿으로 초기화(옛 동적 스트림·비번 잔재 제거)
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:  # noqa: BLE001  런타임 사본 실패 → 템플릿에 비번 기록 방지 위해 go2rtc 미기동(스냅샷 폴백)
            return False
        env = dict(os.environ)
        env.setdefault("RTSP_URL", "")                    # 레거시 tapo 스트림용(없어도 무방)
        env["GO2RTC_LAN_IP"] = _lan_ip()                  # WebRTC 후보에 실제 LAN IP 광고(ICE 성립)
        env["GO2RTC_FFMPEG_BIN"] = _ffmpeg_bin(root)      # [웹캠 WebRTC] 템플릿 ffmpeg.bin — bin/ffmpeg.exe 우선, 없으면 PATH
        go2rtc_log = root / "data" / "go2rtc.log"
        # [Z-2] D그룹 회전 — 기동 시점에만 가능(파일이 Popen 수명 동안 열려 있어 세션 중 회전 불가)
        try:
            import tuning
            from retention import rotate_if_large
            rotate_if_large(go2rtc_log, tuning.val("retention", "ops_log_max_mb", 50))
        except Exception:  # noqa: BLE001  회전 실패해도 go2rtc 기동은 막지 않는다
            pass
        _close_logf()
        logf = open(go2rtc_log, "ab")   # noqa: SIM115  Popen 수명 동안 유지(관측성 — WebRTC 진단), stop_go2rtc 가 닫는다
        _G2_LOGF = logf
        # [#40] go2rtc 출력을 파일에 직접 잇지 않고 파이프로 받아 자격증명을 가린 뒤 쓴다(_LogRelay, 비차단).
        proc = subprocess.Popen([str(binp), "-config", str(runtime)], cwd=str(binp.parent),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        _G2_PROC = proc
        pipe = getattr(proc, "stdout", None)
        if pipe is not None:
            _G2_RELAY = _LogRelay(pipe, logf).start()
        try:
            _g2_pidfile().write_text(str(proc.pid), encoding="utf-8")
        except Exception:  # noqa: BLE001  PID 파일 실패해도 핸들은 남는다(이 수명 안에서는 정리 가능)
            pass
        _LOG.info("go2rtc 기동(pid %d)", proc.pid)
        return True
    except Exception:  # noqa: BLE001
        return False


def stop_go2rtc() -> bool:
    """서버 shutdown: **우리가 띄운** go2rtc 만 종료(핸들 → 없으면 PID 파일의 살아 있는 go2rtc). 남의 것은 무시."""
    global _G2_PROC
    done = False
    p = _G2_PROC
    if p is not None:
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()                      # [OPEN_ISSUES #12] terminate 뒤 5 s 안에 안 죽으면 강제 — 고아 금지
                    p.wait(timeout=5)
            done = True
        except Exception:  # noqa: BLE001
            pass
        _G2_PROC = None
    else:
        old = _read_pidfile()
        if old is not None and _pid_alive(old):
            done = _terminate_pid(old)
    _close_logf()
    try:
        _g2_pidfile().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    if done:
        _LOG.info("go2rtc 종료(우리가 띄운 인스턴스)")
    return done


def autostart_enabled() -> dict:
    """서버 startup 자동복원 — enabled 카메라 워커 자동 start. 실패해도 서버는 뜬다."""
    res: dict = {}
    for c in _reg.list_cameras():
        if not c.get("enabled"):
            continue
        try:
            _start(c["id"])
            res[c["id"]] = "started"
        except Exception as ex:  # noqa: BLE001
            res[c["id"]] = f"fail: {type(ex).__name__}"
    return res
