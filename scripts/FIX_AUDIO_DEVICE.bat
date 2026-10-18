@echo off
chcp 65001 >nul 2>&1
title HECATE G3 Max - Enable USB Audio Interface

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
echo   HECATE G3 Max  -  Enable USB Audio Interface
echo   Target: USB\VID_35BB^&PID_A22B^&MI_00
echo ============================================================
echo.

set "DEVID=USB\VID_35BB&PID_A22B&MI_00\7&3B0EF022&0&0000"

echo [1/4] Current device status:
powershell -NoProfile -Command "Get-PnpDevice -InstanceId '%DEVID%' | Select-Object Status,Class,FriendlyName | Format-List"
echo.

echo [2/4] Enabling the audio interface...
powershell -NoProfile -Command "$id='%DEVID%'; try { Enable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction Stop; Write-Host '  Enable OK' } catch { Write-Host ('  Enable returned: ' + $_.Exception.Message) }"
echo.

echo [3/4] Restarting Windows Audio service to rebuild audio endpoints...
net stop Audiosrv /y >nul 2>&1
timeout /t 2 /nobreak >nul
net start Audiosrv >nul 2>&1
timeout /t 3 /nobreak >nul
echo.

echo [4/4] Result:
powershell -NoProfile -Command "Get-PnpDevice -InstanceId '%DEVID%' | Select-Object Status,Class,FriendlyName | Format-List"
echo.

echo ============================================================
echo   Expected:  Status = OK
echo   If it still shows Error, run this file once more.
echo ============================================================
echo.
echo Now open Sound Settings and select "HECATE G3 Max".
echo Volume control should also work again.
echo.
pause
