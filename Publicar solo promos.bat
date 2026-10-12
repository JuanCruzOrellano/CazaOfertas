@echo off
chcp 65001 >nul
title Publicar solo promos
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publicar_promos.ps1" %*
echo.
pause
