"""6단계 검증 — 데이터엔진(증거·로그 저장/집계) + 리포트(목록·다시열기·표준양식)"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vigent-core"))

import data_engine  # noqa: E402
from _isolate import isolate_data_dirs  # noqa: E402

# 1x1 흰 픽셀 JPEG (base64) — 증거 프레임 저장 테스트용
_PIX = ("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAP//////////////"
        "////////////////////////////////////////////////////////////2wBDAf//"
        "////////////////////////////////////////////////////////////////////"
        "////////wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAAAv/EABQQAQAA"
        "AAAAAAAAAAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAA"
        "AAAAAP/aAAwDAQACEQMRAD8AfwH/2Q==")


class TestDataEngine(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_data_dirs())   # [4단계 ④] 운영 data/evidence·recognition 에 쓰지 않는다

    def test_log_event_writes_record_and_frame(self):
        rec = data_engine.log_event("zone_intrusion", "high", 60, site="t", image_data_url=_PIX)
        self.assertEqual(rec["rule"], "zone_intrusion")
        self.assertIsNotNone(rec["evidence"])                  # 증거 경로 생성
        self.assertTrue((data_engine._ROOT / rec["evidence"]).exists())   # 실제 파일 저장됨(격리 루트 기준)

    def test_log_without_image_is_ok(self):
        rec = data_engine.log_event("ppe_missing", "medium", 30)
        self.assertIsNone(rec["evidence"])                     # 이미지 없이도 무중단

    def test_aggregate_counts(self):
        before = {d["rule"]: d["count"] for d in data_engine.aggregate(hours=24)}
        data_engine.log_event("guard_bypass", "critical", 100)
        after = {d["rule"]: d["count"] for d in data_engine.aggregate(hours=24)}
        self.assertEqual(after.get("guard_bypass", 0), before.get("guard_bypass", 0) + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
