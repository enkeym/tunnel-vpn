# Ждёт, пока связь агента вернётся, — для одного сообщения с действием,
# роняющим туннель (щелчок в мастере, деинсталляторе): ответ агента не уходит
# в API, пока этот скрипт не завершится. Сначала до -Drop секунд ждёт, что
# маршрут 0.0.0.0/1 на tun* пропадёт (не пропал — действие туннель не тронуло),
# потом до -Back секунд — что вернётся и api.anthropic.com ответит не 403.
# Возвращать туннель — дело guard.ps1. ВАЖНО: файл в UTF-8 С BOM.
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File wait-link.ps1 [-Drop 30] [-Back 300]

param([int]$Drop = 30, [int]$Back = 300)

function TunnelUp {
    [bool](Get-NetRoute -DestinationPrefix '0.0.0.0/1' -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -like 'tun*' })
}

function ApiCode {
    try { (Invoke-WebRequest https://api.anthropic.com -UseBasicParsing -TimeoutSec 10).StatusCode }
    catch { if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 } }
}

$t0 = Get-Date
$end = $t0.AddSeconds($Drop)
while ((Get-Date) -lt $end -and (TunnelUp)) { Start-Sleep -Milliseconds 500 }
if (TunnelUp) { "туннель не пропадал за $Drop с; api $(ApiCode)"; exit 0 }
$down = Get-Date
"туннель пропал через $([int]($down - $t0).TotalSeconds) с"

$end = $down.AddSeconds($Back)
while ((Get-Date) -lt $end) {
    if (TunnelUp) {
        $code = ApiCode
        if ($code -ne 403 -and $code -ne 0) {
            "связь вернулась через $([math]::Round(((Get-Date) - $down).TotalSeconds, 1)) с; api $code"
            exit 0
        }
    }
    Start-Sleep 1
}
"связь не вернулась за $Back с: туннель $(TunnelUp), api $(ApiCode)"
exit 1
