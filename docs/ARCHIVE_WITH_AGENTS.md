# 보관 태그 `archive/with-agents-20261012` 에서 에이전트 층 꺼내 보기 (초보자용)

> 2026-10-12 에 이 저장소는 비전 전용이 됐고, 그날 삭제된 에이전트·LLM·VLM 층 **전체**는 git 태그 `archive/with-agents-20261012`
> (= 커밋 `075aad4`, 같은 지점의 브랜치 `archive/with-agents`)에 남아 있다. 이 문서는 그 태그에서 코드를 **읽고 · 파일만 꺼내고 · 그 시점 서버를 따로 켜 보는**
> 세 가지를 한 줄씩 따라 할 수 있게 쓴 것이다. 아래 명령은 2026-10-12 개발기에서 실제로 실행해 확인했다.
> 태그·브랜치는 원격(GitHub)에 푸시돼 있다. **태그는 절대 지우거나 옮기지 않는다**(특허 근거 보존 지점).

## 0. 준비

```powershell
cd D:\vigent_original
git fetch --tags origin                      # 태그가 로컬에 없으면 받아온다
git tag -l "archive/*"                        # archive/with-agents-20261012 가 보여야 함
```

## 1. 코드 열람 (저장소를 건드리지 않음)

```powershell
# 파일 하나를 화면에 출력
git show archive/with-agents-20261012:vigent-core/agents/scribe.py | more
# 파일을 저장소 밖으로 저장해 편집기로 보기
git show archive/with-agents-20261012:vigent-core/vlm_confirm.py > D:\vigent_field\vlm_confirm_archive.py
# 그 시점의 전체 파일 목록
git ls-tree -r --name-only archive/with-agents-20261012 vigent-core | more
```
GitHub 에서는 저장소 페이지 → "main ▾" 드롭다운 → **Tags** → `archive/with-agents-20261012` 를 고르면 그 시점 트리를 그대로 둘러볼 수 있다.

## 2. 특정 파일만 가져오기 (작업 트리를 더럽히지 않는 방법)

```powershell
# 원하는 파일들을 압축 스트림으로 뽑아 저장소 밖 폴더에 풀기(현재 작업 트리는 그대로)
New-Item -ItemType Directory -Force D:\vigent_field\tag_extract | Out-Null
git archive archive/with-agents-20261012 vigent-core/agents/scribe.py vigent-core/legal_whitelist.py | tar -x -C D:\vigent_field\tag_extract
# 폴더째 꺼내기
git archive archive/with-agents-20261012 vigent-core/agents config/corpus | tar -x -C D:\vigent_field\tag_extract
```
`git checkout <태그> -- <파일>` 도 되지만 그 파일이 **지금 작업 트리에 복원·스테이징**되므로, 실수로 커밋하지 않으려면 위 `git archive` 방식을 쓴다.
(꼭 checkout 을 썼다면 `git restore --staged <파일>; git rm -q <파일>` 로 되돌린다.)

## 3. 그 시점 서버를 따로 켜 보기 (임시 worktree)

```powershell
# ① 별도 폴더에 태그 시점을 체크아웃(현재 저장소·브랜치는 그대로)
git worktree add D:\vigent_with_agents archive/with-agents-20261012
# ② 추적되지 않는 자산(가중치·.env·data)은 체크아웃되지 않는다 — 복사 또는 심링크(CLAUDE.md 규칙 8)
#    가중치: scripts\fetch_weights.py --all  (또는 개발기 vigent-core\weights 복사) · .env 는 복사하지 말고 비워 둔다(토큰 노출 방지)
# ③ 포트를 바꿔 기동(개발기 8010 과 충돌 금지), 에이전트 의존성은 그 시점 requirements-agents.txt
cd D:\vigent_with_agents\vigent-core
$env:VIGENT_HOST="127.0.0.1"; D:\vigent_original\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8011
# ④ 끝나면 반드시 제거(남겨 두면 두 작업 트리가 HEAD 를 공유하지 않지만 혼동의 원인이 된다)
cd D:\vigent_original
git worktree remove --force D:\vigent_with_agents
git worktree list                             # 한 줄(현재 저장소)만 남아야 함
```
주의: 임시 worktree 에서 `git commit`·`git checkout` 을 하지 않는다. 그 시점 코드는 go2rtc 로그 마스킹·러너 감지 수정 등 **그 뒤의 결함 수정이 없는 상태**다. 경보 큐 DB 는 `VIGENT_ALERT_DB` 로 임시 파일을 지정해 운영 DB 를 건드리지 않게 한다.

## 4. 특허 후보 ↔ 구현 파일 (파일 대응만 — 발명 내용은 저장소에 적지 않는다)

| 후보 | 주제(제목만) | 구현 파일 | 어디에 있나 |
|---|---|---|---|
| 1·2 | 2단계 크레인 경보 / 미세 권상 절차 판정 | `vigent-core/experimental/rig/rig_monitor.py`(상태기계) · `experimental/rig/rig_replay.py`(CSV 재생 검증) · `tests/test_rig_monitor.py`·`test_rig_replay.py` | **main 에 있음**(미배선 실험 자산, 2026-10-12 정리 3 에서 `experimental/rig/` 로 이동 완료) |
| 3 | 작업 상태 연동 음성 코칭 | `vigent-core/liveguide.py` · `vigent-core/voice.py` · 라우트 `/safety/guide`·`/safety/voice*`(당시 `routers/safety_core.py`) | **보관 태그에만** |
| 4 | CNN→VLM 2차 확정 | `vigent-core/vlm_confirm.py` · 호출부 `routers/zone.py`(opt-in 분기)·`routers/safety_core.py`(`/safety/confirm`, 자동처리 승인) | **보관 태그에만** |
| 5 | 운전자 자동 제외 협착 경보 | `vigent-core/proximity.py`(`driver_containment`, G5) · `vigent-core/worker.py`(proximity_hazard 발화) · `tests/test_proximity_driver.py` | **main 에 있음** |
| 6 | 적응형 백오프 경보 게이트 + 선기록 후전송 | `vigent-core/alert_gate.py`(백오프·시간당 상한) · `vigent-core/alert_queue.py`(선기록 후전송 큐) · `vigent-core/engine/dispatcher.py`(enqueue→전송→mark_sent; 2026-10-12 정리 5 에서 `agents/`→`engine/`) · `vigent-core/alert_notify.py` | **main 에 있음** |
| 7 | 법령 인용 화이트리스트 게이트 | `vigent-core/legal_whitelist.py` · 호출부 `vigent-core/agents/copilot.py`·`agents/scribe.py` · `config/corpus/safety_citations.json` · `scripts/legal_gate_verify.py` | **보관 태그에만** |

보관 태그에만 있는 파일을 볼 때는 §1·§2 의 명령에 위 경로를 그대로 넣으면 된다.

## 5. 태그에만 있는 에이전트 층 파일 목록 (2026-10-12 삭제분)

| 묶음 | 파일 |
|---|---|
| 에이전트 | `vigent-core/agents/analyst.py` · `copilot.py` · `safety_manager.py` · `scribe.py` |
| LLM·VLM | `vigent-core/llm_provider.py` · `vlm_confirm.py` · `scene_vlm.py` · `ml/vlm_risk_summary.py` · (②-b 분리본 `vlm_service.py` 는 태그 뒤 생성·삭제) |
| 지식·서류 | `vigent-core/safety_brain.py` · `safety_rag.py` · `hazard_rules.py` · `legal_whitelist.py` · `tbm_store.py` · `audit_store.py` |
| 기능 | `vigent-core/incident.py` · `ppe_check.py` · `liveguide.py` · `behavior.py` · `voice.py` · `hub.py` · `demo.py` · `quote.py` · `evaluator.py` |
| 라우터 | `vigent-core/routers/incident.py` · `ppe.py` · `tbm.py` · 당시 `routers/safety_core.py` 안의 에이전트 라우트 49개 작업(허브·위험성평가·자동처리·브레인·음성·안내·평가·데모·견적) |
| 화면·정적 | `themes/safety/index.html` · `index_local.html` · `vigent-core/templates/{auto.html,tbm_new.html,tbm.css}` · `static/{auto_terminal.html,realtime_core.js,posture-model.js,ergonomics.js,realtime_shared.css,vigent-skin.css,vigent-terminal.css,vigent-terminal-skin.css,vt-rail-collapse.js}` · `static/models/posture/` |
| 설정·패키지 | `config/corpus/*.json`(5) · `config/templates/*`(KOSHA 양식 2) · `config/ppe_rules.yaml` · `requirements-agents.txt` · `themes/safety/vision.yaml` 의 `vlm:`·`report:`·`evidence:` 섹션 · `.env.example` 의 LLM 키 |
| 스크립트·테스트 | `scripts/golden_*.py`(4) · `scripts/legal_*.py`(2) · `bin/download-vendor.sh` · 테스트 11파일 |

삭제 커밋: ⑤-1 `c2456fe` · ⑤-2 `eccc5f2` · ⑤-4 `66e99bb` · ⑤-5 `c67ca67` · ⑤-3 `1f0acc7` (각 커밋 메시지에 파일 목록이 있다).
