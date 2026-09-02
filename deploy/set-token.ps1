<#
    Записывает токен бота в .env.

    Зачем отдельный скрипт, а не «откройте блокнот»: токен меняется чаще, чем
    кажется (отозвали в BotFather, завели нового бота), и каждый раз это ручная
    правка боевого файла вслепую. Здесь ввод не показывается на экране и не
    попадает в историю PowerShell, токен проверяется у Telegram ДО записи, а
    прежний .env сохраняется рядом — если что-то не так, откатиться нечем не
    придётся.

    Запускать из папки экземпляра:
        powershell -ExecutionPolicy Bypass -File deploy\set-token.ps1
#>
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"
if (-not (Test-Path $envPath)) { throw "Не найден файл $envPath" }

$secure = Read-Host "Вставьте токен от BotFather (ввод не отображается)" -AsSecureString
$token = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)).Trim()

if ($token -notmatch '^\d+:[A-Za-z0-9_-]{30,}$') {
    throw "Это не похоже на токен бота (ожидается вид 1234567890:AA...)"
}

# Проверяем ДО записи: неверный токен в .env означает бота в цикле падений,
# и понять это можно только по логам.
$me = Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/getMe" -TimeoutSec 20
if (-not $me.ok) { throw "Telegram не принял токен" }
Write-Output "Токен принят: @$($me.result.username) — $($me.result.first_name)"

Copy-Item $envPath "$envPath.bak" -Force

$found = $false
$lines = foreach ($line in (Get-Content $envPath -Encoding UTF8)) {
    if ($line -match '^\s*TELEGRAM_BOT_TOKEN\s*=') { $found = $true; "TELEGRAM_BOT_TOKEN=$token" }
    else { $line }
}
if (-not $found) { $lines += "TELEGRAM_BOT_TOKEN=$token" }

# Без BOM: python-dotenv читает файл как обычный UTF-8, и BOM превратил бы имя
# первой переменной в "﻿TELEGRAM_BOT_TOKEN".
[IO.File]::WriteAllLines($envPath, $lines, (New-Object Text.UTF8Encoding($false)))

Write-Output "Записано в $envPath (прежний файл сохранён как .env.bak)"
Write-Output "Дальше — перезапуск от администратора: deploy\update.ps1"
