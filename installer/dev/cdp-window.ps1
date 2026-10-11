# Открывает окно из исходников с портом DevTools — к нему цепляется MCP-сервер
# playwright-cdp. Окно говорит с живой службой; установленный трей не трогает.
# Только для разработки: порт открыт любому локальному процессу.
#
#   powershell -ExecutionPolicy Bypass -File installer\dev\cdp-window.ps1 [-Port 9222]
#
# ВАЖНО: UTF-8 с BOM, как build.ps1.

param([int]$Port = 9222)

$ErrorActionPreference = 'Stop'
$Admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $Admin) {
    # Окно приложения работает с правами администратора, а повышенный процесс
    # не наследует переменные вызывающего — задаём их уже после UAC.
    Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Port', $Port)
    exit 0
}

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$env:TUNNELVPN_DEVTOOLS_PORT = $Port
# Своя папка данных WebView2: занятую браузером с другими опциями WebView2 не
# открывает, и окно осталось бы серым (window._storage_path).
$env:LOCALAPPDATA = Join-Path $env:TEMP 'tvpn-cdp'
New-Item -ItemType Directory -Force $env:LOCALAPPDATA | Out-Null
$env:PYTHONPATH = Join-Path $Root 'lib'
& python.exe (Join-Path $Root 'installer\entry_cli.py') window
