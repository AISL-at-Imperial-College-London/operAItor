<#
.SYNOPSIS
  Install OperAItor as an always-on public server on a dedicated Windows machine.

.DESCRIPTION
  Run this ON THE HOSTING MACHINE, not on a development laptop. It installs from GitHub into its own
  folder (nothing is copied from a development checkout, no .env and no keys), then registers a
  scheduled task that starts the server at boot, whether or not anyone is logged in, and restarts it
  if it stops.

  The server runs in public mode (PLANT_HOSTED=1): visitors connect their own LLM (free Groq, Gemini or
  OpenRouter keys), the machine's own keys and models are never used, and nothing is written to disk.
  It listens on 127.0.0.1 only; Tailscale Funnel publishes it on the internet over HTTPS
  (see docs/deployment.md, "Your own Windows machine").

  Requirements on the hosting machine: Python 3.11+ (python.org installer, with the "py" launcher),
  Git, Node.js 20+. Run in an elevated PowerShell (needed to register the startup task).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
  powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir D:\OperAItor -MaxSessions 6
#>
param(
    [string]$InstallDir = "C:\OperAItor",
    [string]$Repo = "https://github.com/AISL-at-Imperial-College-London/operAItor.git",
    [string]$Branch = "main",
    [int]$Port = 7860,
    [int]$MaxSessions = 4,
    [switch]$NoTask
)
$ErrorActionPreference = "Stop"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd not found. $hint" }
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -and -not $NoTask) { throw "Run this in an elevated PowerShell (Run as administrator), or pass -NoTask." }

Step "Checking prerequisites"
Need "git" "Install Git for Windows: https://git-scm.com/download/win"
Need "node" "Install Node.js 20 LTS or newer: https://nodejs.org"
Need "npm" "Install Node.js 20 LTS or newer: https://nodejs.org"
Need "py" "Install Python 3.11+ from https://www.python.org (tick 'py launcher')"
$pyver = (& py -3 -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ([version]$pyver -lt [version]"3.11") { throw "Python $pyver found; OperAItor needs 3.11 or newer." }
Write-Host "Python $pyver, Node $((& node --version).Trim()), $((& git --version).Trim())"

$App = Join-Path $InstallDir "app"
$Venv = Join-Path $InstallDir "venv"
$Logs = Join-Path $InstallDir "logs"
New-Item -ItemType Directory -Force -Path $InstallDir, $Logs | Out-Null

Step "Getting the code ($Repo, branch $Branch)"
if (Test-Path (Join-Path $App ".git")) {
    & git -C $App fetch --quiet origin $Branch
    & git -C $App checkout --quiet $Branch
    & git -C $App reset --quiet --hard "origin/$Branch"
} else {
    & git clone --quiet --branch $Branch $Repo $App
}
if ($LASTEXITCODE -ne 0) { throw "git failed" }
Write-Host "At commit $((& git -C $App rev-parse --short HEAD).Trim())"

Step "Python environment ($Venv, separate from any development setup)"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) { & py -3 -m venv $Venv }
$Py = Join-Path $Venv "Scripts\python.exe"
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet "$App[llm]" "admit-bench @ git+https://github.com/Refiant-Inc/admit-bench.git"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Step "Building the console"
Push-Location (Join-Path $App "frontend")
try {
    & npm ci --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    & npm run build --silent
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally { Pop-Location }

Step "Writing settings ($InstallDir\settings.ps1)"
$settings = Join-Path $InstallDir "settings.ps1"
if (-not (Test-Path $settings)) {
@"
# OperAItor server settings. Edit, then restart:  Restart-ScheduledTask OperAItor  (or reboot)
`$Port = $Port               # local port; Tailscale Funnel forwards https traffic here
`$MaxSessions = $MaxSessions          # concurrent plants (one per browser tab); about one per CPU core
`$SessionIdleMin = 15        # a session with no open tab is closed after this long
`$SessionMaxMin = 180        # maximum lifetime of a session
"@ | Set-Content -Encoding UTF8 $settings
}
Copy-Item -Force (Join-Path $App "deploy\windows\start.ps1") (Join-Path $InstallDir "start.ps1")
Copy-Item -Force (Join-Path $App "deploy\windows\update.ps1") (Join-Path $InstallDir "update.ps1")

if (-not $NoTask) {
    Step "Registering the startup task 'OperAItor'"
    $ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $action = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $InstallDir `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $InstallDir 'start.ps1')`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    # S4U: runs at boot without anyone logged in and without storing a password; ordinary (not elevated) rights.
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
    # start.ps1 restarts the server itself; the task restarts start.ps1 only if PowerShell itself fails.
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "OperAItor" -Action $action -Trigger $trigger -Principal $principal `
        -Settings $set -Force | Out-Null
    Start-ScheduledTask -TaskName "OperAItor"

    Step "Waiting for the server"
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try { $h = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 3; $ok = $true; break } catch {}
    }
    if (-not $ok) { throw "The server did not answer on port $Port. See the newest log in $Logs." }
    Write-Host "Running: version $($h.version), sessions $($h.sessions)/$($h.max_sessions)" -ForegroundColor Green
}

Write-Host @"

Installed in $InstallDir.
Next: publish it with Tailscale Funnel (docs/deployment.md, "Your own Windows machine"):
    tailscale funnel --bg $Port
Update later with:   powershell -ExecutionPolicy Bypass -File $InstallDir\update.ps1
"@
