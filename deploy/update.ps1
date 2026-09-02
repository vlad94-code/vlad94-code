<#
    Выкатка в боевой экземпляр: остановить, забрать код, поднять.

    Запускать в папке prod ПОСЛЕ того, как изменения прошли полный прогон
    тестов в dev (см. README, «Два уровня проверки»). Здесь тестов нет
    намеренно: боевое окружение ставит только requirements.txt, без pytest.
#>
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$taskName = "CNC-Knowledge-Bot"

Write-Output "1/5 останавливаю бота"
try { Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop } catch { Write-Output "    задача не запущена" }
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "$root*" } |
    ForEach-Object { Stop-Process -Id $_.Id -Force }

Write-Output "2/5 забираю код"
git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull не удался — выкатка прервана, бот не запущен" }

Write-Output "3/5 обновляю зависимости"
& (Join-Path $root "venv\Scripts\python.exe") -m pip install --quiet -r requirements.txt

Write-Output "4/5 предполётная проверка"
$check = & (Join-Path $root "venv\Scripts\python.exe") -c @"
import os, sys, importlib.util
os.environ.setdefault('TELEGRAM_BOT_TOKEN','preflight'); os.environ.setdefault('ADMIN_USER_IDS','0')
s = importlib.util.spec_from_file_location('bot','bot.py'); m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
m.preflight()
print('ok')
"@
if ($LASTEXITCODE -ne 0) { throw "предполётная проверка не прошла — бот не запущен, откатитесь через git" }

Write-Output "5/5 запускаю"
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 5
$running = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$root*" }
if ($running) { Write-Output "Готово. Бот работает (PID $($running.Id))." }
else { Write-Output "ВНИМАНИЕ: процесс не обнаружен, смотрите logs\" }
