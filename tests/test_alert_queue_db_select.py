"""[2026-10-11] 경보 큐 DB 선택 — 테스트 러너 감지 정정 회귀 테스트.

실측 사고: 10-08 커밋 1442750 의 감지 조건 `"unittest" in sys.modules` 는 `import torch` 만으로 참이 돼, 10-08 이후 **진짜 서버가
매번 임시 DB** 를 썼다(기동 로그 "테스트 러너 감지 — 임시 DB", 운영 data/alert_queue.db 수정 시각 10-08 22:44 에 멈춤).
이 파일이 고정하는 것:
  ① torch 가 올라와 `unittest in sys.modules` 가 참인 상태에서도, 실행 파일이 서버(main.py / -m uvicorn / -c)면 **운영 DB**.
  ② 진짜 러너(python -m unittest Windows/POSIX 형태 · pytest · tests/test_x.py 직접 실행 · pytest 모듈 존재)면 **임시 DB**.
  ③ 지금 이 스위트 자체가 러너로 감지된다(= 테스트가 운영 DB 를 건드리는 사고의 구조적 방어가 살아 있다).
  ④ 기동 로그에 "경보 큐 DB: 운영|임시(테스트 러너)|… — 경로" 한 줄이 찍힌다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import alert_queue  # noqa: E402
from _isolate import isolate_alerts  # noqa: E402

_OPS = (_ROOT / "data" / "alert_queue.db").resolve()
_CLEAN_ENV = {"VIGENT_ALERT_DB": "", "VIGENT_ALERT_DB_ALLOW_REAL": "", "PYTEST_CURRENT_TEST": ""}


def _env_clean():
    """세 변수를 지운 환경(mock.patch.dict 는 빈 문자열 대신 pop 이 필요해 수동)."""
    saved = {k: os.environ.get(k) for k in _CLEAN_ENV}
    for k in _CLEAN_ENV:
        os.environ.pop(k, None)

    def _restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return _restore


class ServerProcessUsesOpsDb(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_env_clean())
        self.assertIn("unittest", sys.modules, "전제: 러너 안이라 unittest 는 올라와 있다(예전 판정이 깨지던 바로 그 조건)")

    def test_server_argv_forms_resolve_to_ops_db_even_with_unittest_loaded(self):
        for argv in (["D:\\vigent_original\\vigent-core\\main.py"], ["main.py"], ["python.exe -m uvicorn"],
                     ["/opt/vigent/.venv/lib/python3.11/site-packages/uvicorn/__main__.py"], ["-c"], [""]):
            with self.subTest(argv=argv), mock.patch.object(sys, "argv", argv), \
                 mock.patch.dict(sys.modules, {}, clear=False):
                sys.modules.pop("pytest", None); sys.modules.pop("_pytest", None)
                self.assertFalse(alert_queue._is_test_runner(), argv)
                self.assertEqual(alert_queue._resolve_db_path().resolve(), _OPS, argv)

    def test_torch_loaded_does_not_make_it_a_runner(self):
        """torch 를 실제로 올린 뒤에도(= unittest 가 sys.modules 에 있음) 서버 argv 면 운영 DB."""
        import torch  # noqa: F401  개발기·CI 모두 설치돼 있다(없으면 skip)
        with mock.patch.object(sys, "argv", ["main.py"]):
            self.assertIn("unittest", sys.modules)
            self.assertEqual(alert_queue._resolve_db_path().resolve(), _OPS)


class RunnerProcessUsesTempDb(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_env_clean())

    def _assert_temp(self, label):
        p = alert_queue._resolve_db_path()
        self.assertNotEqual(p.resolve(), _OPS, f"{label}: 러너인데 운영 DB 를 가리킨다")
        self.assertTrue(str(p).startswith(tempfile.gettempdir()), f"{label}: 임시 디렉터리가 아니다 {p}")
        self.assertIn("vigent_test_alert_queue_", str(p))

    def test_runner_argv_forms(self):
        for argv in (["python.exe -m unittest"],                                  # Windows 실측
                     ["C:\\Users\\x\\AppData\\Local\\Programs\\Python\\Python311\\Lib\\unittest\\__main__.py"],
                     ["/usr/lib/python3.11/unittest/__main__.py"],                # POSIX·CI
                     ["/venv/bin/pytest"], ["pytest.exe"], ["py.test"],
                     ["tests/test_x.py"], ["D:\\repo\\tests\\test_x.py"]):     # 직접 실행(unittest.main)
            with self.subTest(argv=argv), mock.patch.object(sys, "argv", argv):
                self.assertTrue(alert_queue._is_test_runner(), argv)
                self._assert_temp(str(argv))

    def test_pytest_traces_count_as_runner(self):
        with mock.patch.object(sys, "argv", ["main.py"]), mock.patch.dict(sys.modules, {"pytest": mock.Mock()}):
            self.assertTrue(alert_queue._is_test_runner()); self._assert_temp("pytest in sys.modules")
        with mock.patch.object(sys, "argv", ["main.py"]), mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x::y"}):
            self.assertTrue(alert_queue._is_test_runner()); self._assert_temp("PYTEST_CURRENT_TEST")

    def test_this_suite_is_detected_as_runner(self):
        """③ 지금 돌고 있는 이 프로세스가 러너로 잡혀야 한다 — 아니면 스위트가 운영 큐를 건드린다."""
        self.assertTrue(alert_queue._is_test_runner(), f"argv0={sys.argv[:1]} 를 러너로 못 잡는다")
        self.assertNotEqual(alert_queue._DB_PATH.resolve(), _OPS, "import 시점에 운영 DB 를 잡았다")

    def test_allow_real_and_explicit_env_still_work(self):
        with mock.patch.object(sys, "argv", ["python.exe -m unittest"]):
            with mock.patch.dict(os.environ, {"VIGENT_ALERT_DB_ALLOW_REAL": "1"}):
                self.assertEqual(alert_queue._resolve_db_path().resolve(), _OPS)
            with mock.patch.dict(os.environ, {"VIGENT_ALERT_DB": str(_ROOT / "tests" / "x.db")}):
                self.assertEqual(alert_queue._resolve_db_path(), _ROOT / "tests" / "x.db")


class StartupLogsDbKind(unittest.TestCase):
    def setUp(self):
        self.addCleanup(isolate_alerts())

    def test_db_kind_words(self):
        with mock.patch.object(alert_queue, "_DB_PATH", _ROOT / "data" / "alert_queue.db"):
            self.assertEqual(alert_queue.db_kind(), "운영")
        with mock.patch.object(alert_queue, "_DB_PATH", Path(tempfile.gettempdir()) / "vigent_test_alert_queue_1" / "alert_queue.db"):
            self.assertEqual(alert_queue.db_kind(), "임시(테스트 러너)")
        with mock.patch.object(alert_queue, "_DB_PATH", Path("/x/y.db")), mock.patch.dict(os.environ, {"VIGENT_ALERT_DB": "/x/y.db"}):
            self.assertEqual(alert_queue.db_kind(), "환경변수 VIGENT_ALERT_DB")

    def test_start_logs_one_line_with_kind_and_path(self):
        with mock.patch.object(alert_queue, "_loop"), self.assertLogs("vigent.alert_queue", level="INFO") as cm:
            alert_queue.start()
        alert_queue.stop()
        lines = [m for m in cm.output if "경보 큐 DB:" in m]
        self.assertEqual(len(lines), 1, cm.output)
        self.assertIn(alert_queue.db_kind(), lines[0]); self.assertIn(str(alert_queue._DB_PATH), lines[0])


if __name__ == "__main__":
    unittest.main()
