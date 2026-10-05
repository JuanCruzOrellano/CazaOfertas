@echo off
chcp 65001 >nul
title Instalar CazaOfertas
cd /d "%~dp0"
echo.
echo  === CazaOfertas ===
echo.
where pyw >nul 2>nul
if errorlevel 1 (
  echo  No encontre Python en esta computadora. Lo instalo ahora...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo  No se pudo instalar automaticamente.
    echo  Instala Python desde https://www.python.org/downloads/  ^(marca "Add python.exe to PATH"^)
    echo  y despues volve a abrir este archivo.
    pause
    exit /b 1
  )
  echo.
  echo  Python instalado. Cerra esta ventana y volve a abrir Instalar.bat
  pause
  exit /b 0
)
echo  Python OK.
echo  Instalando componentes...
py -3 -m pip install --user --upgrade --disable-pip-version-check -q curl_cffi
if errorlevel 1 echo  [Aviso] No se pudo instalar curl_cffi. Revisa tu conexion a internet.
cscript //nologo "%~dp0crear_acceso.vbs"
echo  Acceso directo creado en el Escritorio.
echo.
echo  Abriendo CazaOfertas...
wscript.exe "%~dp0Abrir CazaOfertas.vbs"
timeout /t 3 >nul
