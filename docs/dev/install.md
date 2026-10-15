# Updating the installed build

The setup stops the service and drops the tunnel, so the user's connection (and this session's API calls)
goes down. Update only silently, then put everything back in the same command. Elevation and paths:
[run.md](run.md). Exit codes and test hooks: README, «Установка».

Run it from a session in `awg-term` (`ip netns identify $$`, [testing.md](testing.md), «Agent network»): its link
does not ride the tunnel and survives the setup. Empty answer → this session's API calls and MCP servers go down
for the few seconds the service is stopped: hand the user the steps instead of running them. Sessions of other
projects outside `awg-term` drop the same way — the user is told before the setup starts.

1. Note `& "C:\Program Files\TunnelVPN\tunnelvpn.exe" status` first: was the tunnel `работает`? The first upgrade
   from DualVPN still has the old build: `& "C:\Program Files\DualVPN\dualvpn.exe" status`.
2. `$p = Start-Process '<setup.exe>' -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Verb RunAs -PassThru; $p.WaitForExit()`,
   via Bash `run_in_background` with output to `/tmp/`. Not `-Wait`: it waits for the whole process tree, and the
   setup ends by starting the tray, so the command never returns. Never bare `/SILENT` — that run once ended with
   the service removed and not reinstalled.
3. `sc.exe query TunnelVPN` must be `RUNNING`; `$p.ExitCode` (details in `%TEMP%\Setup Log *.txt`):
   10 — the service failed; 11 — the tunnel was up and did not come back, go to step 4; 12 — no WebView2,
   window will not open, service and tunnel fine; 7/8 — setup did not start, nothing changed (8 — reboot
   needed: ask the user). Service missing → rerun the setup's own steps elevated:
   `tunnelvpn.exe service install`, `sc.exe config TunnelVPN start= auto`,
   `sc.exe failure TunnelVPN reset= 86400 actions= restart/5000/restart/10000/restart/30000`, `tunnelvpn.exe service start`.
4. Tunnel was up → `tunnelvpn.exe start` elevated; restart the tray (`TunnelVPN-Tray.exe`, RunAs) if it is gone.
5. Confirm with `tunnelvpn.exe status`: `работает`, routes present, exit через туннель. After the first upgrade
   from DualVPN also: no `DualVPN` service, task `DualVPN Tray` or `C:\ProgramData\DualVPN`, and
   `C:\ProgramData\TunnelVPN\conf\tunnels.json` in place.

Exit codes change → update step 3, the README table and `lib/tunnelvpn/update.py` (`EXIT_*`) together.
