@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 实时日志
echo ============================================================
echo   Edifier Guard v2.0  实时日志
echo ------------------------------------------------------------
echo   测试步骤：
echo     1. 把耳机从底座拿起戴上  -^> 应出现「切换 -^> 可用」
echo     2. 放回底座              -^> 约 3 秒后「切换 -^> 隐藏」
echo     3. 中途看 Windows 声音设置，设备是否随进随出
echo ------------------------------------------------------------
echo   本窗口只读日志，不改任何设备。Ctrl+C 退出。
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%~dp0guard.log' -Wait -Tail 25 -Encoding UTF8"
pause
