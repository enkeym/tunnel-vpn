# Testing

Check in the fastest layer that can show the behaviour. No Windows Sandbox, VM or test bench: it was removed
after a session lost to reboots and crashes without one scenario run. Something no layer below reaches →
say so in one line and ask, do not build an environment. Running commands from WSL: [run.md](run.md).

## Layers

| What changed | How to check |
| --- | --- |
| Python logic: service, tunnels, update, settings, tray menu | `python.exe -m pytest tests -q`; one test: `python.exe -m pytest tests/<file>.py -q -k <name>` |
| Window: layout, texts, states, bridge calls | mock page in Playwright (below) |
| Window against the live service | the app window over CDP (below) |
| Real wizard, uninstaller, tray menu, toasts: clicks, texts, states | Windows-MCP (below) |
| Wizard and uninstaller pages: look, texts | `installer/dev/mock/index.html?page=<id>` in Playwright — [README](../../installer/dev/mock/README.md) |
| Real wizard window, one page | `installer/dev/snap.ps1 [-Script probe.iss] [-Pages N] [-SetupArgs …]` — builds, starts, screenshots into `installer/dev/screenshots/` |
| Silent install and update, exit codes, failure paths | real host by [install.md](install.md); failures — `build.ps1 -TestHooks`, then `/testhook=service,tunnel,webview2` (README, «Установка») |

## Window in Playwright

1. `python3 -m http.server -b 127.0.0.1 -d lib/tunnelvpn/ui 8765` via Bash `run_in_background`.
2. `browser_resize` 1040×720 (the window size), then `http://127.0.0.1:8765/index.html?mock=<scenario>`.
   Scenarios, `tab`, `open`, `tick`, `upd`, `outcome` — header of `lib/tunnelvpn/ui/dev/mock-api.js`.
3. Screenshots into `.playwright-mcp/<topic>-*.png`; console must show nothing but the `favicon.ico` 404.

`mock-api.js` repeats what `lib/tunnelvpn/window.py` returns: change a bridge answer or a text in Python →
change the fixture in the same commit. A changed user-facing string → `grep -rn` it in `tests/`.

## Window over CDP

1. `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w installer/dev/cdp-window.ps1)"` —
   asks UAC, opens the window from source against the live service with DevTools on `127.0.0.1:9222`.
   The installed tray and window stay untouched.
2. `curl -s http://127.0.0.1:9222/json/list` from WSL lists the `TunnelVPN` page (`networkingMode=mirrored`).
3. Drive it with the `playwright-cdp` MCP server tools: it attaches on the first call, so window first.
   Screenshots and console — as in the mock page.
4. Done → close the window: without `--resident` the cross ends the process.

The port comes from `TUNNELVPN_DEVTOOLS_PORT`, read only when running from source (`window._devtools_port`):
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` does not open it, pywebview sets its own `AdditionalBrowserArguments`.
A build never opens the port: anyone local could drive the window. The script also gives the window its own
`LOCALAPPDATA`, so WebView2 gets a separate data folder.

No `playwright-cdp` in `claude mcp list` (new machine):
`claude mcp add -s local playwright-cdp -- playwright-mcp --cdp-endpoint http://127.0.0.1:9222`.

## Windows-MCP: wizard and tray

UI Automation over real windows: Inno wizard, uninstaller, tray menu, toasts. The wizard, the tray and the
window run elevated, and UIPI keeps an unelevated process from driving them, so the server runs elevated over
HTTP on `127.0.0.1:8790` rather than over stdio.

1. Each session:
   `powershell.exe -NoProfile -Command 'Start-Process (Get-Command uvx).Source -ArgumentList "windows-mcp","serve","--tools","App,Click,Move,Scroll,Shortcut,Snapshot,Screenshot,Type,Wait,WaitFor" -Verb RunAs -WindowStyle Hidden'`.
   `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8790/mcp` → `401` (up, key required).
2. Server started after the Claude session → `/mcp` → reconnect `windows-mcp`, then its tools appear.
3. Only the UI tools: an elevated server with `PowerShell`, `Registry` or `FileSystem` on a local port is
   administrator rights for whoever holds the key. Commands still go through Bash.
4. Done → stop it, elevated: the owner of port 8790 and its `uvx.exe` parent —
   `powershell.exe -NoProfile -Command '$o = (Get-NetTCPConnection -State Listen -LocalPort 8790).OwningProcess | Select-Object -Unique; $p = (Get-CimInstance Win32_Process -Filter "ProcessId=$o").ParentProcessId; Start-Process powershell.exe -ArgumentList "-NoProfile","-Command","Stop-Process -Id $o,$p -Force" -Verb RunAs -WindowStyle Hidden -Wait'`.

Once per machine: `uvx windows-mcp auth --transport streamable-http --host 127.0.0.1 --port 8790` writes the key
into `%USERPROFILE%\.windows-mcp\config.toml`; then
`claude mcp add -s local --transport http windows-mcp http://127.0.0.1:8790/mcp --header "Authorization: Bearer <key>"`
— `/mcp` without the slash, `/mcp/` answers 307.

## Scenarios that drop the tunnel

The agent's link runs through the tunnel. Install, uninstall and update scenarios that drop it are checked by
the user by hand; the agent hands over a list of steps. When such a run is still needed with an agent attached
(the user drives, the agent watches), two scripts keep the link alive. Both are UTF-8 with BOM, like `build.ps1`.

`installer/dev/guard.ps1` — returns the tunnel while the wizard waits for a click without it (error pages,
⚠ on «Готово», the gap between uninstall and a new install).

1. Copy the script, the setup and `dist\TunnelVPN` (the bridge) to a local `%TEMP%` folder: nothing runs from
   `\\wsl.localhost`, and the installer kills `tunnelvpn.exe` in the install folder.
2. Start it elevated and hidden:
   `Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','<copy of guard.ps1>','-Work','<folder>','-Setup','<setup.exe>','-Bridge','<copy of dist\TunnelVPN>'`.
3. Parameters: `-Fast 10` — seconds without the tunnel while the installer is closed or idle (no child
   processes, CPU time not growing); `-Grace 90` — the wait while the installer is busy; `-Minutes 240` —
   lifetime; `-ConfBackup <folder>` — copied back into `%ProgramData%\TunnelVPN\conf` when `tunnels.json` is gone.
4. Order of recovery: service installed → start it and the tunnel; no service → bridge
   `tunnelvpn.exe service console` from `-Bridge`; neither and the wizard closed → silent install of `-Setup`.
5. `<Work>\guard.log` — journal, `<Work>\guard.alive` — time of the last loop, `<Work>\shots` — a screenshot
   every 5 seconds while the tunnel is down. Stop: create `<Work>\stop`.

`installer/dev/wait-link.ps1 [-Drop 30] [-Back 300]` — run in the same message as the click that drops the
tunnel, so the agent's reply does not reach the API before the link is back. Waits up to `-Drop` seconds for the `0.0.0.0/1` route on
`tun*` to vanish (it stays → exit 0, the click did not touch the tunnel), then up to `-Back` seconds for the
route and a non-403 answer from `api.anthropic.com`. Exit 1 — the link did not return; returning it is
`guard.ps1`'s job.

## Service by hand

What pytest cannot reach and what drops the link: the user runs it, the agent hands over these steps. The
journal is `%ProgramData%\TunnelVPN\state\logs\service.log` (window → «Журнал»). Main tunnel = the one in «основной».

| Change | Steps | Expect |
| --- | --- | --- |
| Start and ⏻ | «Выключить», «Включить»; ⏻ of a tunnel on the live VPN | «→ туннель поднят за» ≤ 2,3 с; ⏻ applies in under a second |
| Main `.conf` edited live | edit the active `.conf` of the main (e.g. `MTU`), save | «→ процесс «…» перезапущен»; no «→ выход наружу: direct», sites keep loading |
| Kill-switch | «Настройки → Защита» on; break the main (wrong `Endpoint` or block its server) | «→ выход наружу: block», header «Интернет закрыт»; sites do not open, tunnels «по списку» and LAN do; switch off → «→ выход наружу: direct» |
| Spare configs | main with ≥ 2 `.conf`, break the active one | «пробую запасные конфиги», «запасной X.conf отвечает — делаю его активным»; all broken → «запасные конфиги … не отвечают — выход остаётся block/direct», a new round once a minute |
| IPv6 of the main | main with a v6 `.conf` and one without; switch the config in the window | no full reconnect in the journal, open sites do not drop; without v6 `curl.exe -6 https://api6.ipify.org` fails at once (no hang), sites open over IPv4, the check reads «IPv6 без утечки» |

## Texts

All user-facing text addresses the user as «вы»: window, tray, toasts, messages from Python, installer.
A raw exception never reaches a toast or a page: a human sentence there, the detail into the log.
