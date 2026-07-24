# Registers the Toss watchdog as an hourly scheduled task.
# ** APPROVAL-GATED: this modifies the Windows Task Scheduler. Run manually only
#    after explicit approval. ** Not invoked by any automation.
#
# Runs hourly, checks whether collection has stalled (data-arrival based), and on
# a stale/missing verdict triggers the existing QuantLab_TossCrawl task + toast.
# Non-admin (interactive daily trigger); no AtStartup so admin isn't required.
$ErrorActionPreference = "Stop"
$repo = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$script = Join-Path $repo "projects/dart-event-study/scripts/toss_watchdog_run.ps1"
$taskName = "QuantLab_TossWatchdog"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
# 매시간 반복 (하루 종일). 크롤 슬롯 사이 갭을 촘촘히 감시.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(8) `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Toss collection watchdog (hourly liveness check)" -Force

Write-Host "등록 완료: $taskName (매시간). 검증:"
Write-Host "  Get-ScheduledTaskInfo -TaskName $taskName"
