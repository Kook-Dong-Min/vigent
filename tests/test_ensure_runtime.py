"""tests/test_ensure_runtime.py — "VIGENT Safety 시작.bat" 자동 준비(없는 것만 받기) 잠금 (2026-11-16).

런처가 서버를 띄우기 전에 scripts/ensure_runtime.ps1(파이썬·.venv) → scripts/ensure_runtime.py(패키지·VC++·GPU·모델 파일)를 부른다.
여기서 잠그는 계약:
  ① **갖춰진 환경은 건드리지 않는다** — 없는 것이 없으면 설치·내려받기·상태 기록을 하나도 하지 않는다(규칙 6).
  ② 없는 것만 받는다 — 패키지는 "설치 여부"만 보고(버전 안 봄), 모델 파일은 "없을 때만" 받는다.
     크기가 다른 파일은 **덮어쓰지 않는다**(일부러 바꿔 둔 모델일 수 있다).
  ③ 받았다는 말만 믿지 않는다 — 내려받기가 성공을 돌려줘도 파일이 없으면 실패다(규칙 11).
  ④ 필수 항목 실패 = 종료코드 1(서버를 띄우지 않는다), 선택 항목 실패 = 경고만.
  ⑤ GPU용 torch 는 새로 설치한 환경에서만, 실제 GPU 연산으로 확인하고, 안 되면 CPU용으로 되돌린다.
★기계 상태에 의존하지 않는다: 그래픽카드·레지스트리·디스크·인터넷·OS 는 전부 mock 으로 고정한다(CI 는 ubuntu).
실제 동작(받기·풀기·가상환경 생성)은 2026-11-16 임시 사본에서 따로 돌려 확인했다 — docs/SETUP_NEW_MACHINE.md §0-a.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

import ensure_runtime as ER  # noqa: E402
import fetch_weights as FW  # noqa: E402

_BAT = _ROOT / "VIGENT Safety 시작.bat"
_PS1 = _ROOT / "scripts" / "ensure_runtime.ps1"


def _pe_bytes(major: int, minor: int) -> bytes:
    """링커 버전만 채운 최소 PE 헤더(MZ 스텁 0x40 바이트 + 'PE\\0\\0' + COFF 20 + optional 앞 4)."""
    head = bytearray(0x40)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    return bytes(head) + b"PE\0\0" + bytes(20) + b"\x0b\x02" + bytes([major, minor]) + bytes(16)


class LauncherContract(unittest.TestCase):
    """런처·준비기 파일에 핵심 계약이 살아 있는지(실행하지 않고 텍스트로)."""

    def test_bat_prepares_before_starting_server_and_stops_on_failure(self):
        b = _BAT.read_bytes()
        self.assertFalse(b.startswith(b"\xef\xbb\xbf"), "bat 에 BOM 이 있으면 첫 줄 @echo off 가 깨진다")
        self.assertEqual(b.count(b"\n"), b.count(b"\r\n"), "bat 은 CRLF 여야 한다(cmd 는 LF 배치에서 goto 가 깨질 수 있다)")
        t = b.decode("utf-8")
        self.assertIn("chcp 65001", t)
        i_up = t.index("서버가 이미 동작 중")
        i_prep = t.index(r'-File "%~dp0scripts\ensure_runtime.ps1"')
        i_run = t.index(r'-File "%~dp0run.ps1"')
        self.assertLess(i_up, i_prep, "서버가 이미 떠 있으면 준비 단계 없이 브라우저만 열어야 한다")
        self.assertLess(i_prep, i_run, "준비 단계는 서버를 띄우기 전에 와야 한다")
        after = t[i_prep:i_run]
        self.assertIn("if errorlevel 1 goto prep_fail", after, "준비 실패 시 서버를 띄우지 않고 멈춰야 한다")
        fail = t[t.index(":prep_fail"):t.index(":up")]
        self.assertIn("pause", fail)
        self.assertIn("exit /b 1", fail)

    def test_ps1_encoding_and_python_source(self):
        b = _PS1.read_bytes()
        self.assertTrue(b.startswith(b"\xef\xbb\xbf"), "ps1 은 UTF-8 BOM 이어야 한다(PS 5.1 한글)")
        self.assertEqual(b.count(b"\n"), b.count(b"\r\n"))
        t = b.decode("utf-8-sig")
        self.assertIn(".python-version", t, "파이썬 버전은 .python-version 한 곳에서 읽는다")
        self.assertIn("https://api.nuget.org/v3-flatcontainer/python/$PyVer/python.$PyVer.nupkg", t)
        self.assertIn("Get-AuthenticodeSignature", t)
        self.assertIn("*Python Software Foundation*", t, "받은 python.exe 의 서명자를 확인해야 한다")
        self.assertIn('"$LocalDir.staging"', t, "풀다 만 폴더가 완성본으로 보이지 않게 임시 폴더에 풀고 옮긴다")
        self.assertIn(r'scripts\ensure_runtime.py', t)
        self.assertNotIn('Test-Python "python"', t, "PATH 의 bare python 은 쓰지 않는다(run.ps1 과 같은 원칙)")
        self.assertNotIn("Remove-Item $VenvDir", t, "깨진 .venv 를 알아서 지우면 안 된다 — 안내만")
        self.assertNotIn("Remove-Item $LocalDir", t, "깨진 .python 은 지우지 않고 이름을 바꿔 둔다")

    def test_local_python_dir_is_gitignored(self):
        rules = {ln for ln in (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
        self.assertIn(".python/", rules)
        self.assertIn(".python.*/", rules)

    def test_setup_env_has_no_preflight_switch(self):
        s = (_ROOT / "scripts" / "setup_env.py").read_text(encoding="utf-8")
        self.assertIn('"--no-preflight"', s)
        self.assertIn("if a.no_preflight:", s)


class MissingPackages(unittest.TestCase):
    def test_reports_only_absent_names_ignoring_versions(self):
        have = {"fastapi": "0.1.0", "torch": "9.9.9+cu130"}        # 버전이 핀과 달라도 '있음'

        def version_of(name: str) -> str:
            if name not in have:
                raise md.PackageNotFoundError(name)
            return have[name]

        pins = {"fastapi": "0.137.2", "torch": "2.12.0", "rfdetr": "1.12.1", "psutil": "7.2.2"}
        self.assertEqual(ER.missing_packages(pins, version_of), ["rfdetr", "psutil"])
        self.assertEqual(ER.missing_packages({"fastapi": "0.137.2"}, version_of), [])

    def test_real_requirements_have_the_pins_the_gpu_step_needs(self):
        pins = ER.SE.requirement_pins()
        for name in ("torch", "torchvision", "rfdetr", "fastapi", "uvicorn"):
            self.assertIn(name, pins)
        self.assertNotIn("+", pins["torch"], "requirements 의 torch 핀은 로컬 버전(+cuXXX) 없이 적는다 — GPU 단계가 붙인다")


class GpuPlan(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(ER.gpu_plan("2.12.0+cu130", ("GTX", "616.92"))[0], "keep")
        self.assertEqual(ER.gpu_plan("2.12.0+cu130", None)[0], "keep")
        self.assertEqual(ER.gpu_plan("2.12.0", None)[0], "skip")
        self.assertEqual(ER.gpu_plan("2.12.0", ("GTX 1050", "472.12"))[0], "skip")
        self.assertIn("580", ER.gpu_plan("2.12.0", ("GTX 1050", "472.12"))[1])
        self.assertEqual(ER.gpu_plan("2.12.0", ("X", "[N/A]"))[0], "skip")
        self.assertEqual(ER.gpu_plan("2.12.0", ("RTX", "580.00"))[0], "swap")
        self.assertEqual(ER.gpu_plan("2.12.0", ("GTX 1650 Ti", "616.92"))[0], "swap")


class EnsureGpuTorch(unittest.TestCase):
    def setUp(self):
        self.pip = mock.patch.object(ER.SE, "_pip", return_value=0).start()
        mock.patch.object(ER.SE, "installed_versions", return_value={"torch": "2.12.0", "torchvision": "0.27.0"}).start()
        mock.patch.object(ER.SE, "requirement_pins", return_value={"torch": "2.12.0", "torchvision": "0.27.0"}).start()
        self.nv = mock.patch.object(ER, "nvidia_gpu", return_value=("GTX 1650 Ti", "616.92")).start()
        self.run = mock.patch.object(ER.subprocess, "run").start()
        mock.patch.object(ER, "say").start()
        mock.patch.dict(os.environ, {}, clear=False).start()
        os.environ.pop("VIGENT_BOOTSTRAP_NO_GPU", None)
        self.addCleanup(mock.patch.stopall)

    def test_already_decided_does_nothing(self):
        st = {"gpu": "cpu: 드라이버 낮음"}
        ER.ensure_gpu_torch(st)
        self.pip.assert_not_called()
        self.nv.assert_not_called()

    def test_opt_out_env_skips_without_probing(self):
        os.environ["VIGENT_BOOTSTRAP_NO_GPU"] = "1"
        st: dict = {}
        ER.ensure_gpu_torch(st)
        self.pip.assert_not_called()
        self.nv.assert_not_called()
        self.assertTrue(st["gpu"].startswith("cpu:"))

    def test_swap_then_real_cuda_op_confirms(self):
        self.run.return_value = mock.Mock(returncode=0, stdout="2.12.0+cu130 · GTX 1650 Ti\n", stderr="")
        st: dict = {}
        ER.ensure_gpu_torch(st)
        self.assertEqual(st["gpu"], "cuda")
        self.assertEqual(self.pip.call_count, 1)
        args = self.pip.call_args.args
        self.assertEqual(args[:5], ("install", "--index-url", "https://download.pytorch.org/whl/cu130", "--force-reinstall", "--no-deps"))
        self.assertIn("torch==2.12.0+cu130", args)
        self.assertIn("torchvision==0.27.0+cu130", args)
        self.assertIn("device='cuda'", self.run.call_args.args[0][2], "is_available() 만이 아니라 실제 GPU 연산으로 확인해야 한다")

    def test_cuda_op_failure_restores_cpu_build(self):
        self.run.return_value = mock.Mock(returncode=1, stdout="", stderr="RuntimeError: CUDA error: no kernel image is available\n")
        st: dict = {}
        ER.ensure_gpu_torch(st)
        self.assertTrue(st["gpu"].startswith("cpu:"))
        self.assertIn("no kernel image", st["gpu"])
        self.assertEqual(self.pip.call_count, 2, "GPU용을 남겨 두면 구형 카드에서 추론이 죽는다 — CPU용으로 되돌려야 한다")
        back = self.pip.call_args.args
        self.assertIn("torch==2.12.0", back)
        self.assertIn("torchvision==0.27.0", back)
        self.assertNotIn("--index-url", back)

    def test_download_failure_retries_on_later_runs_then_gives_up(self):
        self.pip.return_value = 1
        st: dict = {}
        for n in range(1, ER.GPU_MAX_TRIES):
            ER.ensure_gpu_torch(st)
            self.assertEqual(st["gpu_tries"], n)
            self.assertNotIn("gpu", st, "결론을 적지 않아야 다음 실행에서 다시 해 본다")
        ER.ensure_gpu_torch(st)
        self.assertTrue(st["gpu"].startswith("cpu:"))
        self.run.assert_not_called()


class VcRuntime(unittest.TestCase):
    def test_pe_linker_version(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "a.pyd").write_bytes(_pe_bytes(14, 51))
            (d / "not_pe.dll").write_bytes(b"hello, not an executable" * 8)
            (d / "short.dll").write_bytes(b"MZ")
            self.assertEqual(ER.pe_linker_version(d / "a.pyd"), (14, 51))
            self.assertIsNone(ER.pe_linker_version(d / "not_pe.dll"))
            self.assertIsNone(ER.pe_linker_version(d / "short.dll"))
            self.assertIsNone(ER.pe_linker_version(d / "missing.dll"))

    def test_required_version_is_max_of_msvc_built_files(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertIsNone(ER.required_vc_version(d))
            (d / "pkg").mkdir()
            (d / "pkg" / "x.pyd").write_bytes(_pe_bytes(14, 44))
            (d / "pkg" / "y.dll").write_bytes(_pe_bytes(14, 51))
            (d / "openblas.dll").write_bytes(_pe_bytes(2, 99))       # MSVC 가 아닌 도구로 만든 파일은 세지 않는다
            self.assertEqual(ER.required_vc_version(d), (14, 51))

    def test_sufficient(self):
        self.assertTrue(ER.vc_runtime_sufficient((14, 51), (14, 51)))
        self.assertTrue(ER.vc_runtime_sufficient((14, 52), (14, 51)))
        self.assertTrue(ER.vc_runtime_sufficient((14, 30), None))
        self.assertFalse(ER.vc_runtime_sufficient((14, 44), (14, 51)))
        self.assertFalse(ER.vc_runtime_sufficient(None, (14, 51)))
        self.assertFalse(ER.vc_runtime_sufficient(None, None))

    def test_ensure_installs_only_when_missing_or_old(self):
        with mock.patch.object(ER, "_is_windows", return_value=True), mock.patch.object(ER, "say"), \
                mock.patch.object(ER, "required_vc_version", return_value=(14, 51)), \
                mock.patch.object(ER, "install_vc_redist", return_value=True) as inst:
            with mock.patch.object(ER, "vc_runtime_version", return_value=(14, 51)):
                self.assertFalse(ER.ensure_vc_runtime())
                inst.assert_not_called()
            with mock.patch.object(ER, "vc_runtime_version", return_value=(14, 29)):
                self.assertTrue(ER.ensure_vc_runtime())
                self.assertEqual(inst.call_count, 1)
            with mock.patch.object(ER, "vc_runtime_version", return_value=None):
                self.assertTrue(ER.ensure_vc_runtime())
                self.assertEqual(inst.call_count, 2)

    def test_ensure_is_noop_off_windows(self):
        with mock.patch.object(ER, "_is_windows", return_value=False), \
                mock.patch.object(ER, "install_vc_redist") as inst:
            self.assertFalse(ER.ensure_vc_runtime())
            inst.assert_not_called()

    def test_unsigned_installer_is_never_executed(self):
        def fake_download(url: str, dest: Path) -> None:
            dest.write_bytes(b"x")

        with mock.patch.object(ER, "_download", side_effect=fake_download), mock.patch.object(ER, "say"), \
                mock.patch.object(ER, "authenticode_ok", return_value=False), \
                mock.patch.object(ER.subprocess, "run") as run:
            self.assertFalse(ER.install_vc_redist())
            run.assert_not_called()

    def test_installer_exit_codes(self):
        def fake_download(url: str, dest: Path) -> None:
            dest.write_bytes(b"x")

        for rc, want in ((0, True), (1638, True), (3010, True), (1602, False), (1603, False)):
            with mock.patch.object(ER, "_download", side_effect=fake_download), mock.patch.object(ER, "say"), \
                    mock.patch.object(ER, "authenticode_ok", return_value=True), \
                    mock.patch.object(ER.subprocess, "run", return_value=mock.Mock(returncode=rc)) as run:
                self.assertEqual(ER.install_vc_redist(), want, rc)
                self.assertEqual(run.call_args.args[0][1:], ["/install", "/passive", "/norestart"])

    def test_dll_failure_signature(self):
        self.assertTrue(ER.looks_like_dll_failure(1, "ImportError: DLL load failed while importing _C: 지정된 모듈을 찾을 수 없습니다."))
        self.assertTrue(ER.looks_like_dll_failure(1, "OSError: [WinError 126] ... msvcp140.dll"))
        self.assertTrue(ER.looks_like_dll_failure(3221225477, ""), "0xC0000005 로 통째로 죽는 것도 낮은 런타임의 모습이다")
        self.assertTrue(ER.looks_like_dll_failure(-1073741819, ""))
        self.assertFalse(ER.looks_like_dll_failure(1, "ModuleNotFoundError: No module named 'rfdetr'"))


class PreparePackages(unittest.TestCase):
    """패키지 단계의 흐름 — 무엇을 언제 부르고 상태를 어떻게 남기는가."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.state = Path(self.td.name) / "vigent_bootstrap.json"
        mock.patch.object(ER, "state_path", return_value=self.state).start()
        mock.patch.object(ER, "say").start()
        mock.patch.object(ER, "warn_if_disk_low").start()
        mock.patch.object(ER.SE, "requirement_pins", return_value={"torch": "2.12.0", "torchvision": "0.27.0", "fastapi": "0.137.2"}).start()
        self.missing = mock.patch.object(ER, "missing_packages", return_value=[]).start()
        self.venv = mock.patch.object(ER, "in_venv", return_value=True).start()
        self.install = mock.patch.object(ER, "install_packages", return_value=True).start()
        self.vc = mock.patch.object(ER, "ensure_vc_runtime", return_value=False).start()
        self.gpu = mock.patch.object(ER, "ensure_gpu_torch", side_effect=lambda st: st.setdefault("gpu", "cuda")).start()
        self.selftest = mock.patch.object(ER, "selftest_or_explain", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def _none_called(self):
        for m in (self.install, self.vc, self.gpu, self.selftest):
            m.assert_not_called()

    def test_complete_environment_is_left_untouched(self):
        self.assertTrue(ER.prepare_packages())
        self._none_called()
        self.assertFalse(self.state.exists(), "갖춰진 환경에는 상태 기록조차 남기지 않는다")

    def test_missing_packages_run_full_sequence_and_record_done(self):
        self.missing.return_value = ["rfdetr"]
        seen: list[str] = []
        self.install.side_effect = lambda: (seen.append(ER.load_state().get("setup")), True)[1]
        self.assertTrue(ER.prepare_packages())
        self.assertEqual(seen, ["pending"], "설치 전에 '진행 중'을 먼저 적어야 중간에 끊겨도 다음 실행이 이어서 한다")
        self.vc.assert_called_once()
        self.gpu.assert_called_once()
        self.selftest.assert_called_once_with(False)
        st = ER.load_state()
        self.assertEqual(st["setup"], "done")
        self.assertEqual(st["gpu"], "cuda")

    def test_refuses_to_install_outside_venv(self):
        self.missing.return_value = ["rfdetr"]
        self.venv.return_value = False
        self.assertFalse(ER.prepare_packages())
        self._none_called()
        self.assertFalse(self.state.exists())

    def test_install_failure_stops_and_stays_pending(self):
        self.missing.return_value = ["rfdetr"]
        self.install.return_value = False
        self.assertFalse(ER.prepare_packages())
        self.gpu.assert_not_called()
        self.selftest.assert_not_called()
        self.assertEqual(ER.load_state()["setup"], "pending")

    def test_selftest_failure_is_failure_and_stays_pending(self):
        self.missing.return_value = ["rfdetr"]
        self.selftest.return_value = False
        self.assertFalse(ER.prepare_packages())
        self.assertEqual(ER.load_state()["setup"], "pending", "설치 종료코드 0 이어도 불러오기가 안 되면 끝난 것이 아니다")

    def test_interrupted_setup_resumes_even_if_nothing_is_missing(self):
        ER.save_state({"setup": "pending"})
        self.assertTrue(ER.prepare_packages())
        self.install.assert_called_once()
        self.assertEqual(ER.load_state()["setup"], "done")

    def test_vc_attempt_is_passed_on_so_it_is_not_repeated(self):
        self.missing.return_value = ["rfdetr"]
        self.vc.return_value = True
        self.assertTrue(ER.prepare_packages())
        self.selftest.assert_called_once_with(True)

    def test_pending_gpu_retry_does_not_reinstall(self):
        ER.save_state({"setup": "done", "gpu_tries": 1})
        self.assertTrue(ER.prepare_packages())
        self.install.assert_not_called()
        self.vc.assert_not_called()
        self.gpu.assert_called_once()
        self.selftest.assert_called_once()

    def test_force_gpu_on_existing_environment(self):
        ER.save_state({"gpu": "cpu: 드라이버 낮음", "gpu_tries": 3})
        got: list[dict] = []
        self.gpu.side_effect = lambda st: (got.append(dict(st)), st.setdefault("gpu", "cuda"))[1]
        self.assertTrue(ER.prepare_packages(force_gpu=True))
        self.install.assert_not_called()
        self.assertNotIn("gpu", got[0], "--gpu 는 지난 결론을 지우고 다시 해 본다")
        self.assertEqual(got[0]["gpu_tries"], 0)

    def test_check_only_never_installs_or_writes(self):
        self.missing.return_value = ["rfdetr"]
        self.assertFalse(ER.prepare_packages(check_only=True))
        self._none_called()
        self.assertFalse(self.state.exists())
        self.missing.return_value = []
        self.assertTrue(ER.prepare_packages(check_only=True))

    def test_install_packages_uses_no_preflight_and_recounts(self):
        mock.patch.stopall()
        with mock.patch.object(ER, "say"), \
                mock.patch.object(ER.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(ER.SE, "requirement_pins", return_value={"a": "1", "b": "2"}), \
                mock.patch.object(ER.SE, "installed_versions", return_value={"a": "1"}):
            self.assertFalse(ER.install_packages(), "setup_env 가 0 을 돌려줘도 다시 세어 빠진 것이 있으면 실패다")
            cmd = run.call_args.args[0]
            self.assertTrue(cmd[1].endswith("setup_env.py"))
            self.assertIn("--no-preflight", cmd)
        with mock.patch.object(ER, "say"), \
                mock.patch.object(ER.subprocess, "run", return_value=mock.Mock(returncode=1)), \
                mock.patch.object(ER.SE, "installed_versions") as iv:
            self.assertFalse(ER.install_packages())
            iv.assert_not_called()


class Weights(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.wdir = self.root / "vigent-core" / "weights"
        self.wdir.mkdir(parents=True)
        self.man = {"base_url_env": "VIGENT_WEIGHTS_BASE_URL", "weights": [
            {"file": "req.pth", "slot": "ppe", "required": True, "url": "release:t", "size_bytes": 3},
            {"file": "pre.pth", "slot": "_pretrain", "required": True, "url": "https://example.invalid/pre.pth", "size_bytes": 4},
            {"file": "tool.exe", "slot": "_bin", "required": False, "url": "https://example.invalid/t.zip", "root_dest": "bin", "size_bytes": 2},
            {"file": "face.onnx", "slot": "privacy", "required": False, "url": "release:t", "size_bytes": 5},
            {"file": "rollback.pt", "slot": "ppe", "required": False, "url": "release:t", "size_bytes": 9},
        ]}
        mock.patch.object(FW, "_WEIGHTS", self.wdir).start()
        mock.patch.object(FW, "_ROOT", self.root).start()
        mock.patch.object(FW, "load_manifest", return_value=self.man).start()
        self.token = mock.patch.object(FW, "github_token", return_value="").start()
        mock.patch.object(ER, "say").start()
        self.addCleanup(mock.patch.stopall)
        self.calls: list[str] = []

    def _put(self, name: str, size: int) -> Path:
        e = next(w for w in self.man["weights"] if w["file"] == name)
        p = FW.target_path(e)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p

    def _fill_all(self):
        for w in ER.runtime_entries(self.man):
            self._put(w["file"], w["size_bytes"])

    def _downloader(self, fail: tuple[str, ...] = (), lie: tuple[str, ...] = ()):
        def download(entry: dict, man: dict) -> tuple[bool, str]:
            self.calls.append(entry["file"])
            if entry["file"] in fail:
                return False, "HTTP 404"
            if entry["file"] not in lie:
                self._put(entry["file"], entry["size_bytes"])
            return True, "다운로드·검증 완료"
        return download

    def test_runtime_entries_select_required_plus_tools_and_privacy(self):
        self.assertEqual([w["file"] for w in ER.runtime_entries(self.man)], ["req.pth", "pre.pth", "tool.exe", "face.onnx"])

    def test_real_manifest_selection(self):
        man = json.loads((_ROOT / "weights_manifest.json").read_text(encoding="utf-8"))
        names = {w["file"] for w in ER.runtime_entries(man)}
        required = {w["file"] for w in man["weights"] if w.get("required")}
        self.assertTrue(required <= names, "필수 항목은 전부 받는 대상이어야 한다")
        for f in ("go2rtc.exe", "ffmpeg.exe", "face_detection_yunet.onnx"):
            self.assertIn(f, names)
        for f in ("yolo11m.pt", "ppe_rfdetr_v1.onnx", "forklift_rfdetr_fk510_smoke.pth"):
            self.assertNotIn(f, names, "롤백·다른 프로파일용 파일까지 매 실행 받을 이유가 없다")

    def test_entry_state(self):
        e = self.man["weights"][0]
        self.assertEqual(ER.entry_state(e), "missing")
        self._put("req.pth", 7)
        self.assertEqual(ER.entry_state(e), "size")
        self._put("req.pth", 3)
        self.assertEqual(ER.entry_state(e), "ok")

    def test_all_present_downloads_nothing(self):
        self._fill_all()
        self.assertTrue(ER.ensure_weights(download=self._downloader()))
        self.assertEqual(self.calls, [])
        self.token.assert_not_called()

    def test_only_missing_files_are_fetched(self):
        self._fill_all()
        FW.target_path(self.man["weights"][0]).unlink()
        FW.target_path(self.man["weights"][2]).unlink()
        self.assertTrue(ER.ensure_weights(download=self._downloader()))
        self.assertEqual(self.calls, ["req.pth", "tool.exe"])
        self.assertEqual((self.root / "bin" / "tool.exe").stat().st_size, 2)

    def test_size_mismatch_is_never_overwritten(self):
        self._fill_all()
        p = self._put("req.pth", 7)                       # 일부러 바꿔 둔 모델일 수 있다
        self.assertTrue(ER.ensure_weights(download=self._downloader()))
        self.assertEqual(self.calls, [])
        self.assertEqual(p.stat().st_size, 7)

    def test_required_failure_blocks_start(self):
        self._fill_all()
        FW.target_path(self.man["weights"][0]).unlink()
        self.assertFalse(ER.ensure_weights(download=self._downloader(fail=("req.pth",))))

    def test_optional_failure_only_warns(self):
        self._fill_all()
        FW.target_path(self.man["weights"][2]).unlink()
        FW.target_path(self.man["weights"][3]).unlink()
        self.assertTrue(ER.ensure_weights(download=self._downloader(fail=("tool.exe", "face.onnx"))))
        self.assertEqual(self.calls, ["tool.exe", "face.onnx"])

    def test_reported_success_without_file_is_failure(self):
        self._fill_all()
        FW.target_path(self.man["weights"][1]).unlink()
        self.assertFalse(ER.ensure_weights(download=self._downloader(lie=("pre.pth",))),
                         "받았다고 했는데 파일이 없으면 성공이 아니다(규칙 11)")

    def test_check_only_downloads_nothing(self):
        self.assertFalse(ER.ensure_weights(check_only=True, download=self._downloader()))
        self.assertEqual(self.calls, [])
        self._put("req.pth", 3)
        self._put("pre.pth", 4)
        self.assertTrue(ER.ensure_weights(check_only=True, download=self._downloader()), "선택 파일만 없으면 서버는 띄울 수 있다")
        self.assertEqual(self.calls, [])

    def test_token_looked_up_once_without_login_window_and_not_left_in_env(self):
        seen: dict[str, str | None] = {}

        def token() -> str:
            seen["GCM_INTERACTIVE"] = os.environ.get("GCM_INTERACTIVE")
            seen["GIT_TERMINAL_PROMPT"] = os.environ.get("GIT_TERMINAL_PROMPT")
            return "tok-for-test"

        self.token.side_effect = token
        during: list[str | None] = []

        def download(entry: dict, man: dict) -> tuple[bool, str]:
            during.append(os.environ.get("VIGENT_GITHUB_TOKEN"))
            self._put(entry["file"], entry["size_bytes"])
            return True, "ok"

        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("VIGENT_GITHUB_TOKEN", "GCM_INTERACTIVE", "GIT_TERMINAL_PROMPT"):
                os.environ.pop(k, None)
            self.assertTrue(ER.ensure_weights(download=download))
            self.assertEqual(self.token.call_count, 1, "항목마다 git 을 다시 부르지 않는다")
            self.assertEqual(seen, {"GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}, "로그인 창을 띄우지 않는다")
            self.assertEqual(during, ["tok-for-test"] * 4)
            for k in ("VIGENT_GITHUB_TOKEN", "GCM_INTERACTIVE", "GIT_TERMINAL_PROMPT"):
                self.assertNotIn(k, os.environ, f"{k} 가 실행 뒤에 남으면 안 된다")

    def test_no_token_lookup_when_nothing_comes_from_a_release(self):
        self._fill_all()
        FW.target_path(self.man["weights"][1]).unlink()      # pre.pth 는 일반 주소
        self.assertTrue(ER.ensure_weights(download=self._downloader()))
        self.token.assert_not_called()


class MainExitCode(unittest.TestCase):
    def _run(self, argv: list[str], pk: bool, wt: bool) -> tuple[int, mock.Mock, mock.Mock]:
        with mock.patch.object(ER, "say"), \
                mock.patch.object(ER, "prepare_packages", return_value=pk) as p, \
                mock.patch.object(ER, "ensure_weights", return_value=wt) as w:
            return ER.main(argv), p, w

    def test_exit_codes(self):
        self.assertEqual(self._run([], True, True)[0], 0)
        self.assertEqual(self._run([], False, True)[0], 1)
        self.assertEqual(self._run([], True, False)[0], 1)

    def test_weights_are_still_attempted_when_packages_fail(self):
        _, _, w = self._run([], False, True)
        w.assert_called_once_with(check_only=False)

    def test_check_never_forces_gpu(self):
        _, p, w = self._run(["--check", "--gpu"], True, True)
        p.assert_called_once_with(check_only=True, force_gpu=False)
        w.assert_called_once_with(check_only=True)
        _, p, _ = self._run(["--gpu"], True, True)
        p.assert_called_once_with(check_only=False, force_gpu=True)


if __name__ == "__main__":
    unittest.main()
