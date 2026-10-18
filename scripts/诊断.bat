@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 体检

call "%~dp0_env.bat"
if not defined EG_CMD (
    echo [错误] 找不到 EdifierGuard.exe，也没找到 Python。
    pause
    exit /b 1
)

echo 正在体检（只读，不改任何设备）...
echo.
%EG_CMD% --doctor
echo.
pause
exit /b 0
