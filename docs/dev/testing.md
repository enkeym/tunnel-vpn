# Testing

Check in the fastest layer that can show the behaviour. No Windows Sandbox, VM or test bench: it was removed
after a session lost to reboots and crashes without one scenario run. Something no layer below reaches →
say so in one line and ask, do not build an environment. Running commands from WSL: [run.md](run.md).

## Agent network

`ip netns identify $$` → `awg-term`: the shell and every MCP server Claude starts live in a network namespace whose
traffic goes through `amnezia`, not TunnelVPN.

- `127.0.0.1` there is the namespace's own: Windows ports answer ECONNREFUSED to `curl` from the shell; check one
  from Windows: `powershell.exe -NoProfile -Command 'Get-NetTCPConnection -State Listen -LocalPort <port>'`.
- `powershell.exe`, `python.exe` and `wsl.exe` run outside it and reach Windows loopback. `.wslconfig`
  does not help: `mirrored` shares loopback with the root namespace only.
- The agent's link does not ride the tunnel: ⏻ of the main tunnel and «Выключить» keep it.
- The mock page (`http.server` + `playwright`) lives inside the namespace and works as is.
- Empty `ip netns identify` → the old setup: WSL loopback is Windows loopback (`networkingMode=mirrored`), MCP
  clients connect directly, and the agent's link rides the tunnel.

## Layers

| What changed | How to check |
| --- | --- |
| Python logic: service, tunnels, update, settings, tray menu | `python.exe -m pytest tests -q -n auto` (~20 s; `-n` needs `pip install pytest-xdist`, without it ~50 s); one test: `python.exe -m pytest tests/<file>.py -q -k <name>` |
| Window: layout, texts, states, bridge calls | mock page in Playwright — [playwright.md](playwright.md) |
| Wizard and uninstaller pages: look, texts | `installer/dev/mock/index.html?page=<id>` in Playwright — [README](../../installer/dev/mock/README.md) |
| Real wizard window, one page | `installer/dev/snap.ps1 [-Script probe.iss] [-Pages N] [-SetupArgs …]` — builds, starts, screenshots into `installer/dev/screenshots/` |
| Silent install and update, exit codes, failure paths | real host by [install.md](install.md); failures — `build.ps1 -TestHooks`, then `/testhook=service,tunnel,webview2` (README, «Установка») |

## Window in Playwright

Mock page moved to [playwright.md](playwright.md): the page, opening the
«Туннелирование» sheet for a check, screenshots, console, MCP registration.

## Scenarios that drop the tunnel

Install, uninstall and update scenarios that drop the tunnel are checked by the user by hand; the agent hands over
a list of steps. When such a run is still needed with an agent attached (the user drives, the agent watches), two
scripts help. Both are UTF-8 with BOM, like `build.ps1`. In `awg-term` the agent's link does not ride the tunnel:
`wait-link.ps1` is not needed, `guard.ps1` still returns the user's tunnel.

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
journal is `%ProgramData%\TunnelVPN\state\logs\service.log` (lines `YYYY-MM-DD HH:MM:SS.mmm …`; window →
«Журнал», as `[служба]`, `!!` lines under «Предупреждения»); past 4 MB the older lines move to `service.log.1`. Main tunnel = the one in «основной».

| Change | Steps | Expect |
| --- | --- | --- |
| Start and ⏻ | «Выключить», «Включить»; ⏻ of a tunnel on the live VPN | «→ туннель поднят за» ≤ 2,3 с; ⏻ of a «по списку» tunnel applies in about 2 s (waits for sing-box to take the rule-set, `RULE_SET_WAIT`) |
| Main `.conf` edited live | edit the active `.conf` of the main (e.g. `MTU`), save | «→ процесс «…» перезапущен»; no «→ выход наружу: direct», sites keep loading |
| Kill-switch | «Настройки → Защита» on; break the main (wrong `Endpoint` or block its server) | «→ выход наружу: block», header «Интернет закрыт»; sites do not open, tunnels «по списку» and LAN do; switch off → «→ выход наружу: direct» |
| Spare configs | main with ≥ 2 `.conf`, break the active one | «пробую запасные конфиги», «запасной X.conf отвечает — делаю его активным»; all broken → «запасные конфиги … не отвечают — выход остаётся block/direct», a new round once a minute |
| IPv6 of the main | main with a v6 `.conf` and one without; switch the config in the window | no full reconnect in the journal, open sites do not drop; without v6 `curl.exe -6 https://api6.ipify.org` fails at once (no hang), sites open over IPv4, the check reads «IPv6 без утечки» |

## Texts

All user-facing text addresses the user as «вы»: window, tray, toasts, messages from Python, installer.
A raw exception never reaches a toast or a page: a human sentence there, the detail into the log.
