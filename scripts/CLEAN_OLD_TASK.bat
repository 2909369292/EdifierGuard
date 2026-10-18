@echo off
chcp 65001 >nul 2>&1
title Edifier Guard - Remove legacy scheduled task
setlocal

:: ---------- self-elevate to Administrator ----------
fltmc >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Requesting administrator privileges...
    echo   Please click "Yes" on the UAC prompt.
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================================
echo   Remove legacy scheduled task: EdifierGuard
echo   (it points to a folder that no longer exists)
echo ============================================================
echo.

schtasks /Query /TN "EdifierGuard" >nul 2>&1
if errorlevel 1 (
    echo   [SKIP] Task "EdifierGuard" does not exist. Nothing to do.
    goto :done
)

schtasks /Delete /TN "EdifierGuard" /F
if errorlevel 1 (
    echo.
    echo   [FAILED] Could not remove the task.
) else (
    echo.
    echo   [OK] Scheduled task "EdifierGuard" removed.
)

:done
echo.
echo   Note: the app now auto-starts from the Windows Startup folder
echo   (a single EdifierGuard.exe), not from a scheduled task.
echo   Removing this legacy task is safe.
echo   (EdifierGuard.exe --install already does this automatically.)
echo.
pause
