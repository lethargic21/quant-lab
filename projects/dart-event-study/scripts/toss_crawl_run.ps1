# Toss crawl single-run wrapper (called by Task Scheduler).
# ASCII-only on purpose: this runs headless for weeks; no non-ASCII to avoid
# PowerShell codepage/encoding breakage. On failure (exit!=0) it raises an alert
# (marker file + Windows balloon) -- silent death is the biggest operational risk.
$ErrorActionPreference = "Stop"
# scripts -> dart-event-study -> projects -> quant-lab (three levels up)
$repo = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"
$env:UV_LINK_MODE = "copy"
# Playwright browsers live in a fixed ASCII path OUTSIDE %LOCALAPPDATA%.
# The Task Scheduler execution context cannot read C:\Users\<u>\AppData\Local\ms-playwright
# (verified: pathlib.exists()==False there while True in an interactive shell -- likely
# Defender Controlled Folder Access / profile-context restriction), so scheduled runs
# failed with "Executable doesn't exist". This path is readable in the scheduled context.
# Browsers were seeded here by copying ms-playwright; update via:
#   $env:PLAYWRIGHT_BROWSERS_PATH="C:\pw-browsers"; uv run --project projects/dart-event-study python -m playwright install chromium
$env:PLAYWRIGHT_BROWSERS_PATH = "C:\pw-browsers"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $repo "projects/dart-event-study/data/toss_logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "crawl_$stamp.log"

"=== crawl start $(Get-Date) ===" | Out-File -Encoding utf8 $log
uv run --project projects/dart-event-study python -m dart_event_study.toss.crawl *>&1 |
    Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
"=== exit $code $(Get-Date) ===" | Out-File -Encoding utf8 -Append $log

if ($code -ne 0) {
    $alert = Join-Path $logDir "LAST_FAILURE.txt"
    "Toss crawl FAILED $(Get-Date) -- log: $log" | Out-File -Encoding utf8 $alert
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Warning
        $n.Visible = $true
        $n.ShowBalloonTip(10000, "Toss crawl FAILED", "exit $code. Check log.", "Warning")
        Start-Sleep -Seconds 11; $n.Dispose()
    } catch {}
    exit $code
}
