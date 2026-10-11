# Страховка на время ручных сценариев мастера на этой машине: связь агента
# идёт через туннель, а страницы ошибки, ⚠ на «Готово» и промежуток между
# удалением и новой установкой ждут клика без туннеля. Запускать повышенным:
#
#   Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList
#     '-NoProfile','-ExecutionPolicy','Bypass','-File','<копия guard.ps1>',
#     '-Work','<папка>','-Setup','<setup.exe>','-Bridge','<копия dist\TunnelVPN>'
#
# Туннеля нет -Fast секунд, а установщик закрыт или ждёт клика (нет дочерних
# процессов, не растёт процессорное время) — возвращает его; пока установщик
# работает, ждёт до -Grace секунд, чтобы не мешать ему. Способ самый мягкий:
# служба есть — запускает её и туннель; службы нет — «мост»: собранный
# tunnelvpn.exe service console из -Bridge (его сам гасит установщик:
# StopEverything убивает tunnelvpn.exe); службы и моста нет, мастер закрыт —
# тихо ставит -Setup. Пока туннеля нет — снимок экрана раз в 5 секунд
# в <Work>\shots. Журнал — <Work>\guard.log, пульс — <Work>\guard.alive
# (время последнего круга). Файл <Work>\stop — выход.
# ВАЖНО: файл в UTF-8 С BOM, как build.ps1.

param(
    [Parameter(Mandatory)][string]$Work,
    [Parameter(Mandatory)][string]$Setup,
    [string]$Bridge = '',
    [string]$ConfBackup = '',
    # Установщик закрыт или ждёт клика: восстанавливать быстро — Claude Code
    # повторяет запрос к API около 2 минут.
    [int]$Fast = 10,
    # Установщик работает: дать ему вернуть туннель самому.
    [int]$Grace = 90,
    [int]$Minutes = 240
)

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$Shots = Join-Path $Work 'shots'
New-Item -ItemType Directory -Force $Shots | Out-Null
$LogFile = Join-Path $Work 'guard.log'
$StopFile = Join-Path $Work 'stop'
$AliveFile = Join-Path $Work 'guard.alive'
Remove-Item $StopFile -ErrorAction SilentlyContinue
$Conf = Join-Path $env:ProgramData 'TunnelVPN\conf'

function Log([string]$Text) {
    Add-Content -Path $LogFile -Encoding UTF8 -Value ("{0:HH:mm:ss} {1}" -f (Get-Date), $Text)
}

function TunnelUp {
    [bool](Get-NetRoute -DestinationPrefix '0.0.0.0/1' -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -like 'tun*' })
}

# Путь к tunnelvpn.exe установленной службы; $null — службы нет.
function ServiceExe {
    $s = Get-CimInstance Win32_Service -Filter "Name='TunnelVPN'" -ErrorAction SilentlyContinue
    if ($s -and $s.PathName -match '^"?([^"]+?\.exe)') { $Matches[1] } else { $null }
}

# Мастер установки (<имя>-setup.tmp) или деинсталлятор (_iu*.tmp, unins*).
function InnoProcs {
    @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '*setup*' -or $_.Name -like '_iu*' -or $_.Name -like 'unins*' })
}

function InnoRunning { (InnoProcs).Count -gt 0 }

# Установщик занят делом: у него есть дочерние процессы (tunnelvpn.exe, sc.exe,
# schtasks.exe…) или растёт процессорное время с прошлого круга. Иначе он
# стоит на странице и ждёт клика. $script:InnoCpu — сумма с прошлого круга.
$script:InnoCpu = -1
function InnoBusy {
    $procs = InnoProcs
    if (-not $procs) { $script:InnoCpu = -1; return $false }
    $ids = $procs.Id
    $kids = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $ids -contains $_.ParentProcessId -and $ids -notcontains $_.ProcessId -and $_.Name -ne 'conhost.exe' }
    $cpu = ($procs | Measure-Object -Property CPU -Sum).Sum
    $grew = $script:InnoCpu -ge 0 -and $cpu - $script:InnoCpu -gt 0.2
    $script:InnoCpu = $cpu
    [bool]$kids -or $grew
}

# Ждёт маршрут туннеля до $Seconds секунд.
function WaitUp([int]$Seconds) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) {
        if (TunnelUp) { return $true }
        Start-Sleep 1
    }
    $false
}

function Shot([string]$Name) {
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
    $bmp.Save((Join-Path $Shots "$Name.png"), [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
}

function RestoreConf {
    if ($ConfBackup -and -not (Test-Path (Join-Path $Conf 'tunnels.json'))) {
        Log "conf: tunnels.json нет — возвращаю из $ConfBackup"
        robocopy $ConfBackup $Conf /E /COPYALL /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
    }
}

function StartTunnel([string]$Exe) {
    $svc = Get-Service TunnelVPN -ErrorAction SilentlyContinue
    if ($svc.Status -ne 'Running') {
        Log 'служба не работает — service start'
        & $Exe service start 2>&1 | Out-Null
        # Автоподключение поднимет туннель само — тогда start не нужен.
        WaitUp 8 | Out-Null
    }
    for ($i = 0; $i -lt 5 -and -not (TunnelUp); $i++) {
        & $Exe start 2>&1 | Out-Null
        Log "tunnelvpn start: код $LASTEXITCODE"
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep 3
    }
    $tray = Join-Path (Split-Path $Exe) 'TunnelVPN-Tray.exe'
    if (-not (Get-Process TunnelVPN-Tray -ErrorAction SilentlyContinue) -and (Test-Path $tray)) {
        Log 'трей не запущен — запускаю'
        Start-Process $tray -ArgumentList '--background'
    }
}

function StartBridge {
    $exe = Join-Path $Bridge 'tunnelvpn.exe'
    if (Get-Process tunnelvpn -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
        Log 'мост уже работает'
    }
    else {
        Log "мост: $exe service console"
        Start-Process $exe -ArgumentList 'service', 'console' -WindowStyle Hidden
    }
    # Канал службы открывается не сразу: start повторяется, пока не пройдёт.
    for ($i = 0; $i -lt 8 -and -not (TunnelUp); $i++) {
        Start-Sleep 2
        & $exe start 2>&1 | Out-Null
        Log "мост: start, код $LASTEXITCODE"
        if ($LASTEXITCODE -eq 0) { break }
    }
}

function SilentInstall {
    Log "ставлю тихо: $Setup"
    $p = Start-Process $Setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru
    $p.WaitForExit()
    Log "setup: код $($p.ExitCode)"
    $exe = ServiceExe
    if ($exe) { StartTunnel $exe }
}

Log "старт: Fast $Fast с, Grace $Grace с, срок $Minutes мин, setup $Setup, мост '$Bridge', копия conf '$ConfBackup'"
$deadline = (Get-Date).AddMinutes($Minutes)
# $firstDown — когда туннель пропал (для журнала), $downSince — отсчёт до
# следующей попытки, $idleSince — с какого круга установщик не занят.
$firstDown = $null
$downSince = $null
$idleSince = $null
$shot = 0
$lastShot = [datetime]::MinValue
while ((Get-Date) -lt $deadline -and -not (Test-Path $StopFile)) {
    Set-Content -Path $AliveFile -Value (Get-Date -Format 'HH:mm:ss')
    if (TunnelUp) {
        if ($firstDown) { Log ("туннель вернулся через {0:N0} с" -f ((Get-Date) - $firstDown).TotalSeconds) }
        $firstDown = $null
        $downSince = $null
        $idleSince = $null
        Start-Sleep 2
        continue
    }
    if (-not $firstDown) {
        $firstDown = Get-Date
        $downSince = $firstDown
        Log "туннеля нет; служба: $(ServiceExe); мастер: $(InnoRunning); conf: $(Test-Path (Join-Path $Conf 'tunnels.json'))"
    }
    if ($shot -lt 200 -and ((Get-Date) - $lastShot).TotalSeconds -ge 5) {
        $shot++; $lastShot = Get-Date
        Shot ("down-{0:D3}-{1:HHmmss}" -f $shot, $lastShot)
    }
    if (InnoBusy) { $idleSince = $null } elseif (-not $idleSince) { $idleSince = Get-Date }
    $since = [datetime]$(if ($idleSince -and $idleSince -gt $downSince) { $idleSince } else { $downSince })
    $quiet = $idleSince -and ((Get-Date) - $since).TotalSeconds -ge $Fast
    if (-not $quiet -and ((Get-Date) - $downSince).TotalSeconds -lt $Grace) {
        Start-Sleep 1
        continue
    }
    $exe = ServiceExe
    $inno = InnoRunning
    Log "восстанавливаю: служба $exe, мастер $inno, установщик $(if ($quiet) { 'ждёт' } else { 'занят дольше Grace' })"
    RestoreConf
    if ($exe -and (Test-Path $exe)) {
        StartTunnel $exe
    }
    elseif ($Bridge) {
        StartBridge
    }
    elseif (-not $inno) {
        SilentInstall
    }
    else {
        Log 'службы нет, мастер открыт, моста нет — жду'
    }
    if (-not (WaitUp 20)) { Log 'туннель не поднялся за 20 с — следующая попытка' }
    # Следующая попытка — не раньше чем через ещё одну паузу.
    $downSince = Get-Date
    $idleSince = $null
}
Log $(if (Test-Path $StopFile) { 'выход: файл stop' } else { "выход: срок $Minutes мин истёк" })
Remove-Item $AliveFile -ErrorAction SilentlyContinue
