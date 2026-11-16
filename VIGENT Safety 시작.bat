@echo off
chcp 65001 >nul
rem ════════════════════════════════════════════════════════════
rem  VIGENT Safety — 바탕화면 더블클릭 실행기 (Windows)
rem  mac 의 'VIGENT Safety 시작.command'(→ _archive/macos/) 대응. 감사 C4, 2026-09-06
rem  하는 일: 1. 서버가 떠 있으면 브라우저만 연다.
rem           2. 아니면 실행에 필요한 것이 있는지 보고 **없는 것만** 자동으로 받는다. [2026-11-16]
rem              scripts\ensure_runtime.ps1 - 파이썬 3.14, .venv, 패키지, VC++ 런타임, GPU용 torch, 모델 파일.
rem              다 있으면 아무것도 받지 않고 지나간다. 새 PC 의 첫 실행은 인터넷이 필요하고 수 GB 를 받는다.
rem           3. run.ps1 로 서버를 띄운 뒤 /health 200 을 기다려 연다.
rem  종료: 서버 창을 닫거나 Ctrl+C, 또는 "VIGENT Safety 종료.bat" 더블클릭
rem  ★괄호 블록 안에서는 echo·rem 에 괄호를 쓰지 않는다(deploy\portable\VIGENT_시작.bat 2026-09-23 실제 사고).
rem ════════════════════════════════════════════════════════════
set PORT=8010
set HEALTH=http://127.0.0.1:%PORT%/health
set URL=http://127.0.0.1:%PORT%/safety-hub
cd /d "%~dp0"

powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%HEALTH%'; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  echo 서버가 이미 동작 중 - 페이지를 엽니다.
  start "" "%URL%"
  exit /b 0
)

rem ---- 실행에 필요한 것 확인: 없는 것만 받는다. 실패하면 서버를 띄우지 않는다 ----
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ensure_runtime.ps1"
if errorlevel 1 goto prep_fail

echo 서버 시작 중 (모델 로드 포함 최대 90초)...
start "VIGENT 서버" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
for /l %%i in (1,1,90) do (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 '%HEALTH%'; exit 0 } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto up
  timeout /t 1 /nobreak >nul
)
echo [경고] 90초 안에 /health 가 200 을 주지 않았습니다. 서버 창의 오류를 확인하세요.
pause
exit /b 1

:prep_fail
echo.
echo [오류] 실행 준비를 끝내지 못해 서버를 띄우지 않았습니다. 위의 [오류]·★ 줄을 확인한 뒤 다시 실행하세요.
echo        인터넷이 끊겼던 것이라면 다시 실행하면 이어서 받습니다.
pause
exit /b 1

:up
start "" "%URL%"
echo ------------------------------------------------
echo  관제 화면:  %URL%   (로컬판·CDN 불필요)
echo  서버 창을 닫으면 서버가 종료됩니다.
echo ------------------------------------------------
