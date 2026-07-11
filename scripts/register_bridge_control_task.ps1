$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Launcher = Join-Path $Root "scripts\start_bridge_control.vbs"

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "//B //Nologo `"$Launcher`""
$Trigger = New-ScheduledTaskTrigger -AtLogon
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Hermes_SSP_Bridge_Control" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Force

Write-Host "Registered Hermes_SSP_Bridge_Control for current user logon."
