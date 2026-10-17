# Тихая установка или обновление по docs/dev/install.md, шаги 1–5, одной командой.
# Без прав администратора копирует себя и setup в -Work (с \\wsl.localhost exe не
# запускается), перезапускается через RunAs, ждёт и печатает журнал. С правами:
# запоминает, работал ли туннель, ставит /VERYSILENT, поднимает туннель и трей,
# если их нет, пишет status. Код выхода — код setup (таблица в install.md, шаг 3).
# ВАЖНО: файл в UTF-8 С BOM.
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Setup <setup.exe> [-Work <папка>]

param([Parameter(Mandatory)][string]$Setup, [string]$Work = "$env:TEMP\tvpn-install", [switch]$Elevated)

$exe = 'C:\Program Files\TunnelVPN\tunnelvpn.exe'
$tray = 'C:\Program Files\TunnelVPN\TunnelVPN-Tray.exe'
$log = Join-Path $Work 'install.log'

if (-not $Elevated) {
    New-Item -ItemType Directory -Force $Work | Out-Null
    $self = Join-Path $Work 'install.ps1'
    $copy = Join-Path $Work (Split-Path $Setup -Leaf)
    Copy-Item -LiteralPath $PSCommandPath $self -Force
    if ((Resolve-Path -LiteralPath $Setup).ProviderPath -ne $copy) { Copy-Item -LiteralPath $Setup $copy -Force }
    Remove-Item $log -ErrorAction SilentlyContinue
    $p = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru -ArgumentList '-NoProfile',
        '-ExecutionPolicy', 'Bypass', '-File', "`"$self`"", '-Setup', "`"$copy`"", '-Work', "`"$Work`"", '-Elevated'
    # Не -Wait: setup в конце запускает трей, и -Wait ждал бы всё дерево процессов.
    $p.WaitForExit()
    if (Test-Path $log) { Get-Content $log -Encoding UTF8 } else { "журнала нет: повышенный запуск не начался" }
    exit $p.ExitCode
}

function W($s) { Add-Content -Path $log -Value $s -Encoding UTF8 }
Set-Content -Path $log -Value "начало $(Get-Date -Format s)" -Encoding UTF8
$wasUp = Test-Path $exe
if ($wasUp) {
    $before = (& $exe status) -join "`n"
    $wasUp = $before -match ': работает'
    W "до: $(($before -split "`n")[0])"
}

$p = Start-Process $Setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru
$p.WaitForExit()
$code = $p.ExitCode
W "setup: код $code"
W ((sc.exe query TunnelVPN | Select-String 'STATE') -join '')

function Up { ((& $exe status) -join "`n") -match ': работает' }
# Служба после setup поднимает туннель сама (автоподключение) — даём ей время.
$end = (Get-Date).AddSeconds(30)
while ($wasUp -and -not (Up) -and (Get-Date) -lt $end) { Start-Sleep 2 }
if ($wasUp -and -not (Up)) {
    W 'туннель не вернулся — tunnelvpn.exe start'
    W ((& $exe start 2>&1) -join "`n")
}
if (-not (Get-Process TunnelVPN-Tray -ErrorAction SilentlyContinue)) {
    W 'трея нет — запускаю'
    Start-Process $tray
}
Start-Sleep 3
W ((& $exe version | Select-Object -First 1) -join '')
W ((& $exe status) -join "`n")
W "конец $(Get-Date -Format s)"
exit $code
