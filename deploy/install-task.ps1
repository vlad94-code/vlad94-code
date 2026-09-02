<#
    Регистрирует автозапуск бота при загрузке Windows.

    Запускать ОДИН РАЗ, от администратора, из папки боевого экземпляра:
        powershell -ExecutionPolicy Bypass -File deploy\install-task.ps1

    Задача стартует при загрузке компьютера, поэтому вход в систему не нужен —
    после отключения света машина поднимется и бот заработает сам (при условии
    что в BIOS включено восстановление питания).
#>
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "deploy\run-bot.ps1"
$taskName = "CNC-Knowledge-Bot"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Output "Задача '$taskName' зарегистрирована. Рабочий каталог: $root"
Write-Output "Запустить сейчас:  Start-ScheduledTask -TaskName $taskName"
Write-Output "Остановить:        Stop-ScheduledTask  -TaskName $taskName"
Write-Output "Состояние:         Get-ScheduledTask   -TaskName $taskName"
