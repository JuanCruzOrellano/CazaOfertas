@echo off
chcp 65001 >nul
title Publicar CazaOfertas en GitHub
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publicar.ps1" %*
echo.
pause
