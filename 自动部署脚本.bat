@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Hermes SSP Bridge Auto Deploy

set "DEPLOY_PS1=%~dp0scripts\auto_deploy.ps1"
if not exist "%DEPLOY_PS1%" (
    echo [ERROR] Deployment core not found: "%DEPLOY_PS1%"
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%DEPLOY_PS1%"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Deployment script exited with code: %EXIT_CODE%
    pause
)
exit /b %EXIT_CODE%
