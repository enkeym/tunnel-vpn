# Updating the installed build

The setup stops the service and drops the tunnel, so the user's connection (and this session's API calls)
goes down. Update only silently, then put everything back in the same command. Elevation and paths:
[run.md](run.md). Exit codes and test hooks: README, «Установка».

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

## Release

A `v*` tag makes CI publish the release, and every installed copy offers it as an update: the tag goes last.

1. Build locally in the background: `installer/build.ps1` ([run.md](run.md)); `VERSION` still holds the old number.
2. Install the local `installer\Output\TunnelVPN-*-setup.exe` by the steps above.
3. Check every change of the release live on that build; something wrong → fix, back to step 1.
4. All good → commit `VERSION` as «Релиз X.Y.Z: …» (the body is what «Что нового» shows), tag `vX.Y.Z` on it,
   `git push origin main vX.Y.Z`.
5. CI green → install the release build from GitHub the same way.
