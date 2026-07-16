# Register Toss crawl schedule (Windows Task Scheduler). ASCII-only.
#
# *** RUN AS ADMINISTRATOR ***  The AtStartup trigger requires elevation; without
# admin, registration fails with "Access is denied". After running, VERIFY with:
#     Get-ScheduledTask -TaskName QuantLab_TossCrawl
#     Get-ScheduledTaskInfo -TaskName QuantLab_TossCrawl   # LastTaskResult, NextRunTime
#
# Slots: 09:00 / 12:00 / 15:30 / 21:00 daily (KST) + AtStartup (reboot recovery).
# StartWhenAvailable => runs a missed slot soon after wake.
# Battery/sleep: AllowStartIfOnBatteries + DontStopIfGoingOnBatteries + WakeToRun so
#   the laptop still collects on battery and wakes from sleep at slot time.
#   NOTE: WakeToRun cannot wake a fully powered-off (shutdown) machine -- see
#   docs/toss_collection.md operational note.
$ErrorActionPreference = "Stop"
$runner = Join-Path $PSScriptRoot "toss_crawl_run.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`""

$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At 9:00am),
    (New-ScheduledTaskTrigger -Daily -At 12:00pm),
    (New-ScheduledTaskTrigger -Daily -At 3:30pm),
    (New-ScheduledTaskTrigger -Daily -At 9:00pm),
    (New-ScheduledTaskTrigger -AtStartup)
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun `
    -MultipleInstances IgnoreNew

$name = "QuantLab_TossCrawl"
try {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers `
        -Settings $settings -Description "Toss community forward collection (first-seen)" `
        -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Error "FAILED to register '$name': $($_.Exception.Message)"
    Write-Host "  (AtStartup trigger needs Administrator. Re-run this script elevated.)"
    exit 1
}

# Confirm it actually registered before claiming success (Register can emit a
# non-terminating error yet fall through -- verify the object exists).
$task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Error "Registration reported no error but task '$name' is not present. Run elevated."
    exit 1
}
Write-Host "Registered: $name  (State: $($task.State))"
Write-Host "  Triggers: 09:00 / 12:00 / 15:30 / 21:00 daily + at startup"
Write-Host "  ExecutionTimeLimit=3h, battery=allowed, WakeToRun=on, MultipleInstances=IgnoreNew"
Write-Host "  Verify:  Get-ScheduledTaskInfo -TaskName $name"
Write-Host "  Remove:  Unregister-ScheduledTask -TaskName $name -Confirm:`$false"
