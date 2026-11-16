"""rf-detr 백엔드 서비스 — 웹 화면이 호출하는 탐지·추적·위험구역 (permissive).

- 모델은 1회 로드해 재사용(서버 수명 동안).
- detect(): 프레임 → rf-detr 사람탐지 → ByteTrack 추적 → 위험구역 침입 판정.
전부 로컬·permissive(rfdetr/trackers Apache-2.0, supervision/mlx-vlm MIT).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import runtime_config
from app_state import DETECT_LOCK  # F-14: 네이티브 추론 직렬화(RLock) — MPS 다모델 동시추론 크래시 차단
from web_util import zone_points

ROOT = Path(__file__).resolve().parent.parent


def _load_zone_and_threshold(theme: str = "safety"):
    """vision.yaml + danger_zone.json 에서 위험구역(정규화 폴리곤)·임계값.
    ※ worker._load_zone(config/danger_zone.json 직접·튜플만)과 이름·계약이 달라 P2-11에서
      명확히 rename(동명이인 혼동 제거). 공통 점추출만 web_util.zone_points 로 공유."""
    import yaml
    vy = yaml.safe_load(open(ROOT / "themes" / theme / "vision.yaml", encoding="utf-8"))
    jud = vy.get("judgment", {}) or {}
    from defaults import PERSON_THRESHOLD as _pt
    thr = float(jud.get("detect_threshold", _pt))        # [CODE_AUDIT #6] 폴백은 defaults.py(= tuning person 0.40)
    zpath = (jud.get("zones", {}) or {}).get("danger_zones")
    pts = []
    if zpath:
        zp = runtime_config.read_path(zpath)   # B2: 런타임(data/) 우선 → config/ 시드 폴백
        if zp.exists():
            z = json.load(open(zp, encoding="utf-8"))
            pts = zone_points(z)
    return pts, thr


def _point_in_poly(x, y, poly) -> bool:
    """점(x,y)이 폴리곤(픽셀좌표 Nx2) 내부인지 — ray casting."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside


class RFDetrService:
    """rf-detr 탐지 + 추적 + 위험구역. 지연 로드 싱글톤."""

    def __init__(self):
        self._model = None
        self._tracker = None

    def _ensure(self):
        if self._model is not None:
            return
        import device as _device
        from rfdetr import RFDETRNano
        from trackers import ByteTrackTracker
        dev = _device.pick_device(prefer_mps=True)   # 감사 C-1: CUDA→MPS→CPU (리눅스서 GPU 사용)
        # [2026-11-16 rfdetr 1.12.1] optimize_for_inference() 삭제·안티앨리어싱 기본 꺼짐을 공용 함수로 흡수(detectors/rfdetr_compat.py).
        #   예전 `try: optimize_for_inference() except: pass` 는 새 버전에서 조용히 최적화 없이 돌았다.
        from detectors.rfdetr_compat import optimize as _optimize
        from detectors.rfdetr_compat import predict_kwargs as _predict_kwargs
        self._model, self.optimized = _optimize(RFDETRNano(device=dev))
        self._predict_kwargs = _predict_kwargs(self._model)
        # F-8(item4, 2026-07-21): SORTTracker→ByteTrackTracker. 크레인 다중작업자 A/B 실측상 SORT 는
        #   실 ~5명을 39 ID 로 단편화(단편화 3.9·ID스위치 13), ByteTrack 은 5 ID·0 스위치로 안정
        #   (tools/track_quality.py). zone_intrusion 침입자 식별 정확도 직결. 파라미터는 기본값 유지
        #   (lost_track_buffer=30·frame_rate=30·track_activation_threshold=0.7·minimum_iou_threshold=0.1
        #    ·high_conf_det_threshold=0.6·minimum_consecutive_frames=2) — 현장 튜닝은 P3 백로그.
        self._tracker = ByteTrackTracker()
        self.device = dev

    def detect(self, image_bgr: np.ndarray) -> dict[str, Any]:
        """프레임 추론. 반환: 정규화 bbox·라벨·id 목록 + 위험구역 침입."""
        with DETECT_LOCK:   # F-14: 네이티브 추론 직렬화
            self._ensure()
            import cv2
            import supervision as sv
            from PIL import Image
            from rfdetr.assets.coco_classes import COCO_CLASSES  # [M1-10] rfdetr.util.* 은 1.9.0 제거 예정

            h, w = image_bgr.shape[:2]
            pts, thr = _load_zone_and_threshold("safety")   # 매 프레임 설정 반영(화면서 구역 바꾸면 즉시)
            det = self._model.predict(
                Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)), threshold=thr,
                **getattr(self, "_predict_kwargs", {}))
            # 추적: ByteTrack 으로 프레임 간 track id 부여(침입자 식별). 감사 A: 과거엔 생성만 하고
            # 호출하지 않아 id 가 항상 -1이었음 → 실제 update 로 배선. 실패/빈 결과면 raw 탐지 유지(폴백).
            try:
                tracked = self._tracker.update(det)
                if tracked is not None and len(tracked) > 0:
                    det = tracked
            except Exception:  # noqa: BLE001  추적 실패해도 탐지는 유지(절대 저하 없음)
                pass
            tids = getattr(det, "tracker_id", None)
            names = [COCO_CLASSES[c] for c in det.class_id]   # 80종 전부 유지(사람만 거르지 않음)

            # 위험구역 침입은 '사람'에만 적용 → 좌표가 구역 안인지 판정
            zone = None
            if len(pts) >= 3:
                zone = sv.PolygonZone(polygon=(np.array(pts) * [w, h]).astype(int))
                zone.trigger(det)            # 내부 카운트 갱신(개별 마스크는 아래서 직접 계산)
            poly = (np.array(pts) * [w, h]) if len(pts) >= 3 else None

            def _in_zone(box):
                if poly is None:
                    return False
                cx, cy = (box[0] + box[2]) / 2, box[3]          # 발 위치(하단 중앙)
                return bool(_point_in_poly(cx, cy, poly))

            out, n_in, ids_in = [], 0, []
            for i in range(len(det)):
                label = names[i]
                x1, y1, x2, y2 = (float(v) for v in det.xyxy[i])
                tid = int(tids[i]) if tids is not None and tids[i] is not None else -1
                inz = (label == "person") and _in_zone((x1, y1, x2, y2))
                if inz:
                    n_in += 1
                    if tid >= 0:
                        ids_in.append(tid)
                out.append({
                    "label": label,
                    "conf": round(float(det.confidence[i]), 2),
                    "id": tid,
                    "in_zone": inz,
                    "bbox": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)],
                })
            person_count = sum(1 for d in out if d["label"] == "person")
            return {"detections": out, "person_count": person_count,
                    "intrusion": {"count": n_in, "ids": ids_in},
                    "device": getattr(self, "device", "?")}

    def detect_persons(self, image_bgr: np.ndarray, thr: float = 0.1) -> list[dict[str, Any]]:
        """저임계 person 검출(정규화 bbox·무추적) — B9 위험구역 타일 재검출 전용 가산 경로.
        ★지연 로드: 호출 시에만 _ensure()(모델 로드). 안 부르면 로드·메모리 영향 0.
        ★이종 검출기 주의: 일반 detect()/guard 검출기와 별개인 RF-DETR 저임계 경로다.
          반환 conf 는 RF-DETR 척도이므로 guard(YOLO 등) conf 와 직접 비교 불가."""
        with DETECT_LOCK:   # F-14: 네이티브 추론 직렬화
            self._ensure()
            import cv2
            from PIL import Image
            from rfdetr.assets.coco_classes import COCO_CLASSES  # [M1-10] rfdetr.util.* 은 1.9.0 제거 예정
            h, w = image_bgr.shape[:2]
            det = self._model.predict(
                Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)), threshold=thr,
                **getattr(self, "_predict_kwargs", {}))
            out = []
            for i in range(len(det)):
                if COCO_CLASSES[det.class_id[i]] != "person":
                    continue
                x1, y1, x2, y2 = (float(v) for v in det.xyxy[i])
                out.append({"label": "person", "conf": round(float(det.confidence[i]), 3),
                            "bbox": [x1 / w, y1 / h, x2 / w, y2 / h]})
            return out


# 서버 전역 싱글톤
rfdetr = RFDetrService()
# [⑤-2, 2026-10-12] VLM(vlm_service.py)은 에이전트 층과 함께 삭제됐다 — 이 모듈은 검출·추적·위험구역만 남는다.
