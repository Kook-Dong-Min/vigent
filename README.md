# VIGENT

> ★**이 저장소는 비전 전용이다(2026-10-12).** 검출·추적·위험 판정·경보 전송·증거 저장·카메라 관리·관제 화면만 남아 있다. 에이전트·LLM·VLM 층(위험성평가서·TBM·안전 브레인·음성·자동처리 콘솔 등)은 별도 에이전트 저장소로 옮기기 위해 삭제됐고, 삭제 전 전체는 태그 `archive/with-agents-20261012` 에 있다(꺼내 보는 법: [docs/ARCHIVE_WITH_AGENTS.md](docs/ARCHIVE_WITH_AGENTS.md), 계획: [md/MERGE_PLAN_20261012.md](md/MERGE_PLAN_20261012.md)).

**비전 전용 산업안전 CCTV 코어(2026-10-12 ~).** 스마트 카메라 영상에서 위험(위험구역 침입·보호구 미착용·화재/연기·협착 등)을 실시간 탐지해 경보를 보내고 증거를 남긴다(위험성평가서 등 문서 생성은 별도 에이전트 저장소). 테마는 **safety** 단일.

> ⚠️ **안전 경계**: 비전 ML은 확률적이라 인증 안전기능(안전 PLC·Type 4 방호장치)을 **대체하지 않는다.** VIGENT는 **보조·감시 계층**이며 모든 산출물은 "안전관리자 검토용 초안"이다.

---

## 빠른 시작 (로컬)

> 2026-09-06 감사 C4: 개발 PC가 Windows 로 옮겨져 **Windows 절차를 앞에** 둔다. mac/Linux 는 `run.sh`.
> mac 전용 더블클릭 런처(`.command`)·launchd plist 는 `_archive/macos/` 로 격리했다.

### 1. 파이썬 — 3.11 고정
정본은 **Python 3.11.x**(`.python-version` = 3.11.9 · `pyproject.toml` py311). 개발 PC·학원 현장 노트북 모두 3.11.9 실증.
```powershell
py -3.11 --version        # Windows (py 런처)
python3 --version         # mac/Linux
```
> ⚠ CI(`.github/workflows/ci.yml`)는 아직 **3.13** 으로 돌아 개발 환경과 다르다 — 감사 4단계 정합 대상(AUDIT_REPORT §2-2).

### 2. 의존성 설치
```powershell
py -3.11 scripts\setup_env.py --weights              # 감시 서버(필수): requirements(+constraints) 설치 → opencv GUI 제거·cv2 4.13 headless 검증 → 가중치·go2rtc 조달
# 선택 — 기능별 분리([C6])
py -3.11 -m pip install -r requirements-train.txt     # 학습·측정 (ultralytics 포함 — 배포 아님)
```
> `torch`/`torchvision`은 플랫폼마다 설치법이 다르다. **이 데스크탑(RTX 5070 Ti)은 반드시 cu130 휠**(`requirements.txt` 주석 참조).
> (★2026-10-12 ⑤) 에이전트·LLM·VLM 층은 이 저장소에서 제거됐다(별도 에이전트 저장소). 보관 태그 `archive/with-agents-20261012`, 계획 `md/MERGE_PLAN_20261012.md`.

### 3. 서버 실행
```powershell
.
un.ps1            # Windows: http://127.0.0.1:8010 (무토큰). 더블클릭은 run.bat 또는 "VIGENT Safety 시작.bat"
./run.sh             # mac/Linux
# 외부 노출은 토큰 필수:
#   $env:VIGENT_HOST="0.0.0.0"; $env:VIGENT_API_TOKEN="<비밀>"; .
un.ps1
```
> Windows 런처(`run.ps1`·`run.bat`·`VIGENT Safety 시작.bat`)는 **3.11 정본만** 쓴다(`.venv` 가 3.11 이면 그것 → `py -3.11`). 없으면 안내 후 종료 — bare `python`/`py` 는 쓰지 않는다(개발 PC 의 `py` 기본이 3.14 인 사례, [CODE_REVIEW M7-5·6]). `run.sh` 는 `python3`/`python` 자동 탐색.
> 재부팅 자동기동(서비스 등록)은 [deploy/windows/README.md](deploy/windows/README.md).

기동 확인:
```powershell
Invoke-RestMethod http://127.0.0.1:8010/health | ConvertTo-Json -Depth 3
# status: "ok", rfdetr_slots 3개 LOADED — 기동 후 ~15초는 phase=starting(HTTP 503)이 정상(B4)
```
주요 화면: `/safety-hub`(관제 허브, `/` 가 여기로 넘어감) · `/safety-pro`(대시보드) · `/health`.

### 4. 테스트
```powershell
py -3.11 -m unittest discover -s tests    # 1,015 tests OK 가 정상(2026-10-12 실측; 자산 없는 CI 는 1건 skip)
```

---

## 저장소 구조 (2026-10-12 정리 후)

```
vigent-core/            감시 서버(FastAPI) — 서버가 import 하는 것은 이 폴더뿐
  main.py · app_state.py · worker.py · alert_*.py · privacy.py · retention*.py …  (루트 42파일)
  engine/               Guard(검출·추적) · Dispatcher(통보) · BaseEngine   ← 2026-10-12 agents/ 에서 이름 변경
  routers/              HTTP 라우터 8개(tapo·zone·system·detect·recognition·dispatch·cameras·safety_core)
  detectors/ · pose/    RF-DETR 어댑터 · RTMPose
  experimental/rig/     줄걸이 상태기계 — 미배선 실험 자산(특허 후보 1·2, 삭제 금지)
themes/safety/          vision.yaml(슬롯·규칙 설정) + 화면 HTML
config/ · deploy/       tuning.yaml·notify.example.yaml · 현장 프로파일(academy)·USB 포터블·Windows 서비스
scripts/                운영·측정·학습 도구(train/finetune_rfdetr.py 가 현역 학습) — 서버 코드 아님
training/               데이터셋 구성·과거 학습기 · legacy/(삭제된 테마·YOLO 시절 유물 4개)
tests/                  unittest 1,015건 · benchmarks/ 측정 기록 · docs/ 문서(ONBOARDING·onboarding/06_코드_지도)
```
에이전트·LLM 층(삭제분)은 태그 `archive/with-agents-20261012` — [docs/ARCHIVE_WITH_AGENTS.md](docs/ARCHIVE_WITH_AGENTS.md).

---

## 더 알아보기

| 문서 | 내용 |
|---|---|
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | 신규 개발자용 — 아키텍처·핵심 흐름·엔드포인트 지도 |
| [DEPLOYMENT.md](md/DEPLOYMENT.md) | 표준 기동·확인(/health 3슬롯)·재기동·좀비 방지·환경변수 |
| [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md) | 코드 검토 스냅샷(이슈·우선순위) |
| [benchmarks/FINDINGS.md](benchmarks/FINDINGS.md) | 측정·이관 과정의 리스크와 후속 태스크(F-1~) |
| [CLAUDE.md](CLAUDE.md) | AI 도구(에이전트) 작업 규칙 |

---

## 환경변수 (요약 — 상세는 DEPLOYMENT.md)

| 변수 | 기본 | 의미 |
|---|---|---|
| `VIGENT_HOST` / `VIGENT_PORT` | `127.0.0.1` / `8010` | 바인딩 |
| `VIGENT_API_TOKEN` | (없음) | 설정 시 전 라우트 Bearer 인증. **외부 바인딩엔 필수**(미설정 시 기동 거부). 공유 네트워크·파일럿에선 상시 설정 |
| `VIGENT_LLM_PROVIDER` | `openai` | 텍스트 LLM(`openai`/`anthropic`). 키 없으면 규칙 기반 폴백(기능 유지) |
| `VIGENT_CLOUD_VLM` | off | **1**일 때만 현장 프레임을 클라우드 VLM에 전송(기본 차단, 영상 불유출 원칙) |

---

## 설계 원칙 (핵심 2가지)
1. **절대 저하 없음 = 가산식 + 폴백.** 딥러닝 신호는 규칙 점수에 가산만. 모델/키/네트워크가 없거나 실패하면 휴리스틱·규칙으로 자동 폴백한다(코드의 `except … → 폴백`은 의도된 패턴).
2. **테마 = 코드가 아니라 설정.** 신규 테마는 `themes/<name>/vision.yaml` + 프론트 1개로 추가하고 코어 로직은 건드리지 않는다.
