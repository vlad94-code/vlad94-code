<#
    Запуск бота как постоянной службы.

    Две вещи, ради которых нужен этот скрипт, а не просто "python bot.py":

    1. Рабочий каталог. Пути к данным в проекте относительные (data/, uploads/).
       Планировщик Windows стартует задачу с произвольным каталогом, поэтому
       выставляем его явно. Предполётная проверка в bot.py это же и
       контролирует — здесь мы просто не даём ей повода сработать.
    2. Перезапуск. Процесс может упасть: обрыв сети к Telegram, сбой диска.
       Пять менеджеров не должны ждать, пока кто-то заметит и поднимет руками.
#>
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Не найден интерпретатор: $python" }

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

while ($true) {
    $stamp = Get-Date -Format "yyyy-MM-dd"
    $out = Join-Path $logDir "bot_$stamp.out.log"
    $err = Join-Path $logDir "bot_$stamp.err.log"

    Add-Content $out -Encoding utf8 -Value "[$(Get-Date -Format o)] запуск bot.py"

    # Start-Process с раздельными потоками: перенаправление stderr родного exe
    # через 2>&1 внутри PowerShell 5.1 оборачивает каждую строку в ErrorRecord
    # и портит и вывод, и код возврата.
    $proc = Start-Process -FilePath $python -ArgumentList "bot.py" `
        -WorkingDirectory $root -NoNewWindow -PassThru `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $proc.WaitForExit()

    $code = $proc.ExitCode
    Add-Content $err -Encoding utf8 -Value "[$(Get-Date -Format o)] процесс завершился с кодом $code, перезапуск через 15 с"
    Start-Sleep -Seconds 15
}
