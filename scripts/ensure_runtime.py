#!/usr/bin/env python3
"""scripts/ensure_runtime.py — 서버를 띄우기 전에 "실행에 필요한 것"이 있는지 보고, **없는 것만** 받는다. [2026-11-16]

부르는 곳: "VIGENT Safety 시작.bat" → scripts\\ensure_runtime.ps1(파이썬·.venv 준비) → 이 파일(.venv 의 python 으로).
다 있으면 아무것도 받지 않는다 — 설치 목록과 파일 존재·크기만 본다(import·SHA 계산 없음, 그래서 빠르다).

하는 일
  1) 패키지: requirements.txt 에 적힌 것 중 **설치 안 된 것**이 있으면 scripts/setup_env.py 로 설치한다.
  2) 1) 에서 새로 설치했을 때만:
       - VC++ 런타임: torch·onnxruntime 이 msvcp140.dll 을 시스템에서 찾는다(build_portable.ps1 1b 실측). 없거나 낮으면 받는다.
       - GPU: NVIDIA 그래픽카드 + 드라이버 580 이상이면 torch 를 GPU용(cu130)으로 바꾸고, 실제 GPU 연산이 되는지 확인한다.
       - 불러오기 시험: 서버가 쓰는 패키지를 새 프로세스에서 실제로 import 해 본다(규칙 11 — 설치 종료코드 0 은 증거가 아니다).
  3) 모델·보조 파일: weights_manifest.json 의 필수 항목 + go2rtc·ffmpeg·얼굴 가림 모델 중 **없는 것만** 받는다(SHA256 대조).

일부러 건드리지 않는 것(이미 돌던 환경을 바꾸지 않기 위해 — 규칙 6)
  - 이미 설치된 패키지의 버전: 없는 것만 채운다. 버전 맞추기는 setup_env.py 를 직접 돌릴 때의 일이다.
  - 이미 있는 torch 의 CPU/GPU 종류: 이 파일이 **새로 설치한 환경에서만** GPU용으로 바꾼다(직접 바꾸려면 --gpu).
  - 크기가 매니페스트와 다른 모델 파일: 일부러 바꿔 둔 파일일 수 있어 덮어쓰지 않고 경고만 한다.

사용(.venv 의 python 으로):
    .\\.venv\\Scripts\\python.exe scripts\\ensure_runtime.py            # 없는 것 받기
    .\\.venv\\Scripts\\python.exe scripts\\ensure_runtime.py --check    # 받지 않고 무엇이 없는지만
    .\\.venv\\Scripts\\python.exe scripts\\ensure_runtime.py --gpu      # torch 를 GPU용으로 (다시) 시도
종료코드: 0 = 서버를 띄워도 된다 / 1 = 필수 항목이 없다(화면의 ★ 줄 참조)
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata as _md
import json
import os
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

# cp949 콘솔에서 한글·기호 print 가 죽지 않게(tests/test_new_machine_20261008.EntryScriptsUtf8 이 검사)
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

import fetch_weights as FW  # noqa: E402
import setup_env as SE  # noqa: E402

# 매니페스트에서 required 가 아니어도 받는 것: 확대뷰·웹캠 WebRTC 실행 파일(_bin: go2rtc·ffmpeg)과 얼굴 가림 모델(privacy).
#   없어도 서버는 뜨지만 기능이 줄어든다(스냅샷 폴백·person 박스 밖 얼굴 미가림) — "이 PC 에서 돌던 대로" 돌게 하려고 받는다.
RUNTIME_OPTIONAL_SLOTS = ("_bin", "privacy")

# GPU용 torch — requirements.txt 주석·docs/SETUP_NEW_MACHINE.md §1-b 와 같은 값(cu130 휠은 NVIDIA 드라이버 580 이상).
CUDA_TAG = "cu130"
CUDA_INDEX = "https://download.pytorch.org/whl/" + CUDA_TAG
MIN_NVIDIA_DRIVER = 580
GPU_MAX_TRIES = 3            # 내려받기 실패(인터넷 등)는 다음 실행에서 이 횟수까지 다시 해 본다

# Microsoft 공식 "최신 v14 재배포 패키지" 고정 주소.
#   ★실측(2026-11-16, 받아서 파일 버전·서명 확인): 이 주소(= aka.ms/vs/18/release/…)는 14.52.37029 를 준다.
#     scripts/build_portable.ps1 1b 가 쓰는 aka.ms/vs/17/release/… 는 **14.44.35211** 을 줘서, 설치되는 패키지가 요구하는
#     14.51(아래 required_vc_version 실측)에 못 미친다 — 그래서 그 주소를 쓰지 않는다.
VC_REDIST_URL = "https://aka.ms/vc14/vc_redist.x64.exe"
_VC_KEY = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"      # scripts/deploy/preflight.ps1 §6 과 같은 키

# 전체 설치 용량의 어림 — 문서 실측(docs/SETUP_NEW_MACHINE.md §0, 개발기): .venv ≈ 6 GB(cu130 torch 포함) + 가중치 ≈ 1.1 GB.
#   이 노트북 실측(2026-11-16, du): .venv 3.7 GB + vigent-core/weights 1.4 GB(선택 파일 포함) + bin 0.12 GB. 큰 쪽을 쓴다.
FULL_INSTALL_GB = 7

_NO_GPU_ENV = "VIGENT_BOOTSTRAP_NO_GPU"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _is_windows() -> bool:
    return os.name == "nt"


# ── 준비 상태 기록 ──────────────────────────────────────────────────────────────
def in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def state_path() -> Path:
    """준비 상태 기록 위치 — 이 파이썬 환경(.venv) 안. 환경을 지우면 기록도 같이 사라진다."""
    return Path(sys.prefix) / "vigent_bootstrap.json"


def load_state(path: Path | None = None) -> dict[str, Any]:
    try:
        d = json.loads((path or state_path()).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(st: dict[str, Any], path: Path | None = None) -> None:
    (path or state_path()).write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 1) 패키지 ───────────────────────────────────────────────────────────────────
def missing_packages(pins: dict[str, str] | None = None,
                     version_of: Callable[[str], str] = _md.version) -> list[str]:
    """requirements.txt 핀 중 **설치되지 않은** 이름. 버전은 보지 않는다(있는 환경을 바꾸지 않으려고)."""
    pins = SE.requirement_pins() if pins is None else pins
    out: list[str] = []
    for name in pins:
        try:
            version_of(name)
        except _md.PackageNotFoundError:
            out.append(name)
    return out


def install_packages() -> bool:
    """setup_env.py 로 설치한 뒤, **새 프로세스에서** 다시 세어 전부 깔렸는지 확인한다(종료코드만 믿지 않는다)."""
    rc = subprocess.run([sys.executable, str(_ROOT / "scripts" / "setup_env.py"), "--no-preflight"], cwd=str(_ROOT)).returncode
    if rc != 0:
        say(f"  ★[오류] 패키지 설치 실패(setup_env.py 종료코드 {rc}) — 바로 위 메시지를 확인하세요.")
        say("    인터넷이 끊겼던 것이라면 다시 실행하면 이어서 받습니다.")
        return False
    pins = SE.requirement_pins()
    have = SE.installed_versions(tuple(pins))
    still = [n for n in pins if n not in have]
    if still:
        say("  ★[오류] 설치가 끝났다는데 아직 없는 패키지: " + ", ".join(still))
        return False
    say(f"  패키지 {len(pins)}개 설치 확인(새 프로세스에서 다시 셈)")
    return True


def warn_if_disk_low(free_bytes: int | None = None) -> None:
    free_gb = (shutil.disk_usage(str(_ROOT)).free if free_bytes is None else free_bytes) / 1024 ** 3
    if free_gb < FULL_INSTALL_GB * 2:      # 2배는 pip 임시 파일·캐시 몫으로 잡은 여유(측정값 아님)
        say(f"  ★주의: 디스크 여유 {free_gb:.1f} GB — 전체 설치는 약 {FULL_INSTALL_GB} GB(문서 실측)에 내려받기 임시 공간이 더 듭니다. "
            "모자라면 설치가 중간에 실패할 수 있습니다.")


# ── 2-a) VC++ 런타임 ────────────────────────────────────────────────────────────
def vc_runtime_version() -> tuple[int, int] | None:
    """설치된 VC++ 재배포 패키지(x64) 버전 (major, minor). 없으면 None."""
    if not _is_windows():
        return None
    import winreg
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _VC_KEY, 0, winreg.KEY_READ | view) as k:
                if int(winreg.QueryValueEx(k, "Installed")[0]) != 1:
                    continue
                return int(winreg.QueryValueEx(k, "Major")[0]), int(winreg.QueryValueEx(k, "Minor")[0])
        except OSError:
            continue
    return None


def pe_linker_version(path: Path) -> tuple[int, int] | None:
    """Windows 실행 파일(.pyd/.dll) 헤더의 링커 버전 = 그 파일을 만든 빌드 도구 버전. 실행 파일이 아니면 None."""
    try:
        with path.open("rb") as f:
            head = f.read(0x40)
            if len(head) < 0x40 or head[:2] != b"MZ":
                return None
            f.seek(struct.unpack_from("<I", head, 0x3C)[0])
            b = f.read(28)          # 'PE\0\0'(4) + COFF 헤더(20) + optional 헤더 앞 4바이트(magic 2, 링커 major 1, minor 1)
            if len(b) < 28 or b[:4] != b"PE\0\0":
                return None
            return b[26], b[27]
    except OSError:
        return None


def required_vc_version(site_packages: Path | None = None) -> tuple[int, int] | None:
    """설치된 확장 모듈들의 MSVC(14.x) 링커 버전 **최댓값** = 필요한 VC++ 재배포 패키지의 하한.

    근거: Microsoft Learn "C++ binary compatibility" — 재배포 패키지는 앱 구성요소 중 가장 새 빌드 도구보다 같거나 새로워야 한다
    (scripts/deploy/preflight.ps1 §6 과 같은 방법). 고정 숫자를 두지 않고 **설치된 것을 그 자리에서 잰다** — 패키지 구성이
    바뀌어도 값이 낡지 않는다(규칙 9). 이 PC .venv 실측(2026-11-16): 432개 중 최댓값 14.51, 설치된 런타임 14.51.
    """
    sp = site_packages or Path(sysconfig.get_paths()["purelib"])
    best: tuple[int, int] | None = None
    for pat in ("*.pyd", "*.dll"):
        for p in sp.rglob(pat):
            v = pe_linker_version(p)
            if v and v[0] == 14 and (best is None or v > best):      # 14 가 아닌 것은 MSVC 가 아닌 도구(gfortran 등)로 만든 파일
                best = v
    return best


def vc_runtime_sufficient(have: tuple[int, int] | None, need: tuple[int, int] | None) -> bool:
    return have is not None and (need is None or have >= need)


def authenticode_ok(path: Path, signer: str) -> bool:
    """Windows 디지털 서명이 유효하고 서명자 이름에 signer 가 들어 있는가(PowerShell Get-AuthenticodeSignature)."""
    ps = ("$s = Get-AuthenticodeSignature -FilePath $env:VIGENT_SIG_FILE; "
          "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -like ('*' + $env:VIGENT_SIG_SIGNER + '*')) { exit 0 } else { exit 1 }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=120,
                           env={**os.environ, "VIGENT_SIG_FILE": str(path), "VIGENT_SIG_SIGNER": signer})
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "vigent-ensure-runtime"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.replace(dest)


def install_vc_redist() -> bool:
    """Microsoft VC++ 재배포 패키지를 받아 서명을 확인한 뒤 설치한다(관리자 권한 창이 뜬다). 성공 여부를 돌려준다."""
    d = Path(tempfile.mkdtemp(prefix="vigent_vcredist_"))
    exe = d / "vc_redist.x64.exe"
    try:
        say(f"    ↓ {VC_REDIST_URL}")
        _download(VC_REDIST_URL, exe)
        if not authenticode_ok(exe, "Microsoft Corporation"):
            say("  ★받은 VC++ 설치 파일의 Microsoft 서명을 확인하지 못해 실행하지 않았습니다.")
            return False
        say("  '이 앱이 디바이스를 변경하도록 허용하시겠습니까?' 창이 뜨면 [예] 를 눌러 주세요(1분쯤 걸립니다).")
        rc = subprocess.run([str(exe), "/install", "/passive", "/norestart"]).returncode
    except Exception as ex:  # noqa: BLE001  내려받기·실행 어느 쪽이 실패해도 판정은 뒤의 불러오기 시험이 한다
        say(f"  ★VC++ 런타임 설치를 못 했습니다: {type(ex).__name__}: {ex}")
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if rc in (0, 1638, 3010):       # 0 성공 · 1638 더 새 버전이 이미 있음 · 3010 성공(재부팅 권장)
        say("  VC++ 런타임 설치됨" + (" — Windows 재시작이 필요할 수 있습니다" if rc == 3010 else ""))
        return True
    say(f"  ★VC++ 런타임 설치가 끝나지 않았습니다(종료코드 {rc} — 창에서 [아니요] 를 눌렀거나 관리자 권한이 없는 경우).")
    return False


def ensure_vc_runtime() -> bool:
    """필요하면 VC++ 런타임을 받는다. 돌려주는 값 = 설치를 시도했는가(뒤에서 또 시도하지 않으려고)."""
    if not _is_windows():
        return False
    have, need = vc_runtime_version(), required_vc_version()
    fmt = lambda v: "없음" if v is None else f"{v[0]}.{v[1]}"
    if vc_runtime_sufficient(have, need):
        say(f"  VC++ 런타임: 설치됨 {fmt(have)} (설치된 패키지가 요구하는 최소 {fmt(need)})")
        return False
    say(f"  VC++ 런타임: 설치됨 {fmt(have)} · 필요 {fmt(need)} 이상 → Microsoft 공식 재배포 패키지를 받아 설치합니다.")
    install_vc_redist()
    after = vc_runtime_version()        # 설치 프로그램의 종료코드가 아니라 레지스트리를 다시 읽어 확인한다(규칙 11)
    if vc_runtime_sufficient(after, need):
        say(f"  VC++ 런타임 확인: 설치됨 {fmt(after)}")
    else:
        say(f"  ★VC++ 런타임이 여전히 {fmt(after)} 입니다(필요 {fmt(need)} 이상) — 아래 불러오기 시험으로 실제로 되는지 판정합니다.")
    return True


# ── 2-b) GPU용 torch ────────────────────────────────────────────────────────────
def nvidia_gpu() -> tuple[str, str] | None:
    """(그래픽카드 이름, 드라이버 버전). nvidia-smi 가 없거나 실패하면 None."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    line = next((ln for ln in (r.stdout or "").splitlines() if "," in ln), "") if r.returncode == 0 else ""
    if not line:
        return None
    name, drv = (x.strip() for x in line.rsplit(",", 1))
    return name, drv


def gpu_plan(torch_version: str, nvidia: tuple[str, str] | None) -> tuple[str, str]:
    """torch 를 GPU용으로 바꿀지 정한다 → ('keep' | 'skip' | 'swap', 이유). 순수 함수(테스트가 고정)."""
    if "+cu" in torch_version:
        return "keep", f"이미 GPU용 torch({torch_version})"
    if nvidia is None:
        return "skip", "NVIDIA 그래픽카드를 찾지 못함(nvidia-smi 없음) — CPU 로 동작"
    name, drv = nvidia
    try:
        major = int(drv.split(".")[0])
    except ValueError:
        return "skip", f"{name}: 드라이버 버전 '{drv}' 을 읽지 못함 — CPU 로 동작"
    if major < MIN_NVIDIA_DRIVER:
        return "skip", (f"{name}: 드라이버 {drv} 가 낮음(GPU용 torch {CUDA_TAG} 는 {MIN_NVIDIA_DRIVER} 이상 필요) — CPU 로 동작. "
                        "드라이버를 올린 뒤 `.venv\\Scripts\\python.exe scripts\\ensure_runtime.py --gpu`")
    return "swap", f"{name} · 드라이버 {drv}"


_CUDA_PROBE = ("import torch, torch.nn.functional as F\n"
               "assert torch.cuda.is_available(), 'torch.cuda.is_available() == False'\n"
               "x = torch.randn(1, 3, 32, 32, device='cuda'); w = torch.randn(4, 3, 3, 3, device='cuda')\n"
               "F.conv2d(x, w).sum().item()\n"
               "print(torch.__version__, '·', torch.cuda.get_device_name(0))\n")


def ensure_gpu_torch(st: dict[str, Any]) -> None:
    """NVIDIA GPU 가 있으면 torch 를 GPU용으로 바꾸고 **실제 GPU 연산**으로 확인한다. 결론은 st['gpu'] 에 적는다.

    안 되면 CPU용으로 되돌린다 — GPU용을 남겨 두면 `cuda.is_available()` 은 True 인데 연산이 죽는 구형 카드에서
    서버가 CPU 보다 못한 상태(추론 오류)가 된다.
    """
    if st.get("gpu"):
        return
    pins = SE.requirement_pins()
    cur = SE.installed_versions(("torch", "torchvision")).get("torch", "")
    if os.environ.get(_NO_GPU_ENV, "").strip() == "1":
        action, why = "skip", f"{_NO_GPU_ENV}=1 로 건너뜀 — CPU 로 동작"
    else:
        action, why = gpu_plan(cur, nvidia_gpu())
    if action == "keep":
        st["gpu"] = "cuda"
        say(f"  GPU: {why}")
        return
    if action == "skip":
        st["gpu"] = "cpu: " + why
        say(f"  GPU: {why}")
        return
    say(f"  GPU: {why} → torch 를 GPU용({CUDA_TAG})으로 바꿉니다(수 GB 를 받습니다 — 시간이 걸립니다).")
    cuda = [f"torch=={pins['torch']}+{CUDA_TAG}", f"torchvision=={pins['torchvision']}+{CUDA_TAG}"]
    if SE._pip("install", "--index-url", CUDA_INDEX, "--force-reinstall", "--no-deps", *cuda, check=False) != 0:
        st["gpu_tries"] = int(st.get("gpu_tries", 0)) + 1
        if st["gpu_tries"] >= GPU_MAX_TRIES:
            st["gpu"] = f"cpu: GPU용 torch 내려받기 {st['gpu_tries']}회 실패"
            say(f"  ★GPU용 torch 를 {st['gpu_tries']}번 받지 못해 그만둡니다 — CPU 로 동작합니다. "
                "나중에 다시: `.venv\\Scripts\\python.exe scripts\\ensure_runtime.py --gpu`")
        else:
            say(f"  ★GPU용 torch 를 받지 못했습니다({st['gpu_tries']}/{GPU_MAX_TRIES}회) — 이번에는 CPU 로 동작하고 다음 실행 때 다시 해 봅니다.")
        return
    probe = subprocess.run([sys.executable, "-c", _CUDA_PROBE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(_ROOT))
    if probe.returncode == 0:
        st["gpu"] = "cuda"
        say(f"  GPU 연산 확인: torch {probe.stdout.strip()}")
        return
    reason = ((probe.stderr or "").strip().splitlines() or ["원인 불명"])[-1][:200]
    say(f"  ★GPU용 torch 를 깔았지만 이 PC 에서 GPU 연산이 되지 않습니다 — CPU용으로 되돌립니다. 이유: {reason}")
    SE._pip("install", "--force-reinstall", "--no-deps", f"torch=={pins['torch']}", f"torchvision=={pins['torchvision']}", check=False)
    st["gpu"] = "cpu: GPU 연산 실패 — " + reason


# ── 2-c) 불러오기 시험 ──────────────────────────────────────────────────────────
_SELFTEST = ("import cv2, numpy, torch, torchvision, onnxruntime, rfdetr, supervision, trackers, rtmlib, fastapi, uvicorn, yaml, psutil\n"
             "import torch.nn.functional as F\n"
             "F.conv2d(torch.zeros(1, 3, 16, 16), torch.zeros(2, 3, 3, 3)).sum().item()\n"
             "print('cv2', cv2.__version__, '· torch', torch.__version__, '· cuda', torch.cuda.is_available(),"
             " '· onnxruntime', onnxruntime.__version__, '· rfdetr OK')\n")

_DLL_SIGNS = ("msvcp140", "vcruntime140", "dll load failed", "winerror 126")     # deploy/portable/VIGENT_시작.bat 자가진단과 같은 서명


def run_selftest() -> tuple[bool, str, int]:
    """서버가 쓰는 패키지를 새 프로세스에서 실제로 불러 본다 → (성공?, 출력 또는 오류, 종료코드)."""
    r = subprocess.run([sys.executable, "-c", _SELFTEST], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_ROOT), env={**os.environ, "PYTHONUTF8": "1"})
    ok = r.returncode == 0
    return ok, ((r.stdout if ok else r.stderr) or "").strip(), r.returncode


def looks_like_dll_failure(returncode: int, text: str) -> bool:
    """VC++ 런타임이 없거나 낮을 때의 모습인가 — DLL 을 못 찾는 메시지이거나, 프로세스가 통째로 죽은 종료코드(0xC0000000 이상)."""
    low = (text or "").lower()
    return any(s in low for s in _DLL_SIGNS) or returncode >= 0xC0000000 or returncode < 0


def selftest_or_explain(vc_tried: bool) -> bool:
    ok, out, rc = run_selftest()
    if not ok and _is_windows() and not vc_tried and looks_like_dll_failure(rc, out):
        say("  불러오기 시험이 DLL 문제로 실패 → VC++ 런타임을 설치하고 다시 해 봅니다.")
        install_vc_redist()
        ok, out, rc = run_selftest()
    if ok:
        say("  불러오기 시험 통과: " + (out.splitlines() or [""])[-1])
        return True
    say(f"  ★[오류] 패키지를 불러오지 못했습니다(종료코드 {rc}). 마지막 줄들:")
    for ln in out.splitlines()[-12:]:
        say("      " + ln)
    if looks_like_dll_failure(rc, out):
        say(f"    Microsoft Visual C++ 재배포 패키지가 필요해 보입니다 — {VC_REDIST_URL} 를 받아 설치한 뒤 다시 실행하세요.")
    return False


def prepare_packages(check_only: bool = False, force_gpu: bool = False) -> bool:
    """패키지 단계 전체. 서버를 띄워도 되면 True."""
    st = load_state()
    pins = SE.requirement_pins()
    missing = missing_packages(pins)
    fresh = bool(missing) or st.get("setup") == "pending"
    gpu_due = force_gpu or (st.get("setup") == "done" and "gpu" not in st)      # 지난번 GPU용 내려받기 실패 → 다시
    if not fresh and not gpu_due:
        say(f"  패키지: 필요한 {len(pins)}개 모두 설치됨")
        return True
    if check_only:
        if missing:
            say(f"  ★패키지: {len(pins)}개 중 {len(missing)}개 없음 — " + ", ".join(missing))
        elif fresh:
            say("  ★패키지: 지난 설치가 끝까지 가지 못했습니다(다시 실행하면 이어서 합니다)")
        else:
            say(f"  패키지: 필요한 {len(pins)}개 모두 설치됨 (GPU용 torch 재시도 대기 중)")
        return not fresh
    if not in_venv():
        say("  ★[오류] 가상환경(.venv)의 파이썬이 아닙니다 — PC 전체 파이썬에는 설치하지 않습니다.")
        say('    "VIGENT Safety 시작.bat" 으로 실행하거나 `.venv\\Scripts\\python.exe scripts\\ensure_runtime.py` 로 실행하세요.')
        return False
    vc_tried = False
    if fresh:
        if missing:
            say(f"  패키지: {len(pins)}개 중 {len(missing)}개 없음 → 설치합니다(인터넷 필요 · 처음이면 수 GB · 수십 분 걸릴 수 있습니다).")
        else:
            say("  패키지: 지난 설치가 끝까지 가지 못했습니다 → 이어서 합니다.")
        warn_if_disk_low()
        st["setup"] = "pending"
        save_state(st)
        if not install_packages():
            return False
        vc_tried = ensure_vc_runtime()
    elif force_gpu:
        st.pop("gpu", None)
        st["gpu_tries"] = 0
    ensure_gpu_torch(st)
    save_state(st)
    if not selftest_or_explain(vc_tried):
        return False
    if fresh:
        st["setup"] = "done"
        save_state(st)
    return True


# ── 3) 모델·보조 파일 ───────────────────────────────────────────────────────────
def runtime_entries(man: dict[str, Any]) -> list[dict[str, Any]]:
    """실행에 쓰는 매니페스트 항목: 필수(required) + 보조 실행 파일·얼굴 가림 모델."""
    return [e for e in man.get("weights", []) if e.get("required") or e.get("slot") in RUNTIME_OPTIONAL_SLOTS]


def entry_state(entry: dict[str, Any]) -> str:
    """'ok' | 'missing' | 'size'(있지만 크기가 매니페스트와 다름). SHA 는 계산하지 않는다 — 매 실행마다 0.9 GB 를 읽지 않으려고."""
    p = FW.target_path(entry)
    if not p.is_file():
        return "missing"
    want = int(entry.get("size_bytes") or 0)
    if want and p.stat().st_size != want:
        return "size"
    return "ok"


@contextlib.contextmanager
def _env(**kv: str) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update(kv)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def ensure_weights(check_only: bool = False,
                   download: Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str]] | None = None) -> bool:
    """없는 모델·보조 파일만 받는다. **필수** 항목이 전부 있으면 True(선택 항목 실패는 경고만)."""
    download = download or FW.download
    man = FW.load_manifest()
    entries = runtime_entries(man)
    states = [(e, entry_state(e)) for e in entries]
    for e, s in states:
        if s == "size":
            p = FW.target_path(e)
            say(f"  ★주의: {e['file']} 의 크기({p.stat().st_size:,}B)가 목록({int(e['size_bytes']):,}B)과 다릅니다 — "
                "일부러 바꾼 파일일 수 있어 그대로 둡니다. 확인: `python scripts\\fetch_weights.py --check`")
    todo = [e for e, s in states if s == "missing"]
    if not todo:
        say(f"  모델·보조 파일: 필요한 {len(entries)}개 모두 있음")
        return True
    names = ", ".join(e["file"] for e in todo)
    if check_only:
        say(f"  ★모델·보조 파일: {len(entries)}개 중 {len(todo)}개 없음 — {names}")
        return not any(e.get("required") for e in todo)
    total_mb = sum(int(e.get("size_bytes") or 0) for e in todo) / 1024 ** 2
    say(f"  모델·보조 파일: {len(entries)}개 중 {len(todo)}개 없음 → 받습니다(풀었을 때 약 {total_mb:,.0f} MB): {names}")
    failed: list[tuple[dict[str, Any], str]] = []
    # 비공개 릴리스는 GitHub 토큰이 있어야 받는다. 저장된 로그인만 쓰고 **로그인 창은 띄우지 않는다**
    #   (fetch_weights.github_token 의 20초 제한 안에 로그인 창을 끝낼 수 없어, 띄우면 항목마다 창이 떴다 죽는다).
    with _env(GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPT="0"):
        tok = FW.github_token() if any(str(e.get("url", "")).startswith("release:") for e in todo) else ""
        with _env(**({"VIGENT_GITHUB_TOKEN": tok} if tok else {})):      # 항목마다 git 을 다시 부르지 않게 이 실행 동안만 기억
            for e in todo:
                ok, msg = download(e, man)
                if ok and entry_state(e) != "ok":                        # 받았다는데 파일이 없거나 크기가 다르면 실패다(규칙 11)
                    ok, msg = False, "받았다는데 파일이 없거나 크기가 다름"
                say(f"    [{'OK' if ok else '실패'}] {e['file']} — {msg}")
                if not ok:
                    failed.append((e, msg))
    req_fail = [e for e, _ in failed if e.get("required")]
    opt_fail = [e for e, _ in failed if not e.get("required")]
    if opt_fail:
        say("  ★선택 파일을 받지 못했습니다(서버는 뜨지만 기능이 줄어듭니다 — 확대뷰·웹캠 실시간 영상은 스냅샷으로, "
            "얼굴 가림은 사람 박스 안에서만): " + ", ".join(e["file"] for e in opt_fail))
    if any(str(e.get("url", "")).startswith("release:") for e, _ in failed):
        say("    ※ 이 파일들은 비공개 GitHub 릴리스에 있습니다. 이 PC 에 GitHub 로그인이 저장돼 있어야 받을 수 있습니다.")
        say("      방법: ① git 을 설치하고 이 저장소를 한 번 내려받아(로그인) 둔다  ② 환경변수 GITHUB_TOKEN 을 준다")
        say("            ③ 이미 되는 PC 의 vigent-core\\weights 폴더를 통째로 복사한다")
    if req_fail:
        say("  ★[오류] 필수 모델 파일이 없어 서버를 띄울 수 없습니다: " + ", ".join(e["file"] for e in req_fail))
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="실행에 필요한 것(패키지·VC++ 런타임·GPU용 torch·모델 파일)이 없으면 받는다")
    ap.add_argument("--check", action="store_true", help="받지 않고 무엇이 없는지만 본다")
    ap.add_argument("--gpu", action="store_true", help="torch 를 GPU용으로 바꾸기를 (다시) 시도한다")
    a = ap.parse_args(argv)
    say("[실행 준비] 필요한 것이 있는지 확인합니다" + (" (확인만 — 받지 않음)" if a.check else ""))
    ok_packages = prepare_packages(check_only=a.check, force_gpu=a.gpu and not a.check)
    ok_weights = ensure_weights(check_only=a.check)
    if ok_packages and ok_weights:
        say("[실행 준비] 완료 — 필요한 것이 모두 있습니다.")
        return 0
    say("[실행 준비] ★끝내지 못했습니다 — 위의 ★ 줄을 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
