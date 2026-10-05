@echo off
setlocal
title Instalar CazaOfertas
rem Instala (o actualiza) CazaOfertas para este usuario:
rem  - Python oficial si no esta (firmado, los antivirus lo conocen)
rem  - los archivos de la app en %LOCALAPPDATA%\Programs\CazaOfertas
rem  - el icono en el Escritorio y en el menu Inicio
set "APPDIR=%LOCALAPPDATA%\Programs\CazaOfertas"
set "ZIPURL=https://github.com/JuanCruzOrellano/CazaOfertas/releases/latest/download/CazaOfertas-app.zip"
set "ZIPLOCAL=%~1"
echo.
echo   === Instalando CazaOfertas ===
echo.

call :buscar_python
if not defined PYW (
  echo   [1/4] Instalando Python ^(solo la primera vez, puede tardar un par de minutos^)...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
  call :buscar_python
)
if not defined PYW (
  echo   [1/4] Descargando Python desde python.org...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile (Join-Path $env:TEMP 'python-instalador.exe') -UseBasicParsing"
  "%TEMP%\python-instalador.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
  call :buscar_python
)
if not defined PYW (
  echo.
  echo   No se pudo instalar Python. Instalalo desde https://www.python.org/downloads/
  echo   y volve a abrir este archivo.
  pause
  exit /b 1
)
echo   [1/4] Python listo.

echo   [2/4] Descargando la app...
taskkill /im CazaOfertas.exe /f >nul 2>nul
if not exist "%APPDIR%" mkdir "%APPDIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $d='%APPDIR%'; $z='%ZIPLOCAL%'; if (-not $z) { $z = Join-Path $env:TEMP 'cazaofertas-app.zip'; Invoke-WebRequest '%ZIPURL%' -OutFile $z -UseBasicParsing }; foreach ($v in @('_internal','CazaOfertas.exe','unins000.exe','unins000.dat','version.txt','accesos.txt')) { $p = Join-Path $d $v; if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue } }; Expand-Archive -Path $z -DestinationPath $d -Force"
if errorlevel 1 (
  echo.
  echo   No se pudo descargar la app. Revisa tu conexion a internet y volve a intentar.
  pause
  exit /b 1
)

echo   [3/4] Instalando componentes...
"%PY%" -m pip install --user --upgrade --disable-pip-version-check -q -r "%APPDIR%\requirements.txt"
echo instalado> "%APPDIR%\.instalado"

echo   [4/4] Creando el icono en el Escritorio...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { try { $s = $w.CreateShortcut((Join-Path $d 'CazaOfertas.lnk')); $s.TargetPath = '%PYW%'; $s.Arguments = '\"%APPDIR%\app.py\"'; $s.WorkingDirectory = '%APPDIR%'; $s.IconLocation = '%APPDIR%\app.ico,0'; $s.Description = 'CazaOfertas'; $s.Save() } catch {} }"

echo.
echo   Listo. CazaOfertas quedo instalada y tiene su icono en el Escritorio.
echo   Abriendo...
start "" "%PYW%" "%APPDIR%\app.py"
timeout /t 4 >nul
exit /b 0

:buscar_python
set "PY="
set "PYW="
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313" "%LOCALAPPDATA%\Programs\Python\Python312" "%LOCALAPPDATA%\Programs\Python\Python311" "%ProgramFiles%\Python313" "%ProgramFiles%\Python312" "%ProgramFiles%\Python311") do (
  if not defined PY if exist "%%~P\pythonw.exe" (
    set "PY=%%~P\python.exe"
    set "PYW=%%~P\pythonw.exe"
  )
)
if not defined PY (
  for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if defined PY if not defined PYW set "PYW=%PY:python.exe=pythonw.exe%"
if defined PYW if not exist "%PYW%" set "PYW="
exit /b
