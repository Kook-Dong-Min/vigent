"""OPEN_ISSUES_20261008 M급 일괄 승인분(코드 전용) 고정.

  #10 VLM 대기: 호출 스레드가 DETECT_LOCK 을 쥐지 않고 기다린다(다른 스레드가 그동안 검출 락을 얻는다) · 타임아웃이면 TimeoutError ·
      호출 스레드가 이미 락을 쥔 경우(워커 재진입)는 종전 방식(데드락 없음)
  #11 RTMPose 로드 실패 → _PoseModel.error · Worker.status()["pose_error"] · health.camera_status()["pose_error"]
  #12 psutil 핀 · _pid_alive 는 ImportError 때 tasklist/ps 폴백 · stop_go2rtc 는 terminate 뒤 kill
  #13 VLM 임시 이미지 = tempfile.gettempdir() + imwrite 반환 확인 · worker 수집 imwrite 실패는 collect_failed
  #15 defaults: BYTETRACK_MIN_FRAMES=yaml · ERGO_JOINTS=vision.yaml · RETENTION_WARN_FREE_GB · SPEC_MIN=preflight.ps1 param 기본값
  #16 go2rtc_client 한 곳(코드에 127.0.0.1:1984 리터럴 0) · JS 8005 없음
  #18 매니페스트: fk510 release: · onnx 3종 등재(SHA = 개발기 파일, 있을 때만)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

from _isolate import isolate_alerts  # noqa: E402


def _code_strings(path: Path) -> list[str]:
    """docstring 이 아닌 문자열 상수(주석·docstring 의 역사 기록은 검사 대상이 아니다)."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8")); doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                doc_ids.add(id(first.value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids]


# [⑤-2] VlmWaitOutsideLock·VlmDarwinSerialization(vlm_service)은 모듈 삭제로 제거.


class PoseFailureVisible(unittest.TestCase):
    def test_error_recorded_and_exposed(self):
        import health_status as hs
        import worker
        pm = worker._PoseModel()
        with mock.patch.dict(sys.modules, {"pose.rtmpose_adapter": None}):   # import 가 ImportError 를 내게
            self.assertEqual(pm.persons(None, boxes=[[0, 0, 1, 1]]), [])
        self.assertTrue(pm._failed); self.assertIsNotNone(pm.error); self.assertIn("Error", pm.error)
        st = {"running": True, "last_frame_ts": time.time(), "last_detect_ts": time.time(), "pose_error": pm.error}
        self.assertEqual(hs.camera_status(st, hs.thresholds())["pose_error"], pm.error)
        self.assertIsNone(hs.camera_status({"running": True, "last_frame_ts": time.time(), "last_detect_ts": time.time()}, hs.thresholds())["pose_error"])


class Go2rtcOwnership(unittest.TestCase):
    def test_psutil_pinned_and_fallback(self):
        self.assertRegex((_ROOT / "requirements.txt").read_text(encoding="utf-8"), r"(?m)^psutil==")
        from routers import cameras
        with mock.patch.dict(sys.modules, {"psutil": None}):
            with mock.patch.object(cameras.subprocess, "run", return_value=mock.Mock(stdout='"go2rtc.exe","123"')):
                self.assertTrue(cameras._pid_alive(123))
            with mock.patch.object(cameras.subprocess, "run", return_value=mock.Mock(stdout="INFO: No tasks are running")):
                self.assertFalse(cameras._pid_alive(123))

    def test_stop_kills_after_terminate_timeout(self):
        import subprocess as sp

        from routers import cameras
        p = mock.Mock(); p.poll.return_value = None
        p.wait.side_effect = [sp.TimeoutExpired("go2rtc", 5), None]
        with mock.patch.object(cameras, "_G2_PROC", p), mock.patch.object(cameras, "_close_logf"), mock.patch.object(cameras, "_g2_pidfile") as pf:
            pf.return_value = mock.Mock()
            self.assertTrue(cameras.stop_go2rtc())
        p.terminate.assert_called_once(); p.kill.assert_called_once()


class TempImageAndCollect(unittest.TestCase):
    def test_worker_counts_only_successful_writes(self):
        src = (_ROOT / "vigent-core" / "worker.py").read_text(encoding="utf-8")
        self.assertIn("if cv2.imwrite(", src); self.assertIn('"collect_failed"', src)


class DefaultsMatchYaml(unittest.TestCase):
    def test_four_defaults(self):
        import defaults
        import yaml
        tun = yaml.safe_load((_ROOT / "config" / "tuning.yaml").read_text(encoding="utf-8"))
        vis = yaml.safe_load((_ROOT / "themes" / "safety" / "vision.yaml").read_text(encoding="utf-8"))
        self.assertEqual(int(tun["track"]["bytetrack_min_frames"]), defaults.BYTETRACK_MIN_FRAMES)
        from engine import guard
        self.assertEqual(guard.Guard.BYTETRACK_MIN_FRAMES, defaults.BYTETRACK_MIN_FRAMES)

        def find(d, k):
            if isinstance(d, dict):
                if k in d:
                    return d[k]
                for v in d.values():
                    r = find(v, k)
                    if r is not None:
                        return r
            return None
        joints = find(vis, "joints")
        for j, (g, w) in defaults.ERGO_JOINTS.items():
            self.assertEqual((int(joints[j]["good"]), int(joints[j]["warn"])), (g, w), j)
        import ergonomics
        self.assertIs(ergonomics.ERGO_JOINTS, defaults.ERGO_JOINTS)
        import retention
        self.assertEqual(retention.WARN_FREE_BYTES, int(defaults.RETENTION_WARN_FREE_GB * 1024 ** 3))
        ps = (_ROOT / "scripts" / "deploy" / "preflight.ps1").read_text(encoding="utf-8-sig")
        sm = defaults.SPEC_MIN
        self.assertRegex(ps, rf"\$MinVramGB = {sm['vram_gb']}\b"); self.assertRegex(ps, rf"\$MinRamGB = {sm['ram_gb']}\b")
        self.assertRegex(ps, rf"\$MinDiskGB = {sm['disk_gb']}\b"); self.assertIn(f'$CudaBuild = "{sm["cuda"]}"', ps)
        self.assertIn(f'$MinVcRedist = "{sm["vcredist_min"]}"', ps); self.assertRegex(ps, rf'"cu130" = {sm["driver_min"]}\b')
        bp = (_ROOT / "scripts" / "build_portable.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(f'[string]$Cuda = "{sm["cuda"]}"', bp)


class Go2rtcSingleSource(unittest.TestCase):
    def test_no_literal_left_and_client_builds_urls(self):
        import go2rtc_client as g
        self.assertEqual(g.url("/api/streams", src="cam 1"), "http://127.0.0.1:1984/api/streams?src=cam+1")
        self.assertEqual(g.ws_url("/api/ws", src="a"), "ws://127.0.0.1:1984/api/ws?src=a"); self.assertEqual(g.PORT, 1984)
        for rel in ("routers/cameras.py", "routers/tapo.py", "starvation_guard.py"):
            self.assertFalse([x for x in _code_strings(_ROOT / "vigent-core" / rel) if "127.0.0.1:1984" in x], rel)


class ManifestRelease(unittest.TestCase):
    def test_fk510_and_onnx_in_release(self):
        m = json.loads((_ROOT / "weights_manifest.json").read_text(encoding="utf-8"))
        by = {w["file"]: w for w in m["weights"]}
        for f in ("forklift_rfdetr_fk510_smoke.pth", "ppe_rfdetr_v1.onnx", "forklift_rfdetr_v1.onnx", "fire_smoke_rfdetr_v1_e17.onnx"):
            self.assertIn(f, by); self.assertEqual(by[f]["url"], "release:weights-v1"); self.assertEqual(len(by[f]["sha256"]), 64)
            p = _ROOT / "vigent-core" / "weights" / f
            if p.is_file():
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), by[f]["sha256"], f); self.assertEqual(p.stat().st_size, by[f]["size_bytes"], f)
        self.assertEqual(len(re.findall(r'"url": "local:', (_ROOT / "weights_manifest.json").read_text(encoding="utf-8"))), 0, "local: 항목이 남아 있다")


class IsolateAll(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts())

    def test_placeholder_isolation_active(self):
        import alert_queue
        self.assertNotEqual(alert_queue._DB_PATH.resolve(), (_ROOT / "data" / "alert_queue.db").resolve())


if __name__ == "__main__":
    unittest.main()
