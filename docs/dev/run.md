# Running from WSL

Code is edited from WSL Ubuntu but runs on Windows: call the Windows binaries that WSL interop puts in PATH.
Do not install Linux Python, pytest or sing-box.

## Commands

From the project root:

- Dependencies: `python.exe -m pip install -r requirements.txt`
- CLI: `python.exe installer/entry_cli.py <args>`; tray: `python.exe installer/entry_tray.py`.
  From source, `lib/` must be on `PYTHONPATH`, else `No module named 'tunnelvpn'`:
  `PYTHONPATH="$(wslpath -w lib)" WSLENV=PYTHONPATH/w python.exe installer/entry_tray.py`
- Build and installer: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w installer/build.ps1)"`
  (without `Bypass` the Windows execution policy refuses the script)
- Any other Windows command: `powershell.exe -NoProfile -Command '<command>'`
- Tests: [testing.md](testing.md).

## Administrator rights

- The tray, the CLI and every command touching the service, tunnel or routes run elevated, never as an
  unelevated user.
- Run elevated commands yourself, the user confirms the UAC prompt. Never hand the command back to the user:
  `powershell.exe -NoProfile -Command 'Start-Process <exe> -ArgumentList <args> -Verb RunAs -Wait'`
  (e.g. `Start-Process sc.exe -ArgumentList "start","TunnelVPN" -Verb RunAs -Wait`).
- An elevated process does not inherit variables set in the calling shell: set them inside the elevated
  command (`-Verb RunAs` on `powershell.exe -Command '$env:X = …; & …'`).

## Paths

- Windows sees the project as `\\wsl.localhost\Ubuntu\...`: call `python.exe` and `powershell.exe` directly
  (`cmd.exe` rejects a UNC working directory) and convert path arguments with `wslpath -w`.
- An exe on `\\wsl.localhost` does not start (network path): build or copy it to a local `%TEMP%` folder,
  as `installer/dev/snap.ps1` does.
