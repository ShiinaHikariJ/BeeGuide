param([switch]$Setup, [switch]$Build, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataDirectory = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    $Setup = $true
}
if ($Setup) {
    & $pythonPath -m pip install -r (Join-Path $projectRoot 'backend\requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if ($Build -or -not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) {
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) { npm ci; if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed.' } }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8008/api/health' -TimeoutSec 2
    if ($health.status -ne 'ok' -or -not $health.model_mode) { throw 'Port occupied' }
    Write-Host 'BeeGuide is already running: http://127.0.0.1:8008'
} catch {
    $listener = Get-NetTCPConnection -LocalPort 8008 -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw 'Port 8008 is occupied by another service. Stop that service or use the development setup.' }
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('-m','app.serve') -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataDirectory 'server.out.log') -RedirectStandardError (Join-Path $dataDirectory 'server.err.log')
    Set-Content -LiteralPath (Join-Path $dataDirectory 'server.pid') -Value $process.Id
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8008/api/health' -TimeoutSec 1; if ($health.status -eq 'ok') { $ready = $true; break } } catch {}
        if ($process.HasExited) { break }
    }
    if (-not $ready) { throw 'Startup failed. See data/server.err.log.' }
    Write-Host 'BeeGuide started: http://127.0.0.1:8008'
}
if ($OpenBrowser) { Start-Process 'http://127.0.0.1:8008' }
