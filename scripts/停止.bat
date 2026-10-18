@echo off
chcp 65001 >nul 2>&1
setlocal
title Edifier Guard - 停止

call "%~dp0_env.bat"
if not defined EG_CMD (
    echo [错误] 找不到 EdifierGuard.exe，也没找到 Python。
    pause
    exit /b 1
)

echo 正在停止 Edifier Guard（并把音频端点恢复为「可用」）...
echo.
%EG_CMD% --stop
echo.
echo 已停止。需要时双击 启动.vbs 即可再启动。
pause
exit /b 0
