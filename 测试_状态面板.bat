@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 状态面板
set "PY="
if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"
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
"%PY%" "%~dp0guard.py" --status
echo.
echo -------------------- 最近 6 条日志 --------------------
powershell -NoProfile -Command "Get-Content -LiteralPath '%~dp0guard.log' -Tail 6 -Encoding UTF8"
echo.
echo 每 2 秒自动刷新，Ctrl+C 退出
timeout /t 2 >nul
goto loop
