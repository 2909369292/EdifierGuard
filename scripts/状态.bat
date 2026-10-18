@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 状态面板

call "%~dp0_env.bat"
if not defined EG_CMD (
    echo [错误] 找不到 EdifierGuard.exe，也没找到 Python。
    pause
    exit /b 1
)

echo ============================================================
echo   Edifier Guard 状态面板
echo ------------------------------------------------------------
echo   拿起耳机  -^> 设备变为「可用」，声音设置里能看到
echo   放回底座  -^> 设备被隐藏，从声音设置里消失（正常现象）
echo ------------------------------------------------------------
echo   本窗口只读状态，不会改任何设备（Ctrl+C 退出）
echo ============================================================
echo.

:loop
%EG_CMD% --status
echo.
echo 每 3 秒自动刷新，Ctrl+C 退出
timeout /t 3 >nul
cls
echo ============================================================
echo   Edifier Guard 状态面板（Ctrl+C 退出）
echo ============================================================
echo.
goto loop
