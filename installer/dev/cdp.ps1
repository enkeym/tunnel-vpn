# Управляет окном из cdp-window.ps1 по CDP с самой Windows: из сетевого неймспейса
# агента (awg-term) loopback Windows не виден, и MCP playwright-cdp не подключается.
#
#   powershell -ExecutionPolicy Bypass -File installer\dev\cdp.ps1 -Eval '<выражение JS>'
#   ... -EvalFile <файл.js> -Shot <файл.png>   (Promise ждётся, результат — JSON)
#   ... -Close                                 (закрыть окно: UAC)
#
# Пути — Windows (wslpath -w). Выражение с кавычками — файлом: так не мешает экранирование.
# ВАЖНО: UTF-8 с BOM, как build.ps1.

param([string]$Eval = '', [string]$EvalFile = '', [string]$Shot = '', [switch]$Close, [int]$Port = 9222)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8

if ($Close) {
    # Порт держит msedgewebview2, его родитель — python.exe окна. Окно повышенное:
    # без прав его CommandLine не виден и Stop-Process отказывает.
    $owner = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).OwningProcess |
        Select-Object -Unique
    if (-not $owner) { 'окна нет'; exit 0 }
    $parent = (Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $owner }).ParentProcessId
    $proc = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $parent }
    if ($proc.Name -ne 'python.exe') { throw "порт $Port держит не окно из исходников (родитель: $($proc.Name))" }
    Start-Process taskkill.exe -ArgumentList '/F', '/T', '/PID', $parent -Verb RunAs -WindowStyle Hidden -Wait
    "закрыто: $parent"
    exit 0
}

if ($EvalFile) { $Eval = [IO.File]::ReadAllText($EvalFile) }
$page = (Invoke-RestMethod "http://127.0.0.1:$Port/json/list") | Where-Object { $_.type -eq 'page' } |
    Select-Object -First 1
$ws = [Net.WebSockets.ClientWebSocket]::new()
$ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).Wait()

function Invoke-Cdp($method, $params) {
    $msg = @{ id = 1; method = $method; params = $params } | ConvertTo-Json -Depth 10 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($msg)
    $ws.SendAsync([ArraySegment[byte]]::new($bytes), 'Text', $true, [Threading.CancellationToken]::None).Wait()
    $buf = New-Object byte[] 1048576
    while ($true) {
        $ms = [IO.MemoryStream]::new()
        do {
            $r = $ws.ReceiveAsync([ArraySegment[byte]]::new($buf), [Threading.CancellationToken]::None).Result
            $ms.Write($buf, 0, $r.Count)
        } while (-not $r.EndOfMessage)
        # События страницы пропускаем: ответ — сообщение с нашим id.
        $o = [Text.Encoding]::UTF8.GetString($ms.ToArray()) | ConvertFrom-Json
        if ($o.id -eq 1) { return $o }
    }
}

if ($Eval) {
    $o = Invoke-Cdp 'Runtime.evaluate' @{ expression = $Eval; returnByValue = $true; awaitPromise = $true }
    if ($o.result.exceptionDetails) { $o.result.exceptionDetails | ConvertTo-Json -Depth 6; exit 1 }
    $v = $o.result.result.value
    if ($v -is [string]) { $v } else { $v | ConvertTo-Json -Depth 8 }
}
if ($Shot) {
    $o = Invoke-Cdp 'Page.captureScreenshot' @{ format = 'png' }
    [IO.File]::WriteAllBytes($Shot, [Convert]::FromBase64String($o.result.data))
    "снимок: $Shot"
}
