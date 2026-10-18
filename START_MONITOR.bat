@echo off
chcp 65001 >nul 2>&1
setlocal
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
"%PY%" -u "%~dp0monitor.py"
pause
