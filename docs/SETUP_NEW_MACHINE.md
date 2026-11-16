# 새 컴퓨터 세팅 절차 (Windows) — 2026-10-08, 실제로 따라 해서 통과한 것만 적음

> 검증 환경: 이 절차는 **개발기(Windows 11, RTX 5070 Ti, Python 3.11.9)에서 GitHub 를 새로 clone 한 별도 폴더 `D:\vigent_fresh` + 새 `.venv`
> + 빈 사용자 프로필(`USERPROFILE=D:\vigent_fresh_home`)** 로 재현해 통과시킨 것이다. 다른 OS·GPU(노트북 GTX 1650 Ti, RTX 5060)에서는
> **아직 실행하지 않았다** — 같은 순서로 하되, 통과하면 이 문서 맨 아래 표에 날짜·기계를 적는다.
> 모든 명령은 저장소 루트에서. `<venv>` = `.\.venv\Scripts\python.exe`.

## 0-a. 더블클릭 자동 준비 (2026-11-16 추가) — 아래 1~3단계를 런처가 대신 한다

`VIGENT Safety 시작.bat` 더블클릭 → `scripts\ensure_runtime.ps1`(파이썬·`.venv`) → `scripts\ensure_runtime.py`(패키지·VC++ 런타임·GPU용 torch·모델 파일) → 서버 기동.
**없는 것만** 받는다. 받지 않고 점검만: `.\.venv\Scripts\python.exe scripts\ensure_runtime.py --check`.

| 없는 것 | 어떻게 채우나 | 2026-11-16 실제로 돌려 본 것(GTX 1650 Ti 노트북, Windows 10) |
|---|---|---|
| 파이썬 3.14 | `.venv` → `py -3.14` → 없으면 nuget.org 공식 배포본(`.python-version` 의 3.14.5)을 이 폴더 `.python\` 에 풀고 python.exe 서명(Python Software Foundation) 확인. PC 에 설치하지 않는다 | **확인** — 임시 사본에서 `py` 를 없는 것으로 치고 실행: 받기→풀기→서명 Valid→`.venv` 생성→3.14.5 실행, 15초 |
| 패키지 | `setup_env.py --no-preflight` 뒤 새 프로세스에서 다시 셈 + 불러오기 시험 | **일부만** — 새 `.venv` 에서 `pip install --dry-run -r requirements.txt` 가 충돌 없이 풀림(75개). ★**실제 전체 설치는 돌리지 않았다** |
| VC++ 런타임 | 설치된 `.pyd/.dll` 의 MSVC 링커 버전 최댓값(이 노트북 실측 14.51)보다 낮거나 없으면 `aka.ms/vc14/vc_redist.x64.exe` 를 받아 Microsoft 서명 확인 후 설치(관리자 권한 창) | **일부만** — 받기·서명 확인·파일 버전(14.52.37029)까지. ★설치 실행은 안 했다(이 PC 는 이미 14.51) |
| GPU용 torch | NVIDIA + 드라이버 ≥ 580 이면 cu130 휠로 교체 → 실제 GPU 연산으로 확인 → 안 되면 CPU용으로 되돌림. **이 준비기가 새로 설치한 환경에서만** | **일부만** — 판정(GTX 1650 Ti·616.92 → 교체 대상)과 GPU 연산 확인 스크립트는 실제 실행. ★교체(내려받기·설치) 자체는 안 돌렸다 |
| 모델·보조 파일 | 매니페스트의 필수 6 + go2rtc·ffmpeg·얼굴 가림 모델 중 **없는 파일만** 받아 SHA256 대조. 크기가 다른 파일은 덮어쓰지 않고 경고만 | **확인** — 임시 사본에서 3개(비공개 릴리스·openmmlab zip·공개 GitHub zip)를 빼고 실행 → 받음 → `fetch_weights.py --check` 로 독립 재검증 일치 |
| (다 있을 때) | 아무것도 받지 않고 상태 파일도 만들지 않는다 | **확인** — 1.5초, 변경 파일 0. 런처 전체(준비→기동→`/health` 200→브라우저)도 탐색기 실행 방식으로 확인(첫 200 까지 29초) |

- ★**새 PC 에서 처음부터 끝까지(전체 설치 → 서버 기동)는 아직 한 번도 돌리지 않았다.** 통과하면 §8 표에 적는다.
- ★`requirements.txt` 는 직접 의존 19개만 고정한다. 새 환경에는 **간접 의존이 그날의 최신 버전**으로 깔린다 — 2026-11-16 dry-run 기준 이 노트북 `.venv` 와 23개가 달랐다(예: transformers 5.19.0→5.23.0, starlette 1.7.0→1.10.0). 그 조합으로 서버가 도는지는 미검증.
- 모델 파일 중 `release:` 항목은 비공개 GitHub 릴리스라 그 PC 에 GitHub 로그인(git 자격증명 또는 `GITHUB_TOKEN`)이 있어야 받는다. 없으면 필수 파일 3개(ppe·forklift·fire_smoke)를 못 받아 런처가 멈추고 방법을 안내한다.
- 깨진 `.venv`(다른 버전·다른 PC 에서 복사)는 자동으로 지우지 않는다 — 안내만 한다.

## 0. 전제

| 항목 | 값 | 확인 명령 |
|---|---|---|
| Python | **3.11.x**(`.python-version` 3.11.9). 3.12 이상은 `setup_env.py` 가 거부한다 | `py -3.11 --version` |
| GPU 드라이버 | CUDA 13.0 휠(cu130) → NVIDIA 드라이버 **≥ 580** | `nvidia-smi` 첫 줄 |
| 인터넷 | 1~2단계(pip·가중치 조달)에만 필요. 3단계부터는 오프라인 가능 | — |
| 디스크 | 저장소 81 MB + .venv ≈ 6 GB(cu130 torch 포함) + 가중치 ≈ 1.1 GB | — |

## 1. clone → .venv → 의존성

```powershell
git clone https://github.com/<owner>/vigent.git D:\vigent      # 기본 브랜치 main (2026-10-08 audit/cleanup-20260906 → main 병합)
cd D:\vigent
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe scripts\setup_env.py          # requirements.txt + constraints + requirements-dev(ruff·mypy) + opencv headless 단일화 + 핀 가드 + preflight
```
- 실측: `setup_env.py` 가 끝나면 **preflight 가 "없는 것과 가져올 곳"을 출력**한다(필수 누락이면 종료코드 1). 이 시점엔 가중치 6종이 없다고 나오는 것이 정상 → 2단계.
- ★이 단계에서 torch 는 **PyPI CPU 휠**이다. GPU 는 아래 1-b.

### 1-b. GPU — CUDA 휠로 교체 (RTX 50 시리즈는 cu130 필수)
```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cu130 --force-reinstall --no-deps torch==2.12.0+cu130 torchvision==0.27.0+cu130
.\.venv\Scripts\python.exe -c "import torch;print(torch.__version__, torch.cuda.is_available())"   # 2.12.0+cu130 True
```
- 오프라인이면 USB 스테이지의 `portable\python\wheels_cuda\` 두 휠을 `--no-index --find-links <그 폴더>` 로(실측에 쓴 방법).
- 교체 뒤 `.\.venv\Scripts\python.exe scripts\setup_env.py --no-install` 로 numpy·cv2 핀이 안 바뀌었는지 확인(실측 OK).

### 1-c. 학습까지 할 기계만
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-train.txt -c constraints.txt
.\.venv\Scripts\python.exe scripts\setup_env.py --no-install        # 핀 가드 재확인(실측: numpy 2.4.6 · cv2 4.13.0.92 유지)
.\.venv\Scripts\python.exe scripts\check_pip_deps.py                # 허용 7건(cv2 이름 불일치·headless 충족)만 → exit 0
```

## 2. 가중치 — 저장소가 스스로 받는다 (수동 복사는 2개만)

```powershell
.\.venv\Scripts\python.exe scripts\fetch_weights.py --all     # GitHub Release + 원출처(GCS·openmmlab·go2rtc) → SHA256 대조
.\.venv\Scripts\python.exe scripts\fetch_weights.py --check --all
```
실측(새 clone): 필수 6(rf-detr-nano·ppe_rfdetr_v1·forklift_rfdetr_v1·fire_smoke_rfdetr_v1_e17·rtmlib onnx 2) + 선택 7 + `bin\go2rtc.exe` **전부 조달·검증 OK**. 비공개 저장소면 GitHub 토큰(`gh auth`)이 있어야 Release 자산을 받는다.

| 수동 복사 대상 | 왜 | 어디서 → 어디로 | 확인 |
|---|---|---|---|
| `forklift_rfdetr_fk510_smoke.pth`(학원 프로파일 지게차) | 매니페스트 `url: local:`(Release 미업로드) | 개발기 또는 USB 스테이지 `portable\app\vigent-core\weights\` → `vigent-core\weights\` | `fetch_weights.py --check --all` 에서 `[OK]`(SHA `b409c98d…`) |
| `ppe_rfdetr_v1.onnx` · `forklift_rfdetr_v1.onnx` · `fire_smoke_rfdetr_v1_e17.onnx` | 매니페스트 미등재(onnx-cpu 백엔드·CPU 포터블·`test_rfdetr_onnx_parity` 에만 필요) | 개발기 `vigent-core\weights\` | `setup_env.py --preflight` 에서 OK. torch 백엔드만 쓰면 생략 가능 |

- RF-DETR 사전학습 캐시: 2026-10-08 부터 `Guard`(`vigent-core/engine/guard.py`, 옛 이름 GuardAgent) 가 **저장소 `vigent-core\weights\rf-detr-nano.pth` 를 먼저 보고 `RF_HOME` 을 거기로 세운다**. 따로 `~/.roboflow` 를 채울 필요가 없다(예전엔 프로필 캐시가 지워지면 게이트 ERROR 39).

## 3. 비밀·설정 (저장소에 없는 것 — 값은 어디에도 적지 않는다)

| 파일 | 필수? | 만드는 법 |
|---|---|---|
| `config\notify.yaml` | 선택 | `copy config\notify.example.yaml config\notify.yaml`. **개발·시험 기계는 실채널을 넣지 않는다**(텔레그램·SMTP 비움, 필요하면 `webhook_url: http://127.0.0.1:9912/sink` + `scripts\bench\local_sink.py --port 9912`). 현장 값은 설치 마법사가 넣는다 |
| `.env` | 127.0.0.1 바인드면 선택, 외부 바인드·`VIGENT_REQUIRE_TOKEN=1` 이면 필수 | `python -c "import secrets;print('VIGENT_API_TOKEN='+secrets.token_hex(32))" > .env` |
| `data\camera_secrets.json` | 선택 | 카메라 등록(설정 콘솔·`setup_wizard.py`) 때 자동 생성. 개발기 것을 복사하지 않는다 |
| `data\datasets\css_safety\` | 평가·학습 기계만 | 개발기 `data\datasets` 복사(CC BY 4.0). 없으면 `test_ppe_compare_harness` 1건 skip |
| `VIGENT_DATA_DIR` | 학습·현장 평가 기계만 | 저장소 밖 자료 루트(기본: 저장소 옆 `vigent_private_data`) |

확인: `.\.venv\Scripts\python.exe scripts\setup_env.py --preflight` → "필수 항목 전부 있음".

## 4. 게이트
```powershell
powershell -File scripts\gate.ps1      # .venv 의 ruff 0.12.0 → mypy 1.17.1 → unittest → OpenAPI 110
```
실측(새 clone, 빈 프로필, 2026-10-08 22:00): ruff OK · mypy 19파일 0 · **921 tests OK(skipped=3)** · OpenAPI 110/110 · 261 s.
skip 3 = `test_ppe_compare_harness`(CSS 데이터셋 없음) · `test_audit_usb_fk510`(fk510 없음) · `test_rfdetr_onnx_parity`(.onnx 없음) — 전부 3단계 선택 자산.

## 5. 서버 기동 확인
```powershell
cd vigent-core
$env:VIGENT_HOST="127.0.0.1"; ..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010
# 다른 창: curl http://127.0.0.1:8010/health  → phase=ready, status=healthy, gpu.torch_cuda=true
```
실측(새 clone): 예열 15.0 s(person 10.1 · ppe 2.5 · fire_smoke 2.4) 뒤 `phase=ready · status=healthy · warnings=[]`, GPU `RTX 5070 Ti · torch_cuda=true · fallback=false`, notify `selftest_state=not_configured`(의도: 실채널 없음), 싱크 수신 0건.

## 6. USB 설치기 dry-run (관리자 불필요)
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy\install.ps1 -UsbRoot D:\vigent_usb_stage -Target D:\VIGENT_DRYRUN -Port 8032 -Bind 127.0.0.1 -DryRun -SkipPreflight
.\.venv\Scripts\python.exe scripts\deploy\usb_layout.py verify D:\vigent_usb_stage
```
실측: 8단계 전부 `[DRY]` 출력·exit 0·2 s, 대상 폴더 생성 안 됨, 레이아웃 검증 통과. (USB 를 **새로 빌드**하려면 `scripts\deploy\build_usb.ps1` — 인터넷·python.org 다운로드가 필요하고 dry-run 옵션이 없다. 이번 점검에서는 돌리지 않았다.)

## 7. 새 환경에서 걸렸던 것 (2026-10-08 실측 → 전부 수정, 커밋 `b0065e7` 이후)

| 증상 | 분류 | 수정 |
|---|---|---|
| `setup_env.py` 가 첫 print 에서 `UnicodeEncodeError`(cp949) → pip 설치 시작도 못 함 | 코드 결함 | 진입 스크립트 14개 stdout UTF-8 재설정 |
| 게이트 unittest ERROR 39 — `~/.roboflow/models/rf-detr-nano.pth` 없음 | 테스트·서버가 기계 상태(프로필 캐시) 의존 | guard 가 저장소 weights 를 먼저 보고 `RF_HOME` 을 세움 |
| rfdetr 라이브러리가 빈 프로필에 366 MB 를 인터넷에서 또 받음 | 환경 의존(조용한 다운로드) | 위와 같음(`RF_HOME` 설정) |
| `check_pip_deps` FAIL — `ultralytics → opencv-python` | 코드 결함(학습 venv 에서만 드러남) | 허용 목록 추가 |
| `fetch_weights.py --all` 이 fk510 에서 `unknown url type: local` | 코드 결함(메시지) | "어디서 복사·어떻게 확인" 안내 |
| gate.ps1 이 PATH 의 전역 ruff(0.16.1) 사용, **mypy 단계 없음**, .venv 에 둘 다 없음 | 기계 상태 의존 + 게이트 누락 | `.venv` 의 ruff·mypy(`requirements-dev.txt` = CI 핀) · mypy 단계 추가 |
| 새 clone 에 없는 것을 한 번에 알 길이 없음 | — | `setup_env.py --preflight` |

## 8. 통과 기록

| 날짜 | 기계 | 결과 |
|---|---|---|
| 2026-10-08 | 개발기 내 새 clone `D:\vigent_fresh` + 새 .venv + 빈 프로필(RTX 5070 Ti) | 1~6 전부 통과 |
