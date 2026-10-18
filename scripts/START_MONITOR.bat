@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 只观察模式

for %%I in ("%~dp0..") do set "APP=%%~fI"
set "EXE=%APP%\EdifierGuard.exe"

rem ---- 1) 单文件 exe（首选）----
if exist "%EXE%" (
    echo 只观察、不改设备（Ctrl+C 退出）...
    echo.
    "%EXE%" --monitor
    echo.
    pause
    exit /b 0
)

rem ---- 2) 源码方式（需要 Python）----
set "PY="
if exist "%APP%\runtime\python.exe" set "PY=%APP%\runtime\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
    echo [ERROR] 既没有 EdifierGuard.exe，也没有 Python。
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
"%PY%" -u "%APP%\tools\monitor.py"
pause
