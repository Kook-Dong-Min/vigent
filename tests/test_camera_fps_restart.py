"""tests/test_camera_fps_restart.py — 카메라 fps 변경 시 워커 자동 재시작 (2026-10-12).

증상: 관제 허브에서 돌고 있는 카메라의 감지 fps 를 바꿔 저장해도 실제 fps 는 그대로였다.
`POST /cameras` 가 upsert 로 fps 를 저장한 뒤 `_start(cid)` 를 불렀지만, 워커가 이미 돌고 있으면
WorkerManager.start 가 "이미 실행 중" 을 조용히 돌려줘 루프 간격도 풀세트 주기도 바뀌지 않았다.
화면은 fps_live(워커 실제값) 를 우선 표시하므로 "항상 2" 로 보였다.

고침: fps 가 실제로 바뀌었고 워커가 돌고 있으면 stop → _start 로 재시작한다(구역 저장과 같은 패턴).
  ① fps 변경 + 실행 중 → 재시작(restarted=True), 새 fps 로 start 호출
  ② fps 동일 → 재시작 안 함(불필요한 스트림 끊김 방지)
  ③ fps 변경 + 꺼진 카메라(enabled=False) → 재시작 안 함
  ④ stop 미완료(ok=False) → 이중 워커 금지: _start 를 부르지 않고 restart_error 로 알린다
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vigent-core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import camera_registry  # noqa: E402
import main  # noqa: E402
import worker  # noqa: E402
from _isolate import isolate_no_token  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from routers import cameras as cam_router  # noqa: E402


class _FakeWorker:
    def __init__(self, running=True, stop_ok=True):
        self.state = {"running": running}
        self.stop_ok = stop_ok
        self.stopped = False

    def is_alive(self):
        return self.state["running"]

    def stop(self):
        self.stopped = True
        if not self.stop_ok:
            return {"ok": False, "error": "stop 미완료: 캡처 블로킹"}
        self.state["running"] = False
        return {"ok": True}


class TestCameraFpsRestart(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_no_token())
        self.client = TestClient(main.app)
        self._tmp = tempfile.TemporaryDirectory(prefix="vigent_test_fps_")
        self._saved = (camera_registry._PUB, camera_registry._SEC)
        camera_registry._PUB = Path(self._tmp.name) / "cameras.json"
        camera_registry._SEC = Path(self._tmp.name) / "camera_secrets.json"
        self._saved_workers = dict(worker.manager._workers)
        worker.manager._workers.clear()
        # _start 는 실제 Guard·go2rtc 를 건드린다 → 호출 기록만 남기는 가짜로 바꾼다
        self.start_calls: list = []
        self._p_start = mock.patch.object(cam_router, "_start",
                                          side_effect=lambda cid: self.start_calls.append(cid) or {"ok": True})
        self._p_start.start()

    def tearDown(self):
        self._p_start.stop()
        worker.manager._workers.clear()
        worker.manager._workers.update(self._saved_workers)
        camera_registry._PUB, camera_registry._SEC = self._saved
        self._tmp.cleanup()

    def _register(self, fps=2.0, enabled=True):
        r = self.client.post("/cameras", json={"id": "c1", "source": "0", "fps": fps, "enabled": enabled})
        self.assertEqual(r.status_code, 200, r.text)
        self.start_calls.clear()
        return r.json()

    def test_fps_change_restarts_running_worker(self):
        """① fps 2→5, 워커 실행 중 → stop 뒤 _start 가 다시 불려야 한다."""
        self._register(2.0)
        fw = _FakeWorker(running=True)
        worker.manager._workers["c1"] = fw
        r = self.client.post("/cameras", json={"id": "c1", "fps": 5}).json()
        self.assertTrue(fw.stopped, "fps 를 바꿨는데 워커를 멈추지 않았다")
        self.assertTrue(r["restarted"])
        self.assertEqual(self.start_calls, ["c1"], "재시작(_start) 이 한 번 불려야 한다")
        self.assertEqual(camera_registry.get("c1")["fps"], 5.0)

    def test_same_fps_does_not_restart(self):
        """② fps 가 그대로면 스트림을 끊지 않는다."""
        self._register(2.0)
        fw = _FakeWorker(running=True)
        worker.manager._workers["c1"] = fw
        r = self.client.post("/cameras", json={"id": "c1", "fps": 2, "name": "이름만 바꿈"}).json()
        self.assertFalse(fw.stopped)
        self.assertFalse(r["restarted"])

    def test_disabled_camera_not_restarted(self):
        """③ 꺼진 카메라는 fps 를 바꿔도 켜지지 않는다."""
        self._register(2.0, enabled=False)
        fw = _FakeWorker(running=False)
        worker.manager._workers["c1"] = fw
        r = self.client.post("/cameras", json={"id": "c1", "fps": 5}).json()
        self.assertFalse(fw.stopped)
        self.assertFalse(r["restarted"])
        self.assertEqual(self.start_calls, [])

    def test_stop_failure_blocks_double_worker(self):
        """④ stop 미완료면 _start 를 부르지 않고(이중 워커 금지) restart_error 로 알린다."""
        self._register(2.0)
        fw = _FakeWorker(running=True, stop_ok=False)
        worker.manager._workers["c1"] = fw
        r = self.client.post("/cameras", json={"id": "c1", "fps": 5}).json()
        self.assertTrue(fw.stopped)
        self.assertFalse(r["restarted"])
        self.assertIn("restart_error", r)
        self.assertEqual(self.start_calls, [], "stop 미완료인데 재기동하면 이중 워커가 된다")


if __name__ == "__main__":
    unittest.main()
