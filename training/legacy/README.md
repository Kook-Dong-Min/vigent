# training/legacy — 삭제된 테마·구 백엔드의 학습·측정 유물 (참고용, 실행 보장 없음)

> 2026-10-12 정리 4: `vigent-core/ml/` 에서 옮겼다(`git mv`, 내용 불변). 서버 패키지 안에 학습 도구가 남지 않게 하려는 것.
> 아래 네 파일은 **office/sports 테마(2026-08-10 삭제)·YOLO 백엔드(배포 제거)** 시절 것이라 현재 파이프라인과 무관하다. 지우지 않고 참고용으로 둔다.

| 파일 | 무엇 | 언제 썼나 |
|---|---|---|
| `calibrate_angles.py` | 요가 자세(sports 테마) 정답 각도 보정 → `yoga_asanas.json` | sports 테마 시절(삭제됨) |
| `eval_ergonomics.py` | office 인체공학 각도 채점 하네스 — 서버 `vigent-core/ergonomics.py` 공식과 동형 검증 | office 테마 시절. 각도 공식은 서버 모듈이 정본 |
| `merge_retrain_data.py` | Roboflow 두 데이터셋(office+person) 병합 → YOLO 재학습용 | YOLO11 재학습(사무실 모니터 혼동) |
| `train_retrain.py` | 병합 데이터셋으로 YOLO11 파인튜닝(맥 MPS) | 같은 시절. ultralytics(AGPL)는 배포에서 제거됨 |

현역 학습 경로는 `scripts/train/finetune_rfdetr.py` 다(상위 `training/README.md` 의 지도 참조).
