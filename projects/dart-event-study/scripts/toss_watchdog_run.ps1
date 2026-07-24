# Toss watchdog wrapper (called by a separate hourly scheduled task).
# ASCII-only. Runs the Python decision, and on 'rerun'/'rerun_alert' triggers the
# EXISTING crawl task (schtasks /run) -- NOT the wrapper directly -- so the crawl
# task's MultipleInstances=IgnoreNew prevents overlap with a scheduled crawl.
# Re-run is idempotent (update_cumulative upserts by post_id).
$ErrorActionPreference = "Stop"
$repo = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"
$env:UV_LINK_MODE = "copy"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$logDir = Join-Path $repo "projects/dart-event-study/data/toss_logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("watchdog_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

# Decision (last stdout line = JSON). ErrorActionPreference=Continue so stderr can't throw.
$decision = ""
try {
    $ErrorActionPreference = "Continue"
    $out = & uv run --frozen python -m dart_event_study.toss.watchdog 2>&1 | ForEach-Object { $_.ToString() }
    $out | Out-File -FilePath $log -Append -Encoding utf8
    $decision = ($out | Where-Object { $_ -match '^\{' } | Select-Object -Last 1)
} catch {
    "WATCHDOG WRAPPER EXCEPTION: $($_.Exception.Message)" | Out-File -FilePath $log -Append -Encoding utf8
} finally {
    $ErrorActionPreference = "Stop"
}

$action = "unknown"
if ($decision) { try { $action = (ConvertFrom-Json $decision).action } catch {} }
"action=$action" | Out-File -FilePath $log -Append -Encoding utf8

if ($action -eq "rerun" -or $action -eq "rerun_alert") {
    # Trigger the existing crawl task (IgnoreNew prevents overlap). Fallback: wrapper direct.
    $task = Get-ScheduledTask -TaskName QuantLab_TossCrawl -ErrorAction SilentlyContinue
    if ($task) {
        schtasks /run /tn QuantLab_TossCrawl | Out-File -FilePath $log -Append -Encoding utf8
    } else {
        & (Join-Path $PSScriptRoot "toss_crawl_run.ps1")
    }
}

if ($action -ne "ok") {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Warning
        $n.Visible = $true
        $n.ShowBalloonTip(10000, "Toss watchdog: $action", "Collection may have stalled. Check WATCHDOG_ALERT.txt.", "Warning")
        Start-Sleep -Seconds 11; $n.Dispose()
    } catch {}
}
