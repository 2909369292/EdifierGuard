@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 停止

for %%I in ("%~dp0..") do set "APP=%%~fI"
set "EXE=%APP%\EdifierGuard.exe"

rem ---- 1) 单文件 exe（首选）----
if exist "%EXE%" (
    echo 正在停止 Edifier Guard（并把音频端点恢复为「可用」）...
    echo.
    "%EXE%" --stop
    echo.
    pause
    exit /b 0
)

rem ---- 2) 源码方式（需要 Python）----
cd /d "%APP%"
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

echo 正在停止 Edifier Guard...
if exist "%APP%\watchdog.pid" (
    set /p WPID=<"%APP%\watchdog.pid"
    call :killone %WPID%
)
if exist "%APP%\guard.pid" (
    set /p GPID=<"%APP%\guard.pid"
    call :killone %GPID%
)
timeout /t 1 >nul

echo 正在恢复 HECATE 音频端点为「可用」...
"%PY%" "%APP%\guard.py" --restore
echo.
echo 已停止，设备已恢复「可用」。需要时双击 启动.vbs 即可再启动。
pause
exit /b 0

:killone
if "%~1"=="" exit /b 0
taskkill /PID %~1 /F >nul 2>&1
if errorlevel 1 (echo   PID %~1 未在运行) else (echo   已结束 PID %~1)
exit /b 0
