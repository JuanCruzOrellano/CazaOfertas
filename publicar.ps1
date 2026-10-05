# Sube esta carpeta a GitHub y publica la versión que dice version.py.
# GitHub arma el .exe solo y las apps instaladas ofrecen actualizarse.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = 'JuanCruzOrellano/CazaOfertas'
$Dir  = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- token (se pide una vez y queda guardado solo en esta compu)
$TokFile = Join-Path $env:APPDATA 'CazaOfertas\github_token.txt'
if ($args -contains '-nuevo-token' -and (Test-Path $TokFile)) { Remove-Item $TokFile }
if (Test-Path $TokFile) { $Token = (Get-Content $TokFile -Raw).Trim() }
else {
  $Token = (Read-Host 'Pega tu token de GitHub (empieza con github_pat_)').Trim()
  New-Item -ItemType Directory -Force (Split-Path $TokFile) | Out-Null
  Set-Content -Path $TokFile -Value $Token -NoNewline
}
$H = @{ Authorization = "Bearer $Token"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'CazaOfertas' }

function GH($Method, $Path, $Body) {
  $p = @{ Method = $Method; Uri = "https://api.github.com/repos/$Repo$Path"; Headers = $H }
  if ($Body) {
    $p.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 10 -Compress))
    $p.ContentType = 'application/json; charset=utf-8'
  }
  Invoke-RestMethod @p
}

# --- versión
$verLine = Get-Content (Join-Path $Dir 'version.py') | Where-Object { $_ -match 'APP_VERSION' } | Select-Object -First 1
if ($verLine -notmatch '"([0-9]+\.[0-9]+\.[0-9]+)"') { throw 'No encontre la version en version.py' }
$Ver = $Matches[1]; $Tag = "v$Ver"
Write-Host "Publicando la version $Ver..." -ForegroundColor Cyan

try { GH GET "/git/ref/tags/$Tag" | Out-Null; throw "La version $Ver ya esta publicada. Pedile a Claude que suba el numero de version." }
catch { if ($_.Exception.Message -like 'La version*') { throw } }

# --- rama main (si el repo está vacío, se crea con un primer archivo)
try { $Head = (GH GET '/git/ref/heads/main').object.sha }
catch {
  GH PUT '/contents/.gitkeep' @{ message = 'Inicio'; content = '' ; branch = 'main' } | Out-Null
  $Head = (GH GET '/git/ref/heads/main').object.sha
}

# --- archivos a subir
$Excluir = @('CazaOfertas.exe', 'app.ico', 'Crear EXE.bat', 'logo_toast.png')
$Archivos = Get-ChildItem -Path $Dir -Recurse -File -Force | Where-Object {
  $rel = $_.FullName.Substring($Dir.Length + 1)
  -not ($Excluir -contains $_.Name) -and $rel -notmatch '(^|\\)(__pycache__|build|dist|\.git)(\\|$)' -and $_.Extension -notin @('.pyc', '.spec', '.log')
}

$Tree = @()
foreach ($f in $Archivos) {
  $rel = $f.FullName.Substring($Dir.Length + 1).Replace('\', '/')
  $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($f.FullName))
  $blob = GH POST '/git/blobs' @{ content = $b64; encoding = 'base64' }
  $Tree += @{ path = $rel; mode = '100644'; type = 'blob'; sha = $blob.sha }
  Write-Host "  subido $rel"
}
$BaseTree = (GH GET "/git/commits/$Head").tree.sha   # conserva lo que ya esta en GitHub (ej. .github)
$t = GH POST '/git/trees' @{ base_tree = $BaseTree; tree = $Tree }
$c = GH POST '/git/commits' @{ message = "Version $Ver"; tree = $t.sha; parents = @($Head) }
GH PATCH '/git/refs/heads/main' @{ sha = $c.sha; force = $true } | Out-Null
GH POST '/git/refs' @{ ref = "refs/tags/$Tag"; sha = $c.sha } | Out-Null

Write-Host ''
Write-Host "Listo. GitHub esta armando el .exe de la version $Ver (tarda unos 5 minutos)." -ForegroundColor Green
Write-Host "Progreso:  https://github.com/$Repo/actions"
Write-Host "Descarga:  https://github.com/$Repo/releases/latest/download/CazaOfertas.exe"
