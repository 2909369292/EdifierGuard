@echo off
rem ============================================================
rem  内部辅助脚本（不要直接双击运行）
rem
rem  作用：探测「该用哪个 EdifierGuard 来执行命令」，结果放进 %EG_CMD%
rem
rem  优先级：
rem    1. 已在「启动」文件夹安装的副本  ← 首选，保证只有一份在跑
rem    2. 项目根目录下的单文件 EdifierGuard.exe
rem    3. 项目源码 + Python（开发用）
rem ============================================================

for %%I in ("%~dp0..") do set "APP=%%~fI"
set "STARTUP_EXE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\EdifierGuard.exe"
set "EG_CMD="
set "PY="

if exist "%STARTUP_EXE%" set "EG_CMD="%STARTUP_EXE%""
if not defined EG_CMD if exist "%APP%\EdifierGuard.exe" set "EG_CMD="%APP%\EdifierGuard.exe""

if not defined EG_CMD if exist "%APP%\runtime\python.exe" set "PY=%APP%\runtime\python.exe"
if not defined EG_CMD if not defined PY for /f "delims=" %%i in ('where python.exe 2^>nul') do if not defined PY set "PY=%%i"
if not defined EG_CMD if not defined PY for /f "delims=" %%i in ('where py.exe 2^>nul') do if not defined PY set "PY=%%i"

if not defined EG_CMD if defined PY set "EG_CMD="%PY%" "%APP%\main.py""
if defined PY set "PYTHONIOENCODING=utf-8"

exit /b 0
