@echo off
chcp 65001 >nul
rem ════════════════════════════════════════════════════════════
rem  VIGENT Safety — 서버 종료 (바탕화면 더블클릭)
rem  "VIGENT Safety 시작.bat" 로 띄운 서버를 내린다.
rem  하는 일: 포트 8010 을 쥔 서버(python)를 트리째 종료 → 자식인 go2rtc·ffmpeg 도 함께 내려간다.
rem           혹시 go2rtc 가 고아로 남으면 백스톱으로 한 번 더 정리한다. 이미 꺼져 있으면 그대로 알린다.
rem ════════════════════════════════════════════════════════════
set PORT=8010
echo VIGENT 서버(포트 %PORT%) 종료 중...

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  set FOUND=1
  echo   서버 프로세스 PID %%a 종료 ^(go2rtc·ffmpeg 자식 포함^)
  taskkill /F /T /PID %%a >nul 2>&1
)

rem 백스톱: 위 트리 종료로 함께 내려가지만, 혹시 go2rtc 가 고아로 남았으면 그 트리(ffmpeg 자식 포함)를 정리.
tasklist /FI "IMAGENAME eq go2rtc.exe" 2>nul | findstr /I go2rtc.exe >nul && (
  echo   go2rtc 잔여 프로세스 정리
  taskkill /F /T /IM go2rtc.exe >nul 2>&1
)

if "%FOUND%"=="0" (
  echo   포트 %PORT% 에서 실행 중인 서버가 없습니다 ^(이미 꺼져 있음^).
) else (
  echo 완료 — 서버를 내렸습니다.
)
timeout /t 3 /nobreak >nul
