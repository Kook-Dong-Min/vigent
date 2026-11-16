# experimental/rig — 줄걸이(크레인 인양) 작업 절차 상태기계 (RIG v2)

> ★ **특허 후보 1·2 의 구현이다. 삭제 금지.** (2단계 크레인 경보 / 미세 권상 절차 판정 — 발명 내용은 저장소에 적지 않는다, [docs/ARCHIVE_WITH_AGENTS.md](../../../docs/ARCHIVE_WITH_AGENTS.md) §4)
> ★ **실험 단계 비전 자산 — 파이프라인 미배선.** 어떤 라이브 경로(worker·routers)도 이 패키지를 import 하지 않는다(2026-10-12 실측: 호출부 0).
>   검증은 유닛테스트로만 한다: `tests/test_rig_monitor.py`(5) · `tests/test_rig_replay.py`(6). 실제 워커 연결은 별도 승인 항목(OPEN_ISSUES #29).

| 파일 | 역할 |
|---|---|
| `rig_monitor.py` | 상태기계(`RigStateMachine`·`RigConfig`) — 인양 단계·인원 진입·낙하 감지 → 경보 판정. 임계는 설정으로 외부화 |
| `rig_replay.py` | 사람이 영상을 보고 주석한 CSV(obs)를 프레임별로 보간해 상태기계에 넣고 전이 타임라인을 만든다(실영상 대조 검증 도구) |

실행(재생 도구): `cd vigent-core; python -m experimental.rig.rig_replay <obs.csv>` (패키지 상대 import 라 모듈로 실행한다).
이동 이력: 2026-10-12 정리 3 — `vigent-core/rig_monitor.py`·`rig_replay.py` 에서 이 폴더로(`git mv`, 내용 불변).
