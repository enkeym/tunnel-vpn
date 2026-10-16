# Мост MCP-сервера windows-mcp для Claude Code: stdio ↔ http://127.0.0.1:8790/mcp.
#
# Claude Code запускает мост один раз, на старте сессии. Сервера на 8790 нет —
# mcp-remote сразу выходил, и инструменты появлялись только после /mcp → reconnect.
# Поэтому мост сам поднимает сервер (повышенно: окна трея и мастера повышенные,
# UIPI не даёт обычному процессу ими управлять) и ждёт порт.
#
# В stdout до mcp-remote — ни строки: это канал протокола MCP.
# Регистрация и остановка сервера: docs/dev/testing.md, «Windows-MCP».
param([Parameter(Mandatory = $true)][string]$Key)

$Port = 8790
$Tools = "App,Click,Move,Scroll,Shortcut,Snapshot,Screenshot,Type,Wait,WaitFor"
# Первый запуск uvx докачивает пакет — секунд двадцать.
$Wait = 60

# Из UNC-папки проекта cmd (npx.cmd) печатал бы предупреждение про UNC.
Set-Location $env:TEMP

function Test-Up {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

if (-not (Test-Up)) {
    Start-Process (Get-Command uvx).Source -Verb RunAs -WindowStyle Hidden `
        -ArgumentList "windows-mcp", "serve", "--tools", $Tools
    $deadline = (Get-Date).AddSeconds($Wait)
    while (-not (Test-Up) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
}

& npx.cmd -y mcp-remote@0.15.1 "http://127.0.0.1:$Port/mcp" --allow-http --header "Authorization:Bearer $Key"
