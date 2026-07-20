# Toss crawl single-run wrapper (called by Task Scheduler).
# ASCII-only on purpose: this runs headless for weeks; no non-ASCII to avoid
# PowerShell codepage/encoding breakage.
#
# Exit contract (3 tiers -- the scheduler's LastTaskResult carries this):
#   0 = every ticker collected
#   2 = PARTIAL: data arrived but some tickers failed (usually sort verification)
#   1 = REAL FAILURE: no data at all (browser launch failure, wrapper exception, ...)
# Only tier 1 raises the alert. Sort failures are common and normal-ish, so they must
# not cry wolf -- they are recorded in sort_failures.csv and surfaced in STATUS.txt.
$ErrorActionPreference = "Stop"
# scripts -> dart-event-study -> projects -> quant-lab (three levels up)
$repo = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"
$env:UV_LINK_MODE = "copy"
# The child emits UTF-8; PowerShell decodes a native command's stdout with
# [Console]::OutputEncoding, which is cp949 in the scheduled context -> the log became
# mojibake. Pin it to UTF-8 so the captured text is correct at the source.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
# Playwright browsers live in a fixed ASCII path OUTSIDE %LOCALAPPDATA%.
# The Task Scheduler execution context cannot read C:\Users\<u>\AppData\Local\ms-playwright
# (verified: pathlib.exists()==False there while True in an interactive shell -- likely
# Defender Controlled Folder Access / profile-context restriction), so scheduled runs
# failed with "Executable doesn't exist". This path is readable in the scheduled context.
# Browsers were seeded here by copying ms-playwright; update via:
#   $env:PLAYWRIGHT_BROWSERS_PATH="C:\pw-browsers"; uv run --project projects/dart-event-study python -m playwright install chromium
$env:PLAYWRIGHT_BROWSERS_PATH = "C:\pw-browsers"

$startTime = Get-Date
$stamp = $startTime.ToString("yyyyMMdd_HHmmss")
$logDir = Join-Path $repo "projects/dart-event-study/data/toss_logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "crawl_$stamp.log"
$statusFile = Join-Path $logDir "STATUS.txt"
$alert = Join-Path $logDir "LAST_FAILURE.txt"
$sortCsv = Join-Path $logDir "sort_failures.csv"
$rawDir = Join-Path $repo "projects/dart-event-study/data/raw/toss"
$universeFile = Join-Path $repo "projects/dart-event-study/config/toss_universe.yaml"

"=== crawl start $startTime ===" | Out-File -Encoding utf8 $log

# ROOT CAUSE OF THE SILENT DEATHS (found and fixed 2026-07-20):
# In Windows PowerShell 5.1 a native command's stderr line becomes a NativeCommandError
# ErrorRecord. Under ErrorActionPreference="Stop" that is promoted to a TERMINATING error,
# so this wrapper died the instant uv/python wrote ANY stderr line -- even when the child
# exited 0. Consequences seen in the logs:
#   - log stopped at the 44-byte header, no "=== exit N ===" line
#   - the alert block below was NEVER REACHED -> no marker, no toast (silent death)
#   - the crawl child kept running orphaned until Task Scheduler tore the tree down,
#     leaving partial data and LastTaskResult 0x40010004 (terminated)
# Fix: run the child under "Continue" so stderr cannot throw, and stringify the records so
# the log stays plain UTF-8 text. (Tee-Object -Append also wrote UTF-16 into the UTF-8 file,
# which is why surviving logs were garbled -- Out-File keeps a single encoding.)
$childCode = 1
try {
    $ErrorActionPreference = "Continue"
    & uv run --project projects/dart-event-study python -m dart_event_study.toss.crawl 2>&1 |
        ForEach-Object { $_.ToString() } |
        Out-File -FilePath $log -Append -Encoding utf8
    $childCode = $LASTEXITCODE
} catch {
    $childCode = 1
    "WRAPPER EXCEPTION: $($_.Exception.Message)" | Out-File -FilePath $log -Append -Encoding utf8
} finally {
    $ErrorActionPreference = "Stop"
}

# --- Liveness is judged by DATA ARRIVAL, not by the child's exit code -------------
# A single ticker failing sort verification is common (3x in the first 5 days) and used to
# make the whole run look dead. What actually matters for the watchdog is "did snapshots
# land this run". Empty communities (e.g. 091990) still write a snapshot, so this count is
# a faithful per-ticker success measure (verified: a clean run touches 20/20).
$okTickers = @()
if (Test-Path $rawDir) {
    $okTickers = @(Get-ChildItem $rawDir -Recurse -Filter *.parquet -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "_cumulative.parquet" -and $_.LastWriteTime -ge $startTime } |
        ForEach-Object { Split-Path $_.DirectoryName -Leaf } |
        Sort-Object -Unique)
}
$okCount = $okTickers.Count

$total = 0
if (Test-Path $universeFile) {
    $total = (Select-String -Path $universeFile -Pattern '^\s*"\d{6}"\s*:').Count
}

# Sort failures recorded by the crawler during THIS run (ticker column is ASCII digits).
$failedTickers = @()
if (Test-Path $sortCsv) {
    try {
        $failedTickers = @(Import-Csv $sortCsv | Where-Object {
            $ts = [datetime]::MinValue
            [datetime]::TryParse($_.crawl_ts, [ref]$ts) -and $ts -ge $startTime
        } | ForEach-Object { $_.ticker } | Sort-Object -Unique)
    } catch { $failedTickers = @() }
}

if ($okCount -eq 0) {
    $code = 1   # nothing collected -> real failure
} elseif ($childCode -ne 0 -or $failedTickers.Count -gt 0 -or ($total -gt 0 -and $okCount -lt $total)) {
    $code = 2   # partial: data landed but not everything
} else {
    $code = 0
}

"=== exit $code (child $childCode, tickers $okCount/$total) $(Get-Date) ===" |
    Out-File -Encoding utf8 -Append $log

# --- STATUS.txt: one file that answers "is it alive, and what's broken?" ----------
# LAST_SUCCESS only advances when data actually arrived (tier 0 or 2); a tier-1 run leaves
# the previous value untouched so staleness is detectable. LAST_CRAWL always advances.
$lastSuccess = ""
if (Test-Path $statusFile) {
    $prev = Select-String -Path $statusFile -Pattern '^LAST_SUCCESS\s+(.+)$' |
        Select-Object -First 1
    if ($prev) { $lastSuccess = $prev.Matches[0].Groups[1].Value.Trim() }
}
$now = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
if ($code -ne 1) { $lastSuccess = $now }
if (-not $lastSuccess) { $lastSuccess = "(never)" }

$resultLabel = "OK"
if ($code -eq 2) { $resultLabel = "PARTIAL" }
if ($code -eq 1) { $resultLabel = "FAILED" }
$failedList = "none"
if ($failedTickers.Count -gt 0) { $failedList = ($failedTickers -join ",") }

@(
    "LAST_SUCCESS $lastSuccess",
    "LAST_CRAWL   $now",
    "RESULT       $resultLabel (exit $code)",
    "TICKERS      $okCount/$total",
    "SORT_FAILED  $failedList",
    "LOG          $log"
) | Out-File -Encoding utf8 $statusFile

if ($code -eq 1) {
    "Toss crawl FAILED $now -- exit $code (child $childCode, tickers $okCount/$total) -- log: $log" |
        Out-File -Encoding utf8 $alert
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Warning
        $n.Visible = $true
        $n.ShowBalloonTip(10000, "Toss crawl FAILED", "No data collected. Check log.", "Warning")
        Start-Sleep -Seconds 11; $n.Dispose()
    } catch {}
} elseif (Test-Path $alert) {
    Remove-Item $alert -Force   # data is flowing again -> clear stale failure marker
}

exit $code
