# Сборка TunnelVPN: три exe через PyInstaller, затем установщик через Inno Setup.
#
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
#
# Готовый установщик кладётся в installer\Output\TunnelVPN-<версия>-setup.exe.
# Запускать из корня репозитория или откуда угодно — путь вычисляется сам.
#
# ВАЖНО: файл сохранён в UTF-8 С BOM. Windows PowerShell 5.1 (powershell.exe,
# в отличие от pwsh) без BOM читает скрипт как ANSI: кириллица в строках и
# комментариях рассыпается, парсер спотыкается на «Missing closing '}'», и
# сборка падает ещё до первой команды. Не пересохраняй без BOM.
#
# -TestHooks — тестовый установщик для проверки сбоев:
# installer\Output\TunnelVPN-<версия>-testhooks-setup.exe, ключ /testhook=…
# ломает в нём первую попытку шага (TestHook в tunnelvpn.iss). Не в CI, не с
# подписью и без SHA256SUMS.txt: в релиз такой не уходит.

param([switch]$TestHooks, [switch]$Fast)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Bin = Join-Path $Root 'lib\bin'
$Inst = Join-Path $Root 'installer'
$Version = (Get-Content (Join-Path $Root 'VERSION') -Raw).Trim()

Write-Host "-> TunnelVPN $Version, корень: $Root"

# --- Подпись кода: только при заданном сертификате, иначе сборка та же, что
# всегда, без подписи. TVPN_SIGN_SHA1 — отпечаток сертификата в хранилище
# текущего пользователя или машины: так работают и токен с ключом, и .pfx,
# который CI импортирует из секрета (.github/workflows/build.yml), а пароль не
# попадает ни в командную строку, ни в журнал ISCC.
$SignSha1 = "$env:TVPN_SIGN_SHA1".Replace(' ', '')
if ($TestHooks -and $env:GITHUB_ACTIONS) { throw '-TestHooks — только локально: CI собирает релиз' }
if ($TestHooks -and $SignSha1) { throw '-TestHooks с подписью: тестовая сборка не подписывается, уберите TVPN_SIGN_SHA1' }
$SignTool = $null
if ($SignSha1) {
    if ($SignSha1 -notmatch '^[0-9A-Fa-f]{40}$') { throw "TVPN_SIGN_SHA1: не отпечаток SHA-1: $SignSha1" }
    # signtool из Windows SDK, самый свежий: на раннере GitHub SDK уже стоит.
    $SignTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" `
        -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $SignTool) { throw 'задан TVPN_SIGN_SHA1, но нет signtool.exe (Windows SDK)' }
    Write-Host "-> подпись: $SignTool, сертификат $SignSha1"
}
$Timestamp = 'http://timestamp.digicert.com'
# Одна команда на exe и на установщик; ISCC подставляет файл вместо $f.
$SignArgs = "sign /sha1 $SignSha1 /fd SHA256 /tr $Timestamp /td SHA256"

# Наши exe, не сторонние: sing-box и wintun подписывают (или нет) их авторы.
function Sign-Files([string[]]$Files) {
    & $SignTool ($SignArgs -split ' ') @Files
    if ($LASTEXITCODE -ne 0) { throw "signtool вернул $LASTEXITCODE" }
}

# --- Бинарники должны быть на месте ДО сборки: PyInstaller кладёт их внутрь
# сборки, и без них установщик соберётся, а приложение работать не будет.
if (-not (Test-Path (Join-Path $Bin 'happwn.dll'))) {
    & (Join-Path $Root 'happ_bridge\build.ps1')
}
foreach ($f in 'sing-box.exe', 'wintun.dll', 'happwn.dll') {
    if (-not (Test-Path (Join-Path $Bin $f))) {
        Write-Host "нет lib\bin\$f - см. README, раздел «Из исходников»"
        exit 1
    }
}

# --- Окружение сборки
# Установленное приложение несёт Python внутри себя, и его версия задаётся
# здесь. В CI (.github/workflows/build.yml) — та же, иначе релизы и локальные
# сборки работали бы на разных рантаймах.
$PyVersion = '3.14'
$Venv = Join-Path $Root 'lib\venv'
$Cfg = Join-Path $Venv 'pyvenv.cfg'
$Have = ''
if (Test-Path $Cfg) {
    $m = Select-String -Path $Cfg -Pattern '^version(_info)?\s*=\s*(\S+)' | Select-Object -First 1
    if ($m) { $Have = $m.Matches[0].Groups[2].Value }
}
# Окружение на другой версии пересоздаём: иначе новый Python до сборки
# не дошёл бы никогда.
$VenvCreated = $false
if (-not $Have.StartsWith("$PyVersion.")) {
    # Лаунчер py, а если его нет или он этой версии не знает (на раннере CI
    # Python ставит setup-python) — python из PATH. Берём только нужную версию.
    $SysPy = $null
    foreach ($cand in @(@('py', "-$PyVersion"), @('python'))) {
        if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)) { continue }
        $rest = @($cand | Select-Object -Skip 1)
        try {
            $out = & $cand[0] @rest -c "import sys; print('%d.%d' % sys.version_info[:2], sys.executable)" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and "$out" -match "^$([regex]::Escape($PyVersion)) (.+)$") {
            $SysPy = $Matches[1]
            break
        }
    }
    if (-not $SysPy) {
        throw "нет Python $PyVersion — поставить: winget install Python.Python.$PyVersion"
    }
    if (Test-Path $Venv) {
        Write-Host "-> окружение на Python $Have, пересоздаю на $PyVersion..."
        Remove-Item $Venv -Recurse -Force
    } else {
        Write-Host '-> создаю окружение...'
    }
    & $SysPy -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "venv на $SysPy вернул $LASTEXITCODE" }
    $VenvCreated = $true
}
$Py = Join-Path $Venv 'Scripts\python.exe'
if ($VenvCreated) {
    & $Py -m pip install -q --disable-pip-version-check --upgrade pip
}
$ReqFile = Join-Path $Root 'requirements.txt'
$ReqHash = (Get-FileHash $ReqFile -Algorithm SHA256).Hash
$ReqStamp = Join-Path $Venv 'requirements.sha256'
$HaveReq = if (Test-Path $ReqStamp) { (Get-Content $ReqStamp -Raw).Trim() } else { '' }
if ($HaveReq -eq $ReqHash) {
    Write-Host '-> зависимости не изменились, pip пропущен'
} else {
    & $Py -m pip install -q --disable-pip-version-check -r $ReqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install вернул $LASTEXITCODE" }
    Set-Content $ReqStamp $ReqHash -Encoding Ascii
}
# PyInstaller нужен только сборке, поэтому он не в requirements.txt.
$PyInstallerVersion = '6.22.3'
$HavePyInstaller = ''
try { $HavePyInstaller = (& $Py -c 'import PyInstaller; print(PyInstaller.__version__)' 2>$null) } catch { }
if ("$HavePyInstaller".Trim() -ne $PyInstallerVersion) {
    & $Py -m pip install -q --disable-pip-version-check "pyinstaller==$PyInstallerVersion"
    if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller вернул $LASTEXITCODE" }
}

# --- Значок и картинки мастера: всё из lib\tunnelvpn\icon.py в installer\gen.
$Gen = Join-Path $Inst 'gen'
Add-Type -AssemblyName System.Drawing
# Списки и размеры — те же, что WIZARD и WIZARD_SMALL в make_icon.py и
# WizardImageFile/WizardSmallImageFile в tunnelvpn.iss: пропал масштаб —
# Inno растянет соседний, и при 150–250 % картинка поплывёт.
$WizardFiles = @('202x386', '269x515', '336x643', '403x772', '430x824', '534x1022' | ForEach-Object { "wizard-$_.png" }) +
               @(58, 77, 97, 116, 124, 159 | ForEach-Object { "wizard-small-$_.png" })
$GenSources = @((Join-Path $Inst 'make_icon.py'), (Join-Path $Root 'lib\tunnelvpn\icon.py'))
$GenFresh = $true
foreach ($name in @('TunnelVPN.ico') + $WizardFiles) {
    $file = Join-Path $Gen $name
    if (-not (Test-Path $file)) { $GenFresh = $false; break }
    $stamp = (Get-Item $file).LastWriteTime
    foreach ($src in $GenSources) {
        if ($stamp -lt (Get-Item $src).LastWriteTime) { $GenFresh = $false }
    }
}
if ($Fast -and $GenFresh) {
    Write-Host '-> картинки не изменились, make_icon.py пропущен'
} else {
    & $Py (Join-Path $Inst 'make_icon.py') $Gen
    if ($LASTEXITCODE -ne 0) { throw "make_icon.py вернул $LASTEXITCODE" }
}
$WizardBytes = 0
foreach ($name in $WizardFiles) {
    $file = Join-Path $Gen $name
    if (-not (Test-Path $file)) { throw "нет картинки мастера $name" }
    $img = [System.Drawing.Image]::FromFile($file)
    try { $size = "$($img.Width)x$($img.Height)" } finally { $img.Dispose() }
    $want = if ($name -match '(\d+x\d+)') { $Matches[1] } elseif ($name -match '(\d+)') { "$($Matches[1])x$($Matches[1])" }
    if ($size -ne $want) { throw "$name — $size, ждали $want" }
    $WizardBytes += (Get-Item $file).Length
}
# Ориентир: установщик не должен заметно вырасти из-за картинок.
if ($WizardBytes -gt 300KB) { throw "картинки мастера — $([math]::Round($WizardBytes / 1KB)) КБ, больше 300" }
# ico: каталог в начале файла — число картинок и сторона каждой (0 — 256).
$Ico = [IO.File]::ReadAllBytes((Join-Path $Gen 'TunnelVPN.ico'))
$IcoSizes = for ($i = 0; $i -lt [BitConverter]::ToUInt16($Ico, 4); $i++) {
    $s = $Ico[6 + 16 * $i]; if ($s -eq 0) { 256 } else { $s }
}
# Те же, что icon.SIZES.
foreach ($s in 16, 20, 24, 32, 40, 48, 64, 128, 256) {
    if ($IcoSizes -notcontains $s) { throw "в TunnelVPN.ico нет $s×$s (есть: $($IcoSizes -join ', '))" }
}

function Get-ArgLine([string[]]$Items) {
    ($Items | ForEach-Object {
        if ("$_" -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { "$_" }
    }) -join ' '
}

Push-Location $Root
try {
    $DistPath = Join-Path $Root 'dist'
    $SpecInstalled = Join-Path $Inst 'tunnelvpn.spec'
    $SpecPortable = Join-Path $Inst 'portable.spec'
    if ($Fast) {
        Write-Host '-> собираю exe (установочные)...'
        & $Py -m PyInstaller --noconfirm --distpath $DistPath --workpath (Join-Path $Root 'build') $SpecInstalled
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller вернул $LASTEXITCODE" }
    } else {
        Write-Host '-> собираю exe (установочные + портативный, параллельно)...'
        $WorkInstalled = Join-Path $Root 'build\installed'
        $WorkPortable = Join-Path $Root 'build\portable'
        foreach ($wp in $WorkInstalled, $WorkPortable) {
            if (Test-Path $wp) { Remove-Item $wp -Recurse -Force }
        }
        $LogDir = Join-Path $Root 'build'
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        $InstalledOut = Join-Path $LogDir 'pyinstaller-installed.log'
        $InstalledErr = Join-Path $LogDir 'pyinstaller-installed.err'
        $PortableOut = Join-Path $LogDir 'pyinstaller-portable.log'
        $PortableErr = Join-Path $LogDir 'pyinstaller-portable.err'
        $Common = @('-m', 'PyInstaller', '--noconfirm', '--distpath', $DistPath)
        $procs = foreach ($spec in @(
                @((Get-ArgLine ($Common + @('--workpath', $WorkInstalled, $SpecInstalled))), $InstalledOut, $InstalledErr),
                @((Get-ArgLine ($Common + @('--workpath', $WorkPortable, $SpecPortable))), $PortableOut, $PortableErr))) {
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = $Py
            $psi.Arguments = $spec[0]
            $psi.WorkingDirectory = $Root
            $psi.UseShellExecute = $false
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $proc = New-Object System.Diagnostics.Process
            $proc.StartInfo = $psi
            [void]$proc.Start()
            [pscustomobject]@{
                Process = $proc
                Out = $proc.StandardOutput.ReadToEndAsync()
                Err = $proc.StandardError.ReadToEndAsync()
                OutFile = $spec[1]
                ErrFile = $spec[2]
            }
        }
        foreach ($p in $procs) { $p.Process.WaitForExit() }
        foreach ($p in $procs) {
            [IO.File]::WriteAllText($p.OutFile, $p.Out.Result)
            [IO.File]::WriteAllText($p.ErrFile, $p.Err.Result)
        }
        $Failed = $procs | Where-Object { $_.Process.ExitCode -ne 0 }
        if ($Failed) {
            $f = $Failed[0]
            throw "PyInstaller вернул $($f.Process.ExitCode), журнал: $($f.ErrFile)"
        }
    }

    # Бинарники кладём рядом с exe: paths.BIN у обычной сборки — это папка
    # приложения, и sing-box ищет wintun.dll в своём каталоге.
    # Портативному это не нужно: у него оба файла уже внутри.
    $Dist = Join-Path $Root 'dist\TunnelVPN'
    Copy-Item (Join-Path $Bin 'sing-box.exe') $Dist -Force
    Copy-Item (Join-Path $Bin 'wintun.dll') $Dist -Force
    Copy-Item (Join-Path $Bin 'happwn.dll') $Dist -Force

    # До дымовых проверок: они гоняют уже подписанные exe.
    if ($SignTool) {
        Write-Host '-> подписываю exe...'
        $SignTargets = @((Join-Path $Dist 'tunnelvpn.exe'), (Join-Path $Dist 'TunnelVPN-Tray.exe'))
        if (-not $Fast) { $SignTargets += (Join-Path $Root 'dist\TunnelVPN-Portable.exe') }
        Sign-Files @SignTargets
    }

    # Дымовая проверка: установщик зовёт tunnelvpn.exe для службы, и если под
    # этим именем окажется не CLI (так было, когда трей назывался DualVPN.exe
    # и ложился поверх), установка зависает намертво. Лучше упасть здесь.
    $Cli = Join-Path $Dist 'tunnelvpn.exe'
    $p = Start-Process $Cli -ArgumentList 'version' -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Root 'build\cli-version.txt')
    if (-not $p.WaitForExit(30000)) {
        Stop-Process -Id $p.Id -Force
        throw 'tunnelvpn.exe version не ответил за 30 с — под этим именем не CLI?'
    }
    $out = Get-Content (Join-Path $Root 'build\cli-version.txt') -Raw
    if ($out -notmatch [regex]::Escape($Version)) {
        throw "tunnelvpn.exe version: ждали $Version, получили: $out"
    }
    # Служба ищет sing-box.exe и wintun.dll там же, где их видит version.
    # Раз они уже не там, где их кладёт сборка, — установленная версия не
    # поднимет туннель, хотя всё остальное в ней работает.
    if ($out -match 'MISSING') {
        throw "tunnelvpn.exe не находит бинарники:`n$out"
    }
    if (-not (Test-Path (Join-Path $Dist 'TunnelVPN-Tray.exe'))) {
        throw 'нет TunnelVPN-Tray.exe в dist\TunnelVPN'
    }
    # Ресурс версии собирает tunnelvpn.spec. Пустое описание — и диспетчер
    # задач снова покажет вместо «TunnelVPN» имя файла.
    $CheckedExe = @((Join-Path $Dist 'tunnelvpn.exe'), (Join-Path $Dist 'TunnelVPN-Tray.exe'))
    if (-not $Fast) { $CheckedExe += (Join-Path $Root 'dist\TunnelVPN-Portable.exe') }
    foreach ($exe in $CheckedExe) {
        $desc = (Get-Item $exe).VersionInfo.FileDescription
        if (-not $desc) { throw "у $exe нет FileDescription — ресурс версии не собрался" }
    }
} finally {
    Pop-Location
}

$Portable = Join-Path $Root 'dist\TunnelVPN-Portable.exe'
if ((Test-Path $Portable) -and -not $Fast) {
    $mb = [math]::Round((Get-Item $Portable).Length / 1MB)
    Write-Host "-> портативный: dist\TunnelVPN-Portable.exe ($mb МБ)"
}

# --- Установщик
$Iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    # winget ставит Inno Setup только для пользователя, без прав
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    Write-Host '-> Inno Setup не найден, собраны только exe в dist\'
    Write-Host '   поставить: winget install JRSoftware.InnoSetup'
    exit 0
}

$IsccArgs = @("/DMyVersion=$Version")
if ($Fast) { $IsccArgs += '/DBUILDFAST' }
if ($SignTool) {
    # Inno подписывает сам setup и деинсталлятор внутри него (SignTool=tvpn в
    # tunnelvpn.iss). $q — кавычка, $f — подписываемый файл.
    $IsccArgs += '/DSign'
    $IsccArgs += '/Stvpn=$q' + $SignTool + '$q ' + $SignArgs + ' $f'
}
$SetupName = "TunnelVPN-$Version-setup.exe"
if ($TestHooks) {
    $IsccArgs += '/DTESTHOOKS'
    $SetupName = "TunnelVPN-$Version-testhooks-setup.exe"
}
& $Iscc @IsccArgs (Join-Path $Inst 'tunnelvpn.iss')
if ($LASTEXITCODE -ne 0) { throw "Inno Setup вернул $LASTEXITCODE" }
$Setup = Join-Path $Inst "Output\$SetupName"
# Издатель и продукт setup.exe (VersionInfo* в tunnelvpn.iss) — те же, что в
# ресурсе версии exe (tunnelvpn.spec): окно UAC и «Приложения» не расходятся.
$SetupInfo = (Get-Item $Setup).VersionInfo
$AppInfo = (Get-Item (Join-Path $Root 'dist\TunnelVPN\tunnelvpn.exe')).VersionInfo
# Строки ресурса setup.exe Inno добивает пробелами до длины поля.
foreach ($field in 'CompanyName', 'ProductName') {
    $got = "$($SetupInfo.$field)".Trim()
    if ($got -ne $AppInfo.$field) {
        throw "setup.exe ${field}: «$got», у tunnelvpn.exe «$($AppInfo.$field)»"
    }
}
if (-not "$($SetupInfo.FileDescription)".Trim()) { throw 'у setup.exe нет FileDescription' }
# Метку TESTHOOKS в описание ставит tunnelvpn.iss под тем же define: хуки
# есть в установщике ровно тогда, когда она есть. Та же проверка — в CI.
$Hooked = "$($SetupInfo.FileDescription)" -match 'TESTHOOKS'
if ($Hooked -ne [bool]$TestHooks) {
    throw "$SetupName — хуки: $Hooked, ждали: $([bool]$TestHooks) (описание «$($SetupInfo.FileDescription)»)"
}
Write-Host "-> готово: installer\Output\$SetupName"
if ($TestHooks) {
    Write-Host '   тестовая сборка: /testhook=service,tunnel,webview2 — в README, «Сборка установщика»'
    exit 0
}

# --- Контрольные суммы к релизу, в формате sha256sum: проверить скачанное —
# «sha256sum -c SHA256SUMS.txt» или Get-FileHash.
$SumFiles = @($Setup)
if (-not $Fast) { $SumFiles += $Portable }
$Sums = foreach ($f in $SumFiles) {
    if (Test-Path $f) {
        '{0}  {1}' -f (Get-FileHash $f -Algorithm SHA256).Hash.ToLower(), (Split-Path $f -Leaf)
    }
}
$Sums | Set-Content (Join-Path $Inst 'Output\SHA256SUMS.txt') -Encoding Ascii
Write-Host '-> контрольные суммы: installer\Output\SHA256SUMS.txt'
