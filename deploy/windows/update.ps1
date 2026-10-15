<#
.SYNOPSIS
  Update, restart or stop an OperAItor server installed with install.ps1. Run on the hosting machine in an
  administrator PowerShell:
      powershell -ExecutionPolicy Bypass -File C:\OperAItor\update.ps1            # update to the latest commit
      powershell -ExecutionPolicy Bypass -File C:\OperAItor\update.ps1 -Restart   # restart (e.g. after editing settings.ps1)
      powershell -ExecutionPolicy Bypass -File C:\OperAItor\update.ps1 -Stop      # stop until the next boot or -Restart
#>
param([switch]$Restart, [switch]$Stop)
$ErrorActionPreference = "Stop"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = 7860
. (Join-Path $InstallDir "settings.ps1")
$App = Join-Path $InstallDir "app"
$Py = Join-Path $InstallDir "venv\Scripts\python.exe"

function Stop-Server {
    # The task runs a PowerShell loop that restarts the server; stop the loop first, then the server.
    Stop-ScheduledTask -TaskName "OperAItor" -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process |
        Where-Object { ($_.Name -eq "powershell.exe" -and $_.CommandLine -like "*$InstallDir\start.ps1*") -or
                       ($_.Name -eq "python.exe" -and $_.CommandLine -like "*operaitor serve*--port $Port*") } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

function Start-Server($label) {
    Start-ScheduledTask -TaskName "OperAItor"
    $h = $null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try { $h = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 3; break } catch { $h = $null }
    }
    if (-not $h) { throw "The server did not come back on port $Port; see $InstallDir\logs." }
    Write-Host "Running version $($h.version) $label, sessions $($h.sessions)/$($h.max_sessions)" -ForegroundColor Green
}

if ($Stop) { Stop-Server; Write-Host "Stopped. Start again with -Restart (or reboot)."; exit 0 }
if ($Restart) { Stop-Server; Start-Server ""; exit 0 }

$branch = (& git -C $App rev-parse --abbrev-ref HEAD).Trim()
$before = (& git -C $App rev-parse --short HEAD).Trim()
& git -C $App fetch --quiet origin $branch
& git -C $App reset --quiet --hard "origin/$branch"
$after = (& git -C $App rev-parse --short HEAD).Trim()
if ($before -eq $after) { Write-Host "Already at $after ($branch); nothing to update. Use -Restart to restart anyway."; exit 0 }
Write-Host "Updating $branch $before -> $after"

& $Py -m pip install --quiet "$App[llm]" "admit-bench @ git+https://github.com/Refiant-Inc/admit-bench.git"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Push-Location (Join-Path $App "frontend")
try {
    & npm ci --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    & npm run build --silent
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally { Pop-Location }
Copy-Item -Force (Join-Path $App "deploy\windows\start.ps1") (Join-Path $InstallDir "start.ps1")
Copy-Item -Force (Join-Path $App "deploy\windows\update.ps1") (Join-Path $InstallDir "update.ps1")

Stop-Server
Start-Server "at $after"
