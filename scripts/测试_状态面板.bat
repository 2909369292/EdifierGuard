@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 状态面板
for %%I in ("%~dp0..") do set "APP=%%~fI"
if exist "%LOCALAPPDATA%\EdifierGuard\guard.py" set "APP=%LOCALAPPDATA%\EdifierGuard"
set "PY="
if exist "%APP%\runtime\python.exe" set "PY=%APP%\runtime\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
:loop
cls
"%PY%" "%APP%\guard.py" --status
echo.
echo -------------------- 最近 6 条日志 --------------------
powershell -NoProfile -Command "Get-Content -LiteralPath '%APP%\guard.log' -Tail 6 -Encoding UTF8"
echo.
echo 每 2 秒自动刷新，Ctrl+C 退出
timeout /t 2 >nul
goto loop
