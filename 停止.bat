@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"
title Edifier Guard - 停止
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

echo 正在停止 Edifier Guard...
if exist "%~dp0watchdog.pid" (
    set /p WPID=<"%~dp0watchdog.pid"
    call :killone %WPID%
)
if exist "%~dp0guard.pid" (
    set /p GPID=<"%~dp0guard.pid"
    call :killone %GPID%
)
timeout /t 1 >nul

echo 正在恢复耳机音频端点为可用...
"%PY%" "%~dp0guard.py" --restore
echo.
echo 已停止。设备已恢复可用，开机不会自动运行。
pause
exit /b 0

:killone
if "%~1"=="" exit /b 0
taskkill /PID %~1 /F >nul 2>&1
if errorlevel 1 (echo   PID %~1 未在运行) else (echo   已结束 PID %~1)
exit /b 0
