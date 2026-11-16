"""tests/_isolate.py — 테스트가 **운영 데이터를 건드리지 못하게** 하는 공용 격리 헬퍼(4단계 ④, 2026-09-06).

배경(실측): 2026-08-28 19:27 에 테스트 스위트가 운영 `data/alert_queue.db` 에 "[TZ] 위험구역…"·
"[TESTCAM] 화재/연기 감지" 행을 남겼다(CODE_REVIEW.md §4-0 #81·#82). 경로는
`test_endpoints_smoke` 가 앱 startup 으로 `alert_notify` 전송기를 **실제 dispatcher** 에 배선한 뒤,
다른 모듈의 `_process_frame`/`submit()` 호출이 그 배선을 타 큐에 기록한 것. notify.yaml 이 설정된
PC 라면 **시험 문구가 실제 텔레그램으로 나간다.**

사용: 큐·전송 경로에 닿을 수 있는 테스트의 setUp/setUpClass 에서
    from _isolate import isolate_alerts
    self.addCleanup(isolate_alerts())          # 또는 cls.addClassCleanup(isolate_alerts())
효과: ① alert_queue DB 를 임시 파일로 교체 ② 배경 스레드(재시도·전송) 정지 ③ 전송기 미주입 상태로
초기화 → 큐 기록은 임시 DB, 원격 전송은 불가. cleanup 이 원래 경로·상태를 복구한다.
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vigent-core"))


def isolate_logs():
    """vlog 의 로그 디렉터리(logs/vigent.log · logs/events.jsonl)를 임시 디렉터리로 바꾼다. [5단계 마무리, 2026-09-06]

    실측: test_machine_guard·test_bypass_paths_gated 가 /dispatch/relay 를 호출하며 vlog.log_event 로 운영
    logs/events.jsonl 에 dispatch_relay 행 3개를 남겼다(21:27~21:28). 이벤트 로거는 첫 호출 때 파일 핸들러를 만들어
    캐시하므로 캐시를 비우고, 루트 로거에 붙은 logs/ 아래 파일 핸들러도 임시 경로로 옮긴다. cleanup 이 전부 원복한다."""
    import logging

    import vlog

    tmp = tempfile.TemporaryDirectory(prefix="vigent_test_logs_", ignore_cleanup_errors=True)
    root_dir = Path(tmp.name)
    saved_dir = vlog._LOG_DIR
    saved_events = vlog._event_logger
    swapped: dict[int, logging.Handler] = {}   # id(임시 핸들러) → 원래 핸들러

    def _under(h: logging.Handler, d: Path) -> bool:
        base = getattr(h, "baseFilename", "")
        try:
            return bool(base) and Path(base).resolve().is_relative_to(d.resolve())
        except OSError:
            return False

    def _close(lg: logging.Logger | None) -> None:
        if lg is None:
            return
        for h in list(lg.handlers):
            lg.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass

    _close(saved_events)                     # 캐시된 events.jsonl 핸들러 분리(원복 때 다시 만든다)
    vlog._event_logger = None
    vlog._LOG_DIR = root_dir
    root = logging.getLogger()
    for h in list(root.handlers):            # 루트의 logs/vigent.log 파일 핸들러 → 임시 파일
        if _under(h, saved_dir):
            new = logging.FileHandler(root_dir / Path(getattr(h, "baseFilename")).name, encoding="utf-8")
            new.setFormatter(h.formatter)
            new.setLevel(h.level)
            root.removeHandler(h)
            root.addHandler(new)
            swapped[id(new)] = h

    def _restore() -> None:
        _close(vlog._event_logger)
        vlog._event_logger = None            # 다음 log_event 가 원래 경로에 새 핸들러를 만든다
        vlog._LOG_DIR = saved_dir
        for h in list(root.handlers):        # 임시 경로를 가리키는 핸들러는 전부 닫는다(격리 중 vlog.setup() 이 만든 것 포함)
            if not _under(h, root_dir):
                continue
            root.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
            old = swapped.pop(id(h), None)
            if old is not None:
                root.addHandler(old)
            else:                            # 격리 중 setup() 이 만든 핸들러 → 같은 이름으로 원래 디렉터리에 다시
                try:
                    saved_dir.mkdir(parents=True, exist_ok=True)
                    re = logging.FileHandler(saved_dir / Path(getattr(h, "baseFilename")).name, encoding="utf-8")
                    re.setFormatter(h.formatter)
                    re.setLevel(h.level)
                    root.addHandler(re)
                except Exception:  # noqa: BLE001
                    pass
        tmp.cleanup()

    return _restore


# ── 프로세스 전체 logs/ 격리 ─────────────────────────────────────────────────────────────────────
#   실측(2026-09-06 전체 스위트 전후 data/+logs/ 해시 비교): 개별 격리만으로는 루트 로거의 logs/vigent.log 가 테스트 로그로
#   10MB 회전(vigent.log·.1·.2 변경 3건)했다. unittest discover 는 모든 test 모듈을 먼저 import 한 뒤 실행하므로, 이 모듈이
#   import 되는 순간(어느 test 모듈이든 _isolate 를 쓰면) 루트 파일 핸들러·이벤트 로거를 임시 경로로 돌려 두면 실행 단계의
#   모든 테스트 로그가 운영 logs/ 를 건드리지 않는다. 프로세스 종료 때 원복(atexit). VIGENT_TEST_KEEP_LOGS=1 이면 끄기.
# ── ★[F-35, 2026-09-22] 테스트는 **망을 타지 않는다** ────────────────────────────────────────
#   실측 사고: 알림 채널 자가시험(getMe)을 기동 경로에 넣자 **전체 테스트가 실제 텔레그램에
#   접속했다**(test_startup_services 2건 실패로 드러남). 망이 없거나 느린 환경에서는 테스트가
#   불안정해지고, 토큰이 설정된 PC 에서는 **외부로 요청이 나간다.**
#   이 모듈이 import 되는 순간(어느 test 모듈이든) 자가시험을 꺼 둔다.
#   실제로 망을 태워 보려면 VIGENT_TEST_ALLOW_NET=1.
if os.environ.get("VIGENT_TEST_ALLOW_NET", "") != "1":
    os.environ.setdefault("VIGENT_NOTIFY_SELFTEST", "0")

_PROCESS_LOG_RESTORE = None
if os.environ.get("VIGENT_TEST_KEEP_LOGS", "") != "1":
    try:
        _PROCESS_LOG_RESTORE = isolate_logs()
        atexit.register(_PROCESS_LOG_RESTORE)
    except Exception:  # noqa: BLE001  격리 실패가 테스트 자체를 막지는 않는다(해시 비교가 잡는다)
        _PROCESS_LOG_RESTORE = None


def isolate_alerts():
    """운영 alert_queue.db·전송기를 임시 상태로 바꾸고, 원복 함수를 돌려준다(addCleanup 용).
    [5단계 마무리] logs/ 격리(isolate_logs)를 포함한다 — 경보·릴레이 경로는 vlog.log_event 로 events.jsonl 에도 쓴다."""
    import alert_notify
    import alert_queue
    import data_engine

    restore_logs = isolate_logs()
    tmp = tempfile.TemporaryDirectory(prefix="vigent_test_alerts_")
    saved_db = alert_queue._DB_PATH
    saved_sender = alert_queue._sender
    # [M6-10 후속, 2026-09-06 실측] mark_sent(critical/high) 가 자동 pin 을 쓰므로 pin 목록도 격리한다 — 격리 전에는
    #   임시 DB 의 행 id 로 만든 "alert:1" pin 이 운영 data/retention/pinned.json 에 남았다.
    saved_pins = (data_engine._PINNED, data_engine._PINNED_LEGACY)
    data_engine._PINNED = Path(tmp.name) / "retention" / "pinned.json"
    data_engine._PINNED_LEGACY = Path(tmp.name) / "evidence" / "pinned.json"
    alert_queue.stop()
    alert_notify.stop()
    alert_queue._reset_for_test(Path(tmp.name) / "alert_queue.db")
    alert_notify.reset_for_test()          # 큐·sender·게이트 초기화(스레드 없음)

    def _restore() -> None:
        alert_queue.stop()
        alert_notify.stop()
        alert_notify.reset_for_test()
        alert_queue._reset_for_test(saved_db)
        alert_queue._sender = saved_sender
        data_engine._PINNED, data_engine._PINNED_LEGACY = saved_pins
        tmp.cleanup()
        restore_logs()

    return _restore


def isolate_data_dirs():
    """data_engine(증거·인식 로그·pin)·scribe(위험성평가서) 저장 경로를 임시 디렉터리로 바꾼다.

    실측(2026-09-06 전체 스위트 전후 data/ 해시 비교): test_data_engine_report 가
    data/evidence/·data/risk_assessments/ 에 파일을 만들고, test_privacy_failure_policy 가
    data/recognition/events_<날짜>.jsonl 에 줄을 덧붙였다. 상대경로 계약(`data/evidence/...`)을
    지키기 위해 임시 루트 아래에 같은 `data/` 구조를 만든다."""
    import data_engine  # [⑤-2] scribe(위험성평가서) 경로는 모듈 삭제로 제외

    tmp = tempfile.TemporaryDirectory(prefix="vigent_test_data_")
    root = Path(tmp.name)
    saved_de = (data_engine._ROOT, data_engine._EVIDENCE, data_engine._RECOG, data_engine._PINNED)
    saved_legacy = data_engine._PINNED_LEGACY
    data_engine._ROOT = root
    data_engine._EVIDENCE = root / "data" / "evidence"
    data_engine._RECOG = root / "data" / "recognition"
    data_engine._PINNED = root / "data" / "retention" / "pinned.json"     # [M6-1] evidence 폴더 밖
    data_engine._PINNED_LEGACY = data_engine._EVIDENCE / "pinned.json"

    def _restore() -> None:
        (data_engine._ROOT, data_engine._EVIDENCE, data_engine._RECOG, data_engine._PINNED) = saved_de
        data_engine._PINNED_LEGACY = saved_legacy
        tmp.cleanup()

    return _restore


def isolate_no_token():
    """[2026-10-11] 무인증 기본 동작을 검증하는 테스트용 — main 의 토큰 상수와 환경변수를 **둘 다** 비우고 원복 함수를 돌려준다.

    실측: 개발기 `.env` 에 VIGENT_API_TOKEN 이 있으면 main 이 import 시점에 `_API_TOKEN` 상수로 읽어 토큰 모드가 되고,
    그 뒤 환경변수만 지워도 상수는 그대로라 /health 등이 401 → Mac(토큰 없음)에서 쓴 테스트 22건이 Windows 개발기에서만
    깨졌다. test_endpoints_smoke 가 2026-08-20 에 같은 함정을 겪고 `main._API_TOKEN = ""` 로 고정한 관례를 한 곳에 모은다.
    사용: self.addCleanup(isolate_no_token())   (setUp 첫 줄, TestClient 만들기 전)"""
    import main
    saved_const = main._API_TOKEN
    saved_env = os.environ.get("VIGENT_API_TOKEN")
    main._API_TOKEN = ""
    os.environ.pop("VIGENT_API_TOKEN", None)

    def _restore() -> None:
        main._API_TOKEN = saved_const
        if saved_env is not None:
            os.environ["VIGENT_API_TOKEN"] = saved_env
        else:
            os.environ.pop("VIGENT_API_TOKEN", None)

    return _restore


def isolate_shutdown():
    """[A-7, 2026-10-11] 진짜 `main._shutdown()` 을 끝까지 돌리는 테스트용 — 종료가 남기는 전역 상태를 원복한다.

    실측: `_shutdown` 이 `worker.manager.begin_shutdown()` 으로 전역 매니저에 "종료 중" 깃발을 세우고(이후 start 거부),
    `starvation_guard.stop()` 이 `_stop` 깃발을 세운다. 테스트 프로세스는 종료되지 않으므로 깃발이 그대로 남아, 뒤에 도는
    `test_constants_config` 의 `manager.start` 가 거부됐다(순서 의존 ERROR 1건). 종료를 실제로 돌리는 테스트는 setUp 에
        self.addCleanup(isolate_shutdown())
    을 넣는다. (`worker.manager` 전체를 mock 으로 바꾸는 테스트는 깃발이 가짜에 서므로 불필요.)"""
    import readiness
    import starvation_guard
    import worker

    saved_cancel = readiness._cancel.is_set()          # [A-8] 예열 취소 깃발도 원복
    saved_warm = readiness._thread
    saved_closing = worker.manager.is_closing()
    saved_stop = starvation_guard._stop.is_set()
    saved_thread = starvation_guard._thread

    def _restore() -> None:
        if saved_closing:
            worker.manager.begin_shutdown()
        else:
            worker.manager.reopen()
        if saved_stop:
            starvation_guard._stop.set()
        else:
            starvation_guard._stop.clear()
        starvation_guard._thread = saved_thread
        if saved_cancel:
            readiness._cancel.set()
        else:
            readiness._cancel.clear()
        readiness._thread = saved_warm

    return _restore


def isolate_warmup():
    """[2026-10-12] lifespan(TestClient with/__enter__)을 도는 테스트용 — 예열 스레드와 go2rtc 기동·**종료**를 막는다.

    실측: lifespan 의 _startup 이 readiness.start_background(모델 로드 20 s+, GPU·CPU 점유)와 ensure_go2rtc(bin/go2rtc.exe 가 있으면
    실제 프로세스)를 띄우고, 테스트가 끝나도 예열 스레드는 모델 로드를 끝까지 돈다(A-8 취소 깃발은 on_ready 만 막는다).
    그 뒤에 오는 타이밍 테스트(test_audit_fix10_watchdog, 0.12 s 창)가 굶어 전체 게이트에서만 깨졌다(2026-10-12, 2회 연속).
    예열 자체를 검증하는 테스트(test_readiness_warmup·test_warmup_holds_detect_lock·test_ppe_compare_harness·test_startup_services)
    에는 쓰지 않는다. 사용: self.addCleanup(isolate_warmup())  또는 cls.addClassCleanup(isolate_warmup())"""
    from unittest import mock

    import readiness
    from routers import cameras as _cams

    # ★[2026-10-12 실사고] ensure_go2rtc 만 막고 stop_go2rtc 는 안 막았다 — lifespan 종료(진짜 _shutdown)가 stop_go2rtc 를 부르면
    #   테스트 프로세스엔 _G2_PROC 핸들이 없어 **PID 파일 경로**로 떨어지고, 그 파일은 저장소 data/go2rtc.pid = 개발기에서 **켜져 있는
    #   실제 서버의 go2rtc** 다. 전체 스위트를 서버 옆에서 돌리자 go2rtc 가 죽고 PID 파일이 지워져 웹캠 워커가 RTSP 재연결 루프에
    #   빠졌다(11:02:27~, 허브는 스냅샷 폴백으로 "뚝뚝 끊김"). 테스트는 기계 상태를 건드리면 안 된다(CLAUDE.md 게이트 3 ★).
    patches = [mock.patch.object(readiness, "start_background"),
               mock.patch.object(_cams, "ensure_go2rtc", return_value=False),
               mock.patch.object(_cams, "stop_go2rtc", return_value=False)]
    for pt in patches:
        pt.start()

    def _restore() -> None:
        for pt in reversed(patches):
            try:
                pt.stop()
            except RuntimeError:
                pass
    return _restore

