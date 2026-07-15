# Register Toss crawl schedule (Windows Task Scheduler). ASCII-only.
# Slots: 09:00 / 12:00 / 15:30 / 21:00 daily (KST) + AtStartup (reboot recovery).
# StartWhenAvailable => runs a missed slot soon after wake. Run once to install.
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
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$name = "QuantLab_TossCrawl"
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers `
    -Settings $settings -Description "Toss community forward collection (first-seen)" -Force | Out-Null

Write-Host "Registered: $name"
Write-Host "  Triggers: 09:00 / 12:00 / 15:30 / 21:00 daily + at startup"
Write-Host "  StartWhenAvailable=on -> runs missed slot after wake"
Write-Host "  Check:  Get-ScheduledTaskInfo -TaskName $name"
Write-Host "  Remove: Unregister-ScheduledTask -TaskName $name -Confirm:`$false"
