# 비전 / 에이전트 분리·병합 계획 — 2026-10-12 조사

> 목적: 이 저장소는 원래 비전(검출·추적·경보)만 개발하려던 곳이고, 에이전트는 별도 저장소에서 만들어 나중에 병합할 계획이었다.
> 그런데 `agents/` 와 LLM·VLM 을 쓰는 화면이 섞여 있어, 병합 계획을 세우기 위해 **현재 상태를 코드·git 이력으로 조사**했다.
> 근거는 전부 2026-10-12 HEAD(`7d1dc35`)의 코드·`git log`·개발기 실측이고, 추정은 [추정]으로 표시한다. 이 문서는 조사 결과이며 코드는 바꾸지 않았다.

## 0. 먼저 알아야 할 사실

- **`agents/` 폴더는 이름과 달리 절반이 비전 핵심이다.** `agents/guard.py`(1,154줄)가 RF-DETR 검출·추적기 본체이고, `agents/dispatcher.py`(516줄)가 텔레그램·이메일·웹훅 전송기다. [agents/__init__.py](../vigent-core/agents/__init__.py) 주석도 "코어 2종(Guard·Dispatcher)은 필수, LLM 에이전트 4종(Analyst·Scribe·Copilot·SafetyManager)은 선택 import" 로 이미 구분해 두었다(2026-09-06 감사 결정).
- 6-에이전트 골격은 **첫 커밋(2026-06-19 `e57500f`)부터** 있었다. 메타 프롬프트 §3 설계가 그렇다. "나중에 섞인" 것이 아니라 설계 시작점부터 한 저장소였다.
- 핵심 코드 전체 18,004줄[wc 실측] 중 에이전트 층 모듈 합산 약 4,700줄. 여기에 `routers/safety_core.py`(850줄, 57개 라우트)의 **과반이 에이전트 층 라우트**인데 한 파일에 섞여 있다 — 분리의 첫 걸림돌.

## 1. 세 묶음 분류

| 묶음 | 모듈 | 라우터(라우트 수) | 화면 |
|---|---|---|---|
| **비전 핵심** (검출·추적·위험 판정·경보 전송·증거 저장·카메라 관리) | `worker`(1,518) · `agents/guard` · `agents/dispatcher` · `rfdetr_service`(검출부) · `alert_notify` · `alert_gate` · `alert_queue` · `data_engine`(이벤트·증거 기록) · `privacy`(얼굴 모자이크) · `proximity` · `zone_debounce` · `zone_tile` · `zone_geom` · `press_zone` · `critical_controls` · `camera_registry` · `readiness` · `starvation_guard` · `health_status` · `health_watch` · `relay` · `device` · `ort_tune` · `isolated_detect` · `vision_loader` · `labels` · `defaults` · `go2rtc_client` · `notify_heartbeat` · `retention` · `retention_scheduler` · `pose/` · `ergonomics` · `agents/analyst`(규칙 가산 판정, LLM 아님) | `cameras`(11) · `zone`(7) · `detect`(4 — 단 `/rfdetr/vlm` 1개는 VLM) · `dispatch`(1) · `tapo`(3) · `recognition`(6, 이벤트 조회) · `safety_core` 중 `/worker/*`·`/workers/*`·`/status`·`/alerts/*`·`/safety/judge`·`/safety/sensor`·`/safety/posture`·`/evidence/search`·`/{theme}` | `/safety-hub` · `/safety-local` · `/safety/setup` · `/dashboard` · (`/safety`·`/safety-pro` 는 혼합: 검출은 핵심, VLM 요약 버튼은 에이전트 층) |
| **에이전트·LLM 층** (agents/, LLM·VLM 호출, 서류 생성, 음성, 질의응답) | `agents/scribe`(823) · `agents/copilot` · `agents/safety_manager` · `llm_provider` · `safety_brain` · `safety_rag` · `vlm_confirm` · `scene_vlm` · `ml/vlm_risk_summary` · `incident` · `ppe_check` · `liveguide` · `behavior` · `voice` · `hazard_rules` · `hub` · `demo` · `quote` · `evaluator` · `tbm_store` · `audit_store`(승인 기록) · `dashboard.py`(통계 화면) | `incident`(3) · `ppe`(7) · `tbm`(6) · `safety_core` 의 나머지 대부분: `/hub`·`/home`·`/safety/brain*`·`/safety/voice*`·`/safety/guide`·`/safety/manager/*`·`/safety/risk-assessment*`·`/report/safety`·`/safety/auto*`·`/safety/eval*`·`/safety/demo*`·`/safety/quote`·`/safety/live/analyze`·`/safety/behavior/analyze`·`/safety/context`·`/safety/confirm` | 26개 화면 중 18개(서버 AI 를 쓰는 화면 + 서류 화면 전부) |
| **공통 기반** (인증, 설정, DB, 감사 기록) | `main.py` · `app_state` · `web_util` · `auth_session` · `ws_auth` · `tuning` · `runtime_config` · `data_paths` · `vlog` · `setup_console` · `legal_whitelist` · `routers/system`(`/health`) | `system`(3) · `safety_core` 의 `/site/config`·`/notify/config` | `/login` |

화면 26개의 주소·용도·로그인·서버 AI 사용 여부 표는 2026-10-11 대화 기록에 있다(이 문서 범위 밖).

## 2. 의존 방향과 끄는 스위치

### 비전 핵심 → 에이전트 층 import (실측 5곳)

| # | 위치 | 무엇을 | 성격 |
|---|---|---|---|
| 1 | `app_state.py` · `main.py` | `agents` 패키지 전체(`build_agents`) | `agents/__init__` 이 LLM 4종을 선택 import 로 감싸 **모듈이 없어도 Guard·Dispatcher 만으로 서버가 뜬다**(주석에 "4모듈 import 차단 실험 성공" 기록). 해당 라우트는 503 |
| 2 | [routers/zone.py:111](../vigent-core/routers/zone.py#L111) | `vlm_confirm.confirm()` | 브라우저 구역 침입 경로에서 요청 payload 에 `vlm_confirm` 이 있을 때만(opt-in) |
| 3 | [routers/recognition.py:24](../vigent-core/routers/recognition.py#L24) | `agents.scribe.RULE_KB` | 규칙 이름→한국어 라벨 **사전만** 빌려 씀. 데이터 의존 — 사전을 핵심으로 옮기면 끊긴다 |
| 4 | [routers/system.py:113](../vigent-core/routers/system.py#L113) | `llm_provider.status()` | `/health` 에 LLM 상태를 싣는다(예외 시 `unknown` 폴백) |
| 5 | [routers/detect.py](../vigent-core/routers/detect.py) | `rfdetr_service` | 한 모듈 안에 검출(`detect`)과 VLM(`VLMService`)이 같이 있다 |

- **`worker.py` 는 에이전트·LLM 모듈을 하나도 import 하지 않는다**[실측: import 목록 전수]. 이벤트 기록(`data_engine`)과 통보(`alert_notify`)만 부른다.
- 반대 방향(에이전트 층 → 핵심)은 많지만 그것은 정상이다. `hazard_rules` 가 `agents.scribe` 를 import 하지만 사용처는 `incident`·`scene_vlm`(둘 다 에이전트 층)뿐이다.

### 에이전트 층 없이 비전만 켤 수 있는가

지금도 사실상 그렇게 돌고 있다. 단, **단일 스위치는 없다.**

- VLM: `mlx_vlm` 은 Mac 전용이고 Windows 개발기에는 **미설치**[실측 `importlib.util.find_spec`] → `VLMService.available=False` 로 모든 VLM 경로가 폴백. `vision.yaml vlm:` 블록은 모델명·`fallback: none` 만 있고 on/off 키가 없다.
- LLM: `llm_provider` 는 `OPENAI_API_KEY` 가 없으면 `None` 을 돌려주고 호출자가 규칙 폴백(기본 provider openai, `VIGENT_LLM_PROVIDER=anthropic` 선택 가능). Ollama 는 2026-07-14 제거.
- 라우트·화면은 어떤 경우에도 **전부 마운트**된다. 지금의 "끄기" 는 "폴백으로 동작" 이지 "노출 안 함" 이 아니다.
- 테스트 의존(파일 수, `tests/test_*.py` 158개 중): `agents.guard` 13 · `agents.dispatcher` 9 · scribe 5 · analyst 2 · hub 2 · vlm 1 · incident 1 · `llm_provider`·`safety_brain`·`voice`·`liveguide`·`safety_manager` **0**. → `agents/` 폴더를 통째로 옮기면 안 된다(Guard·Dispatcher 의존이 크다).

## 3. 들어온 시점 (git 이력)

| 시기 | 커밋 | 들어온 것 |
|---|---|---|
| 2026-06-19 | `e57500f` | 2단계 골격에서 **6-에이전트 스텁이 처음부터** 포함(base·guard·dispatcher·analyst·scribe·copilot) |
| 06-20 | `01bf382` · `323d3f8` | mlx-vlm 위험 요약(`ml/vlm_risk_summary`), `/safety-pro` 화면에 VLM 호출이 라우트로 들어옴 |
| 06-22 | `6ea73b6` · `747a23d` · `64fbc81` | `vlm_confirm`(CNN→VLM 2차 확정), 자동처리 콘솔+`audit_store`, `tbm_store` |
| 06-24~28 | 8개 커밋 | `safety_brain`·`safety_rag`(24) · `demo`·`quote`(24) · `incident`·`voice`·`hub`(25) · `liveguide`·`behavior`·`evaluator`(26) · `ppe_check`(28) — **5일간 기능 화면 집중 추가** |
| 07-02~03 | `de98915` · `6a3b534` · `374515a` | `llm_provider`(OpenAI 종합의견), `scene_vlm`, `safety_manager`, `hazard_rules` |
| 07-14 | — | Ollama 제거, OpenAI 단일화 |
| 07-16 | `9d467b9` 등 | P1-7 라우터 분할로 incident·ppe·tbm 이 별도 라우터가 됨(`safety_core` 는 그대로 혼합) |
| 08-06 이후 | — | 기능 추가 없음. 이후 수정은 감사·보안·정리뿐 |

마지막으로 손댄 날[`git log -1`]: `voice` 06-25 · `liveguide` 06-28 · `safety_manager` 07-03 · `hub` 07-04 · `safety_brain`·`safety_rag` 07-15 · `ppe_check` 07-17 · `llm_provider` 08-17 · `incident` 09-28(정리) · `scribe` 10-10(XSS 수정) · `copilot` 10-08(법령 게이트). 즉 에이전트 층은 6월 3주간 만들어진 뒤 **두 달 넘게 기능 동결** 상태다.

## 4. 비전이 내보내는 결과물 형식

### 이벤트 종류 — 워커가 발화하는 규칙 (★2026-10-12 정정: 첫 조사는 `fired.append` 만 세어 3개를 빠뜨렸다)

| rule | level | 뜻 | 출처 |
|---|---|---|---|
| `zone_intrusion` | high | 위험구역 내 작업자(체류 확정·구역-타일 회수) | [worker.py:311-334](../vigent-core/worker.py#L311-L334), :1149 |
| `ppe_missing` | high | 보호구 미착용 | :339 |
| `fire_smoke` | critical | 화재/연기 | :341 |
| `proximity_hazard` | high | 장비 근접 — 문구는 '차량'/'지게차' + (검출 라벨 원문) | :354-357, `proximity.proximity_note` |
| `crowd_density` | mid | 인원 밀집 | :362 |
| `immobility` | high | **장시간 무동작(45초 이상 정지) — 확인 필요** (모션 분석) | [worker.py:649](../vigent-core/worker.py#L649) |
| `rapid_motion` | mid | 급격한 움직임 (모션 분석) | :640 |
| `ergonomic_risk` | low | 근골격계 부담 자세 — **기록 전용**(RTMPose 스레드, 2026-09 대표 결정) | :516, `ergonomics.py` |

워커 밖 경로: 브라우저 구역 침입·자세(`/safety/zone`·`/safety/posture` — `zone_intrusion`·`ergonomic_risk`, `source: browser`),
IoT 센서 `/safety/sensor`(o2·h2s → `asphyxiation`, co·gas → `gas_alarm`, temp → `heat_stress`, 등급 critical, cam 키 `sensor:<종류>`).
등급 이름은 2026-10-12 에 `mid` 로 통일했다(`658fe09`): on_severity 키 = critical·high·mid·low, 원격 전송은 critical·high 만.

브라우저·센서 경로는 출처 꼬리표가 붙는다(`source: browser`; `alert_notify` 의 cam 키 `sensor:<종류>`·`browser_zone:<cam>`·`brain`·`manual`).

### 저장물 (루트는 저장소 `data/` 또는 `VIGENT_DATA_DIR`)

| 산출물 | 위치·형식 | 필드 |
|---|---|---|
| 인식 로그 | `data/recognition/events_YYYYMMDD.jsonl`, 1줄 1이벤트([data_engine.log_event](../vigent-core/data_engine.py#L152)) | `ts`(KST ISO, `+09:00`) · `date` · `time` · `rule` · `level` · `score` · `site`(★실제 값은 **카메라 이름**) · `note`(한국어 자유문) · `evidence`(상대경로) · 선택 `privacy_failed`·`source`·`logged:false` |
| 증거 사진 | `data/evidence/YYYYMMDD/ev_YYYYMMDD_HHMMSS_<rule>_<level>.jpg` | 얼굴 모자이크 적용(`tuning privacy.face_anonymize: true`). 30일 뒤 보존 스윕이 삭제. 지키려면 `data/retention/pinned.json` 에 고정 |
| 경보 큐 | `data/alert_queue.db` sqlite 테이블 `alerts` | `id, created_at(epoch), level, message, meta(json: evidence·note), status(pending/sent/dead), attempts, next_attempt_at, last_error, sent_at, dead_at, channels_sent, channels_failed`. 선기록 후전송(write-ahead) 큐 — **읽기 전용으로만** 볼 것 |
| 실시간 조회 | `GET /cameras/{cid}/detections` | `{online, detections[{tid, label, bbox[4], conf, hits, misses, age_ms, fresh}], ts(ms), person_count, signals{}, fired[]}` |
| 그 외 조회 | `GET /cameras` · `/workers` · `/cameras/{cid}/snapshot` · `/recognition/*`(6개) · `/evidence/search` · `/safety/auto/feed`(이벤트+승인+핀 병합) | 토큰 모드면 Bearer 또는 세션 쿠키 필요 |
| 카메라 등록부 | `data/cameras.json`(마스킹) + `data/camera_secrets.json`(원본 주소) | — |
| 운영 이벤트 로그 | `logs/events.jsonl`(`vlog.log_event`) | 현재 호출부는 `routers/dispatch.py`(릴레이) 1곳뿐 |

### 별도 에이전트가 받아 쓰려면 알아야 할 것

- **푸시 통로가 없다.** 웹소켓은 `/tapo/ws` 하나뿐이고 이벤트 전용이 아니다. 외부 에이전트는 jsonl 파일을 꼬리 추적하거나 API 를 폴링해야 한다. 유일한 푸시는 Dispatcher 의 **웹훅**(notify.yaml `webhook_url`)인데 경보 발송용이라 게이트(시간당 상한·반복 억제)를 거친 뒤의 것만 온다. → OPEN_ISSUES #36
- 규칙 이름의 한국어 라벨 사전 `RULE_KB` 가 에이전트 층(`agents/scribe.py`)에 있다. 계약으로 삼으려면 핵심으로 옮겨야 한다.
- 워커 경로의 `score` 는 **항상 0.0**이다[실측: [worker.py:1185](../vigent-core/worker.py#L1185) 가 `score` 를 넘기지 않아 기본값]. → OPEN_ISSUES #37
- `site` 필드에는 현장명이 아니라 **카메라 이름**이 들어간다[실측: `site=ctx.name`]. → OPEN_ISSUES #38
- 증거는 30일 뒤 사라지므로 에이전트가 참조할 건은 핀을 걸거나 복사해야 한다.
- 재해 분석(`/safety/incident/analyze`)·보호구 점검 결과는 **저장되지 않고 응답으로만** 돌아간다[실측: `incident.py` 에 파일 쓰기 없음]. 서류로 남는 것은 위험성평가서(`data/risk_assessments/ra_*.html+json`)·TBM(`data/tbm/tbm_*.json`)·승인 기록(`data/audit/audit_*.jsonl`)뿐이다.

## 4-1. 진행 상태 (2026-10-12 밤)

| 단계 | 커밋 | 상태 |
|---|---|---|
| ①-1 보관 | 태그 `archive/with-agents-20261012` · 브랜치 `archive/with-agents` (= `075aad4`) · 기록 `8f14719` | 로컬 생성 완료, push 는 대표 |
| ②-a RULE_KB → `rule_labels.py` | `9e7211e` | 완료, OpenAPI 111/111 |
| ②-b VLM → `vlm_service.py` | `9dfe095` | 완료, OpenAPI 111/111 |
| ②-c safety_core 분할 → `routers/agentic.py` | `f9579c2` | 완료, OpenAPI 111/111 |
| ③ 스위치 `VIGENT_AGENTS`(기본 1) | 이 커밋 | 완료 — 켜짐 1,043 OK/111 · 꺼짐 1,043 OK(skip 17)/62 |
| ④ 끈 상태 실기 확인 | [docs/review/VISION_ONLY_CHECK_20261012.md](../docs/review/VISION_ONLY_CHECK_20261012.md) | 대표 완료(2026-10-12 03:0x) — 기동·검출·경보·중지·종료 정상. **발견 4건(OPEN_ISSUES #39~#42)**, 그중 #39(허브 이벤트 목록 404)는 ⑤ 선결 |
| ⑤-1 라우터·화면 삭제 | `c2456fe` | 14파일 2,068줄 · OpenAPI 111→62 · gate.ps1 삭제 파일 오탐 수정 |
| ⑤-2 모듈 삭제 | `eccc5f2` | 27파일 4,891줄 · 핵심→에이전트 import 5곳 해소 · tuning vlm 섹션 제거 |
| ⑤-4 설정·패키지·스크립트 | `66e99bb` | 14파일(vision.yaml 3섹션×4 · corpus·templates·ppe_rules · requirements-agents·mlx · .env.example LLM 키 · 스크립트 6) |
| ⑤-5 스위치 제거·첫 화면·문서 | 이 커밋 | `VIGENT_AGENTS`·vision 기준선·tests/_agents 삭제 · `/` → `/safety-hub` 302 · CLAUDE/README/OPEN_ISSUES 갱신 |
| ⑤-3 브라우저 화면(`/safety`·`/safety-local`·전용 JS) | **보류(대표)** | 두 화면이 아직 부르는 사라진 주소: `/safety` → /report/safety·/safety/auto·/safety/brain(+activities·inspect)·/safety/context·/safety/demo·/safety/live/analyze·/safety/ppe/{catalog,check,rules}·/safety/quote·/safety/tbm (13) · `/safety-local` → /report/safety·/safety/auto·/safety/auto/feed·/safety/tbm (4). 지우지 않고 목록만 기록(OPEN_ISSUES #46) |

## 5. 분리 방안

### 방안 A. 그대로 두고 스위치로 끄기

- 할 일: `VIGENT_AGENTS=0`(또는 `vision.yaml agents.enabled`) 하나로 ① LLM 에이전트 4종 미생성 ② incident·ppe·tbm 라우터 미등록 ③ `safety_core` 안의 에이전트 라우트 미등록. ③ 때문에 어차피 `safety_core` 를 둘로 쪼개야 한다.
- 장점: 코드 이동이 없어 테스트·문서 링크가 안 깨진다. 반나절급[추정].
- 단점: 저장소에는 그대로 남아 "병합" 의 대상이 되지 못한다. 스위치가 꺼진 조합은 CI 가 안 돌리면 금방 썩는다. OpenAPI 기준선(111)이 스위치 상태에 따라 달라져 게이트 규칙을 손봐야 한다.

### 방안 B. 별도 폴더로 격리 (추천 — 먼저 할 것)

- 할 일: 에이전트 층 모듈 22개와 라우터 3개를 `vigent-core/agentic/`(이름은 예시) 아래로 옮기고, `safety_core` 의 에이전트 라우트를 `agentic/routes.py` 로 뽑아 `register(app)` 한 번으로 붙인다. 핵심 쪽에는 ① `RULE_KB` 사전 ② 이벤트 읽기 API ③ `/safety/judge`(Analyst 규칙 판정)만 남긴다. `agents/` 폴더는 Guard·Dispatcher 만 남기고 이름을 바꾸거나 그대로 둔다. 핵심→에이전트 import 5곳을 전부 인터페이스(사전·status 함수) 뒤로 숨긴다.
- 장점: 경계가 폴더로 보여서 "핵심이 에이전트를 import 하면 안 된다" 를 정적 검사 테스트 한 줄로 지킬 수 있다. 방안 A 의 스위치가 `register()` 호출 한 줄이 된다. 라우트 경로가 같으면 OpenAPI 111 불변이고 화면 주소도 그대로다. 나중에 C 로 가는 준비가 된다.
- 단점: import 경로 변경으로 테스트 약 10파일·문서 링크 수십 곳 수정[추정]. 1~2일급[추정].

### 방안 C. 저장소에서 떼어내기

- 할 일: B 가 끝난 `agentic/` 를 별도 저장소로 옮기고, 비전은 §4 의 산출물 계약(이벤트 jsonl·증거 경로·조회 API·웹훅)만 보장한다. 떼어낸 쪽은 자체 FastAPI 앱으로 뜨거나 플러그인으로 다시 붙는다.
- 장점: 원래 계획과 일치한다. 비전 저장소의 게이트가 가벼워지고(테스트 1,035 중 에이전트 층은 10 파일 안팎), VLM·LLM 의존성(mlx-vlm·openai)이 비전 배포본에서 빠진다.
- 단점: 지금 당장은 손해가 크다. ① 인증·설정·`/health`·설정 콘솔 같은 공통 기반을 두 저장소가 나눠 가져야 하고 ② 화면 18개가 홈(`/hub`)까지 포함해 옮겨가 비전 저장소엔 관제 화면 4개만 남으며 ③ 이벤트 푸시 통로가 없어 파일 폴링 계약부터 새로 만들어야 한다. 에이전트 저장소가 아직 없는 지금 하면 두 쪽 다 반쪽이 된다.

### 권장 순서

**B → (B 안에서 스위치 A) → 별도 에이전트 저장소가 실제로 생겨 §4 계약을 소비하기 시작할 때 C.**

B 를 하기 전에 각각 한 커밋으로 먼저 끝내면 뒤가 쉬워지는 것 3개:
1. `RULE_KB` 를 핵심으로 이동(의존 #3 해소)
2. `rfdetr_service` 의 `VLMService` 를 별 파일로 분리(의존 #5 해소)
3. `safety_core.py` 를 비전/에이전트 두 라우터로 분할(OpenAPI 111 불변 확인)

## 부록. 이 문서를 다시 만드는 법

- import 그래프: `vigent-core/**/*.py` 의 `import`/`from` 을 정규식으로 모아 로컬 모듈만 남긴다(세션 스크립트 `import_graph.py`, 저장소 밖).
- 첫 커밋: `git log --diff-filter=A --follow --format="%h %ad %s" --date=short -- vigent-core/<파일>`
- 마지막 수정: `git log -1 --format="%ad %h" --date=short -- vigent-core/<파일>`
- 테스트 의존: `grep -lE "import <모듈>|from <모듈> import" tests/test_*.py | wc -l`
- 핵심→에이전트 호출: `grep -n "vlm_confirm\." vigent-core/routers/zone.py` · `grep -n scribe vigent-core/routers/recognition.py` · `grep -n llm_provider vigent-core/routers/system.py`
- 워커 발화 규칙: `grep -n "fired.append(" vigent-core/worker.py`
