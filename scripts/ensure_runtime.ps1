# scripts/ensure_runtime.ps1 — "VIGENT Safety 시작.bat" 이 서버를 띄우기 전에 부르는 실행 준비기. [2026-11-16]
#   없는 것만 받는다. 다 있으면 아무것도 받지 않고 지나간다.
#     1) 파이썬(.python-version 의 버전): .venv 에 있으면 그것 → 없으면 PC 의 `py -3.14` → 그것도 없으면
#        프로젝트 폴더 안 전용 사본(.python\)을 받는다. 전용 사본 = python.org 가 nuget.org 에 올리는 공식 배포본으로,
#        **폴더에 풀기만 하고 PC 에 설치하지 않는다**(레지스트리·PATH·시작 메뉴를 건드리지 않는다. 지우려면 .python 폴더만 지우면 된다).
#        받은 뒤 python.exe 의 디지털 서명(Python Software Foundation)을 확인한다.
#     2) .venv 가 없으면 만든다.
#     3) 나머지(패키지 · VC++ 런타임 · GPU용 torch · 모델 파일)는 scripts\ensure_runtime.py 가 맡는다.
#   종료코드: 0 = 서버를 띄워도 된다 / 1 = 준비 실패(화면의 [오류]·★ 줄 참조)
#   옵션: -PythonOnly  → 1)·2) 만 하고 끝낸다(시험용).   그 밖의 인자(--check, --gpu)는 ensure_runtime.py 로 그대로 넘긴다.
#   시험용 환경변수: VIGENT_BOOTSTRAP_NO_SYSTEM_PYTHON=1 → PC 의 `py` 를 없는 것으로 친다(전용 사본 경로를 타 보려고).
#   ★이 파일은 UTF-8 BOM + CRLF 로 저장한다 — PowerShell 5.1 은 BOM 이 없으면 한글을 ANSI 로 읽어 구문 오류를 낸다.
param([switch]$PythonOnly)
$ErrorActionPreference = "Stop"
$PassArgs = $args
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$env:PYTHONUTF8 = "1"

$Root     = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$VenvDir  = Join-Path $Root ".venv"
$VenvPy   = Join-Path $VenvDir "Scripts\python.exe"
$LocalDir = Join-Path $Root ".python"
$LocalPy  = Join-Path $LocalDir "tools\python.exe"

function Fail([string]$msg) {
    Write-Host "[오류] $msg" -ForegroundColor Red
    exit 1
}

# ── 쓸 파이썬 버전: .python-version 한 곳에서만 읽는다(run.ps1·setup_env.py 가 요구하는 정본과 같은 값) ──
$verFile = Join-Path $Root ".python-version"
if (-not (Test-Path $verFile)) { Fail ".python-version 파일이 없습니다 - 폴더가 온전히 복사되지 않았습니다: $Root" }
$PyVer = (Get-Content $verFile -TotalCount 1).Trim()
if ($PyVer -notmatch '^(\d+)\.(\d+)\.\d+$') { Fail ".python-version 의 값 '$PyVer' 을 버전으로 읽을 수 없습니다(예: 3.14.5)" }
$PyMajor = [int]$Matches[1]
$PyMinor = [int]$Matches[2]
$PyMM = "$PyMajor.$PyMinor"

function Test-Python([string]$exe, [string]$pre) {
    # 그 파이썬이 실제로 실행되고 버전(major.minor)이 맞는가. 숨은 창에서 돌리고 종료코드만 본다(run.ps1 과 같은 방식).
    $code = 'import sys; sys.exit(0 if sys.version_info[:2] == ({0}, {1}) else 3)' -f $PyMajor, $PyMinor
    $argLine = ($pre + ' -c "' + $code + '"').Trim()
    try {
        $p = Start-Process -FilePath $exe -ArgumentList $argLine -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop
        return ($p.ExitCode -eq 0)
    } catch { return $false }
}

function Invoke-Native([string]$exe, [string[]]$argv) {
    # 성패는 호출한 쪽이 $LASTEXITCODE 로 본다. 값을 return 하지 않는다 - return 하면 프로그램 출력이 화면 대신 변수로 들어간다.
    # ★PS 5.1 은 $ErrorActionPreference=Stop 에서 네이티브 stderr 한 줄도 예외로 올릴 수 있다(build_portable.ps1 2026-09-23 실제 사고).
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @argv } finally { $ErrorActionPreference = $prev }
}

function Install-LocalPython {
    $url   = "https://api.nuget.org/v3-flatcontainer/python/$PyVer/python.$PyVer.nupkg"
    $zip   = Join-Path ([IO.Path]::GetTempPath()) "vigent_python_$PyVer.nupkg"
    $stage = "$LocalDir.staging"
    Write-Host "  이 PC 에 파이썬($PyMM)이 없어 전용 사본을 받습니다(공식 배포본 $PyVer)."
    Write-Host "  이 폴더의 .python\ 안에만 풀고, PC 의 다른 설정은 건드리지 않습니다."
    Write-Host "    ↓ $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $wc = New-Object Net.WebClient
    try { $wc.DownloadFile($url, $zip) } finally { $wc.Dispose() }

    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }      # 지난번에 풀다 만 임시 폴더
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [IO.Compression.ZipFile]::OpenRead($zip)
    $n = 0
    try {
        foreach ($e in $z.Entries) {
            if (-not $e.Name) { continue }                                        # 폴더 항목
            $rel = [Uri]::UnescapeDataString($e.FullName).Replace("/", "\")       # nupkg 는 파일 이름의 공백 등을 %XX 로 적는다
            if (-not $rel.StartsWith("tools\")) { continue }                      # 파이썬 본체는 tools\ 아래 - 나머지는 nuget 메타데이터
            if ($rel.Contains("..")) { continue }
            $dst = Join-Path $stage $rel
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $dst))
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dst, $true)
            $n++
        }
    } finally { $z.Dispose() }

    $exe = Join-Path $stage "tools\python.exe"
    if (-not (Test-Path $exe)) { throw "받은 파일 안에 tools\python.exe 가 없습니다(풀린 파일 $n 개)" }
    $sig = Get-AuthenticodeSignature -FilePath $exe
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notlike "*Python Software Foundation*") {
        Remove-Item $stage -Recurse -Force
        throw "python.exe 의 디지털 서명을 확인하지 못했습니다(상태: $($sig.Status)) - 받은 파일을 지웠습니다"
    }
    # 깨진 .python 이 남아 있으면 지우지 않고 이름을 바꿔 둔다
    if (Test-Path $LocalDir) { Rename-Item $LocalDir (".python.broken-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
    Move-Item $stage $LocalDir
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Write-Host "  파이썬($PyVer) 준비됨: $LocalPy  (파일 $n 개 · 서명 확인: Python Software Foundation)"
}

function Find-BasePython {
    # 결과는 $script:BaseExe / $script:BasePre 에 둔다(함수 반환값에 다른 출력이 섞이지 않게).
    $script:BaseExe = $null
    $script:BasePre = @()
    if ($env:VIGENT_BOOTSTRAP_NO_SYSTEM_PYTHON -ne "1") {
        if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python "py" "-$PyMM")) {
            $script:BaseExe = "py"
            $script:BasePre = @("-$PyMM")
            Write-Host "  PC 에 설치된 파이썬($PyMM)을 씁니다: py -$PyMM"
            return
        }
    }
    if (-not ((Test-Path $LocalPy) -and (Test-Python $LocalPy ""))) {
        try { Install-LocalPython } catch {
            Fail ("파이썬($PyVer)을 받지 못했습니다: " + $_.Exception.Message + "`n       인터넷 연결을 확인한 뒤 다시 실행하세요. (직접 설치해도 됩니다: python.org 의 Python $PyMM)")
        }
        if (-not (Test-Python $LocalPy "")) { Fail "받은 파이썬이 실행되지 않습니다: $LocalPy" }
    } else {
        Write-Host "  이 폴더의 전용 파이썬을 씁니다: $LocalPy"
    }
    $script:BaseExe = $LocalPy
}

# ── 1)·2) 파이썬과 .venv ──
if (Test-Path $VenvPy) {
    if (-not (Test-Python $VenvPy "")) {
        Fail (".venv 가 있지만 파이썬($PyMM)으로 실행되지 않습니다(다른 버전으로 만들었거나, 만들 때 쓴 파이썬이 지워졌거나, 다른 PC 에서 복사해 온 경우).`n       .venv 폴더의 이름을 바꾸거나 지운 뒤 다시 실행하면 새로 만듭니다: $VenvDir")
    }
} else {
    Write-Host "[실행 준비] 가상환경(.venv)이 없어 새로 만듭니다."
    Find-BasePython
    Write-Host "  가상환경을 만드는 중: $VenvDir"
    Invoke-Native $script:BaseExe ($script:BasePre + @("-m", "venv", $VenvDir))
    if ($LASTEXITCODE -ne 0) { Fail "가상환경을 만들지 못했습니다(종료코드 $LASTEXITCODE)." }
    if (-not (Test-Python $VenvPy "")) { Fail "가상환경을 만들었지만 그 안의 파이썬이 실행되지 않습니다: $VenvPy" }
    Write-Host "  가상환경 준비됨 - 파이썬 $PyMM 실행 확인."
}
if ($PythonOnly) {
    Write-Host "[실행 준비] 파이썬·가상환경 확인 끝(-PythonOnly): $VenvPy"
    exit 0
}

# ── 3) 패키지 · VC++ 런타임 · GPU용 torch · 모델 파일 ──
Invoke-Native $VenvPy (@((Join-Path $Root "scripts\ensure_runtime.py")) + $PassArgs)
exit $LASTEXITCODE
