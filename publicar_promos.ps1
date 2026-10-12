# Sube SOLO la lista de promos y eventos (web\promos.json) a GitHub.
# No crea una version nueva: las apps instaladas toman las promos nuevas solas,
# sin aviso de actualizacion.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = 'JuanCruzOrellano/CazaOfertas'
$Dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Archivo = Join-Path $Dir 'web\promos.json'

# --- token (el mismo que usa "Publicar en GitHub")
$TokFile = Join-Path $env:APPDATA 'CazaOfertas\github_token.txt'
if (Test-Path $TokFile) { $Token = (Get-Content $TokFile -Raw).Trim() }
else {
  $Token = (Read-Host 'Pega tu token de GitHub (empieza con github_pat_)').Trim()
  New-Item -ItemType Directory -Force (Split-Path $TokFile) | Out-Null
  Set-Content -Path $TokFile -Value $Token -NoNewline
}
$H = @{ Authorization = "Bearer $Token"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'CazaOfertas' }

# --- revisar que el archivo este bien antes de subirlo
if (-not (Test-Path $Archivo)) { throw 'No encontre web\promos.json en esta carpeta.' }
$Texto = [IO.File]::ReadAllText($Archivo, [Text.Encoding]::UTF8)
try { $Datos = $Texto | ConvertFrom-Json } catch { throw 'El archivo de promos tiene un error y no se puede leer. Pedile a Claude que lo revise.' }
$Cant = @($Datos.promos).Count
Write-Host ""
Write-Host "  Promos a publicar: $Cant (relevadas el $($Datos.actualizado))" -ForegroundColor Cyan

# --- subir (reemplaza el que esta en GitHub)
$Url = "https://api.github.com/repos/$Repo/contents/web/promos.json"
$Sha = $null
try { $Sha = (Invoke-RestMethod -Uri "$Url`?ref=main" -Headers $H).sha } catch {}
$Body = @{ message = "Promos actualizadas ($($Datos.actualizado))"; branch = 'main'
           content = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Texto)) }
if ($Sha) { $Body.sha = $Sha }
Invoke-RestMethod -Method Put -Uri $Url -Headers $H -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Compress))) | Out-Null

Write-Host ""
Write-Host "  Listo. Las promos nuevas les llegan a todos en las proximas horas," -ForegroundColor Green
Write-Host "  sin aviso de actualizacion y sin instalar nada." -ForegroundColor Green
