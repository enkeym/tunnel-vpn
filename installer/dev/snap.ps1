# Собирает пробный мастер (probe.iss по умолчанию), запускает его и снимает
# окно: размеры в журнал, картинку в dev\screenshots. Только для разработки.
#
#   powershell -ExecutionPolicy Bypass -File installer\dev\snap.ps1 [-Script probe.iss] [-Name probe]
#
# ВАЖНО: UTF-8 с BOM, как build.ps1.

param([string]$Script = 'probe.iss', [string]$Name = 'probe', [int]$Pages = 1,
      [string[]]$SetupArgs = @())

$ErrorActionPreference = 'Stop'
$Dev = Split-Path -Parent $MyInvocation.MyCommand.Path
$Iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
# Exe с \\wsl.localhost Windows молча не запускает (сетевой путь) — сборка
# в локальный %TEMP%.
$Out = Join-Path $env:TEMP 'tvpn-probe'
& $Iscc /Q "/O$Out" (Join-Path $Dev $Script)
if ($LASTEXITCODE -ne 0) { throw "ISCC вернул $LASTEXITCODE" }

Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class W {
  [StructLayout(LayoutKind.Sequential)] public struct R { public int L, T, Rt, B; }
  [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c, string t);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
[W]::SetProcessDPIAware() | Out-Null

$Shots = Join-Path $Dev 'screenshots'
New-Item -ItemType Directory -Force $Shots | Out-Null
$p = if ($SetupArgs) { Start-Process (Join-Path $Out "$Name.exe") -ArgumentList $SetupArgs -PassThru }
     else { Start-Process (Join-Path $Out "$Name.exe") -PassThru }
try {
    $h = [IntPtr]::Zero
    for ($i = 0; $i -lt 40 -and $h -eq [IntPtr]::Zero; $i++) {
        Start-Sleep -Milliseconds 250
        # Окно мастера — у распакованного Setup (is-*.tmp\<имя>.tmp), не у загрузчика.
        $h = Get-Process | Where-Object { $_.Path -like '*\is-*.tmp\*' -and $_.MainWindowHandle -ne 0 } |
            Select-Object -First 1 -ExpandProperty MainWindowHandle
        if (-not $h) { $h = [IntPtr]::Zero }
    }
    if ($h -eq [IntPtr]::Zero) { throw 'окно мастера не появилось' }
    Start-Sleep -Milliseconds 800
    for ($n = 1; $n -le $Pages; $n++) {
        [W]::SetForegroundWindow($h) | Out-Null
        Start-Sleep -Milliseconds 400
        $r = New-Object W+R; [W]::GetWindowRect($h, [ref]$r) | Out-Null
        $c = New-Object W+R; [W]::GetClientRect($h, [ref]$c) | Out-Null
        $w = $r.Rt - $r.L; $ht = $r.B - $r.T
        "окно ${w}x${ht}, клиент $($c.Rt)x$($c.B), dpi $([W]::GetDpiForWindow($h))"
        $bmp = New-Object Drawing.Bitmap $w, $ht
        $g = [Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
        $bmp.Save((Join-Path $Shots "$Name-$n.png"))
        $g.Dispose(); $bmp.Dispose()
        if ($n -lt $Pages) {
            [Windows.Forms.SendKeys]::SendWait('{ENTER}') 2>$null
            Start-Sleep -Milliseconds 600
        }
    }
} finally {
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
    Get-Process | Where-Object { $_.Path -like '*\is-*.tmp\*' } | Stop-Process -Force -ErrorAction SilentlyContinue
}
