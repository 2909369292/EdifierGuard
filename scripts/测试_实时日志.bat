@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 实时日志
echo ============================================================
echo   Edifier Guard 实时日志
echo ------------------------------------------------------------
echo   测试步骤：
echo     1. 把耳机连到接收器上  -^> 应用里应出现「切换 -^> 启用」
echo     2. 关掉耳机            -^> 约 3 秒后切换 -^> 隐藏
echo     3. 观察中途 Windows 音频设置里设备是否还在
echo ------------------------------------------------------------
echo   本窗口只读日志，不会改任何设备（Ctrl+C 退出）
echo ============================================================
echo.

for %%I in ("%~dp0..") do set "APP=%%~fI"
if exist "%APP%\EdifierGuard.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%LOCALAPPDATA%\EdifierGuard\guard.log' -Wait -Tail 25 -Encoding UTF8"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%APP%\guard.log' -Wait -Tail 25 -Encoding UTF8"
)
pause
