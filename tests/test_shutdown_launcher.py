"""tests/test_shutdown_launcher.py — "VIGENT Safety 종료.bat" 동작 잠금 (2026-10-12).

서버 종료를 바탕화면 더블클릭 한 번으로 줄인 런처. 시작 런처("VIGENT Safety 시작.bat")와 짝.
여기서는 로직을 실행하지 않고(프로세스를 죽이는 테스트는 위험·비결정) **핵심 계약이 파일에 살아 있는지**만 잠근다:
  ① 포트 8010 을 쥔 프로세스를 트리째(/T) 강제(/F) 종료한다 — 자식 go2rtc·ffmpeg 도 함께 내려간다(프로세스 트리 실측 확인).
  ② go2rtc 고아 백스톱이 있다.
  ③ 이미 꺼져 있으면 그 사실을 알린다(멱등).
  ④ 한글 출력을 위해 chcp 65001.
"""
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BAT = _ROOT / "VIGENT Safety 종료.bat"


class ShutdownLauncher(unittest.TestCase):
    def setUp(self):
        self.assertTrue(_BAT.exists(), "종료 런처가 없다")
        self.t = _BAT.read_text(encoding="utf-8")

    def test_chcp_utf8(self):
        self.assertIn("chcp 65001", self.t)

    def test_kills_port_8010_process_tree(self):
        self.assertIn("set PORT=8010", self.t)
        self.assertIn(':%PORT% ', self.t, "netstat 에서 포트로 PID 를 찾지 않는다")
        self.assertRegex(self.t, r"taskkill /F /T /PID %%a", "포트 소유 프로세스를 트리째(/T) 강제 종료하지 않는다 — go2rtc·ffmpeg 자식이 남는다")

    def test_go2rtc_backstop(self):
        self.assertIn("go2rtc.exe", self.t)
        self.assertIn("taskkill /F /T /IM go2rtc.exe", self.t, "go2rtc 고아 백스톱이 없다")

    def test_idempotent_message(self):
        self.assertIn("이미 꺼져 있음", self.t, "서버가 이미 꺼져 있을 때의 안내가 없다")


if __name__ == "__main__":
    unittest.main()
