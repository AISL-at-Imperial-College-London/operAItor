<#
  Start the OperAItor server in public mode. Run by the 'OperAItor' scheduled task at boot (see install.ps1);
  the task restarts it if it exits. Logs: <install dir>\logs\operaitor-YYYYMMDD.log (kept 14 days).
#>
$ErrorActionPreference = "Stop"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = 7860; $MaxSessions = 4; $SessionIdleMin = 15; $SessionMaxMin = 180
. (Join-Path $InstallDir "settings.ps1")

$App = Join-Path $InstallDir "app"
$Py = Join-Path $InstallDir "venv\Scripts\python.exe"
$Logs = Join-Path $InstallDir "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
Get-ChildItem $Logs -Filter "operaitor-*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Public mode: visitors' own LLM connections only; this machine's keys, models and Ollama are never used,
# and nothing is written to disk. No .env is read from here (the working directory has none).
$env:PLANT_HOSTED = "1"
$env:PLANT_CAMPAIGN_DIR = ""
$env:PLANT_MAX_SESSIONS = "$MaxSessions"
$env:PLANT_SESSION_IDLE_MIN = "$SessionIdleMin"
$env:PLANT_SESSION_MAX_MIN = "$SessionMaxMin"
$env:PLANT_FRONTEND_DIST = Join-Path $App "frontend\dist"
$env:PLANT_DOTENV = Join-Path $InstallDir "no-such.env"
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

Set-Location $InstallDir
# Restart the server whenever it exits (a crash, or update.ps1 stopping it); the task only starts this script.
while ($true) {
    $log = Join-Path $Logs ("operaitor-{0:yyyyMMdd}.log" -f (Get-Date))
    Add-Content -Encoding Ascii -Path $log -Value ("{0:s} starting OperAItor on 127.0.0.1:{1}" -f (Get-Date), $Port)
    # Listen on this machine only; Tailscale Funnel (or a reverse proxy) is the only way in from outside.
    # cmd does the redirection: PowerShell 5.1 would turn the server's stderr log lines into errors and
    # write the file as UTF-16.
    cmd /c "`"$Py`" -m operaitor serve --hosted --host 127.0.0.1 --port $Port >> `"$log`" 2>&1"
    Add-Content -Encoding Ascii -Path $log -Value ("{0:s} OperAItor exited with code {1}; restarting in 10 s" -f (Get-Date), $LASTEXITCODE)
    Start-Sleep -Seconds 10
}
