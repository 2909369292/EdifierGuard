@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 状态面板

for %%I in ("%~dp0..") do set "APP=%%~fI"
set "EXE=%APP%\EdifierGuard.exe"
set "DATA=%APP%"
if exist "%EXE%" set "DATA=%LOCALAPPDATA%\EdifierGuard"

rem ---- 源码方式才用得到的 Python 探测 ----
set "PY="
if exist "%APP%\runtime\python.exe" set "PY=%APP%\runtime\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%i"
set PYTHONIOENCODING=utf-8

:loop
cls
if exist "%EXE%" (
    "%EXE%" --status
) else (
    if defined PY (
        "%PY%" "%APP%\guard.py" --status
    ) else (
        echo [ERROR] 既没有 EdifierGuard.exe，也没有 Python。
        pause
        exit /b 1
    )
)
echo.
echo -------------------- 最近 6 条日志 --------------------
powershell -NoProfile -Command "Get-Content -LiteralPath '%DATA%\guard.log' -Tail 6 -Encoding UTF8" 2>nul
echo.
echo 每 2 秒自动刷新，Ctrl+C 退出
timeout /t 2 >nul
goto loop
