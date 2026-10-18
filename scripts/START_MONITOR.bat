@echo off
chcp 65001 >nul 2>&1
setlocal
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
"%PY%" -u "%APP%\tools\monitor.py"
pause
