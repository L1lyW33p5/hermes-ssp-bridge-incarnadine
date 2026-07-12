@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "ROOT=%~dp0"
set "LAUNCHER=%ROOT%scripts\start_bridge_control.vbs"
set "CONTROL_SERVICE_SCRIPT=%ROOT%bridge_control\control_service.py"
set "PID_FILE=%ROOT%bridge_workspace\control_service.pid"
set "CONTROL_PORT=1313"

:menu
cls
call :is_running
call :render_header
echo.
echo [1] Start service    [2] Stop service
echo [0] Exit
echo.
:menu_prompt
set "CHOICE="
set /p "CHOICE=Select: "
if not defined CHOICE goto menu_prompt

if "%CHOICE%"=="1" goto start_service
if "%CHOICE%"=="2" goto stop_service
if "%CHOICE%"=="0" exit /b 0
goto menu

:render_header
powershell.exe -NoLogo -NoProfile -Command "$w=41; $h=[char]0x2500; $v=[char]0x2502; $tl=[char]0x250C; $tr=[char]0x2510; $bl=[char]0x2514; $br=[char]0x2518; $mark=if ('%RUNNING%' -eq '1') {[char]0x2705} else {[char]0x274C}; $line=''.PadRight($w,$h); $title='  Hermes SSP Bridge Control Service'.PadRight($w); $status=('  Status '+$mark).PadRight($w-1); Write-Host ($tl+$line+$tr); Write-Host ($v+$title+$v); Write-Host ($v+$status+$v); Write-Host ($bl+$line+$br)"
exit /b 0

:start_service
echo.
call :is_running
if "!RUNNING!"=="1" (
    echo Control service is already running.
) else (
    wscript.exe "%LAUNCHER%"
    timeout /t 3 /nobreak >nul
    call :is_running
    if "!RUNNING!"=="1" (
        echo Control service started: http://127.0.0.1:%CONTROL_PORT%
    ) else (
        echo Start command ran, but the control service was not detected.
    )
)
pause
goto menu

:stop_service
echo.
call :is_running
if "!RUNNING!"=="0" (
    echo Control service is not running.
) else (
    echo Stopping control service PID !SERVICE_PID!...
    taskkill /PID !SERVICE_PID! /T /F
    timeout /t 1 /nobreak >nul
    call :is_running
    if "!RUNNING!"=="0" (
        echo Control service stopped.
    ) else (
        echo Stop command ran, but the control service still appears to be running.
    )
)
pause
goto menu

:is_running
set "RUNNING=0"
set "SERVICE_PID="
if exist "%PID_FILE%" set /p "SERVICE_PID="<"%PID_FILE%"
if not defined SERVICE_PID (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:%CONTROL_PORT% .*LISTENING"') do (
        for /f %%R in ('powershell.exe -NoLogo -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%%P' -ErrorAction SilentlyContinue; $expected=[regex]::Escape($env:CONTROL_SERVICE_SCRIPT); if($p -and $p.CommandLine -match $expected){ 'yes' }"') do if /i "%%R"=="yes" set "SERVICE_PID=%%P"
    )
)
if not defined SERVICE_PID exit /b 0
set "PORT_MATCH=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:%CONTROL_PORT% .*LISTENING"') do if "%%P"=="!SERVICE_PID!" set "PORT_MATCH=1"
set "PROCESS_MATCH="
for /f %%R in ('powershell.exe -NoLogo -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=!SERVICE_PID!' -ErrorAction SilentlyContinue; $expected=[regex]::Escape($env:CONTROL_SERVICE_SCRIPT); if($p -and $p.CommandLine -match $expected){ 'yes' }"') do set "PROCESS_MATCH=%%R"
if "!PORT_MATCH!"=="1" (
    if /i "!PROCESS_MATCH!"=="yes" (
        set "RUNNING=1"
        if not exist "%PID_FILE%" >"%PID_FILE%" echo !SERVICE_PID!
        exit /b 0
    )
)
set "SERVICE_PID="
del /q "%PID_FILE%" >nul 2>&1
exit /b 0
