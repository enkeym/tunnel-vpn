---
name: windows-shell
description: "PowerShell from WSL — quoting through bash, script files instead of nested -Command, elevated runs and their output, WMI filters, process lookups. Load before writing or running a powershell.exe command, a .ps1 script, or a Start-Process -Verb RunAs call."
---

# PowerShell from WSL

Which binary runs what, elevation and paths: `docs/dev/run.md`; the window,
CDP and the agent's network: `docs/dev/testing.md`. This file adds the
pitfalls those docs leave out.

## Quoting
- One flat statement → `powershell.exe -NoProfile -Command '<…>'`: bash single
  quotes outside, PowerShell double quotes inside, no `'` in the body.
- Nested quotes, a loop, an elevated call with arguments, or JS for CDP → a
  `.ps1` written with `Write` into `/tmp/<task>/`, run with `-File "$(wslpath
  -w /tmp/<task>/x.ps1)"`. A `ParserError` such as `Missing expression after
  unary operator` is a quoting failure — move to a file, don't re-escape.
- `.ps1` files UTF-8 with BOM, like `installer/build.ps1`: without the BOM
  Windows PowerShell reads Cyrillic as ANSI.

## WMI and processes
- `-Filter` is WQL: string values quoted inside — `-Filter "Name =
  'msedge.exe'"`; unquoted (`Name=msedge.exe`) fails with `Недопустимый
  запрос`. Inside `-Command '…'` that means a script file or
  `Where-Object Name -eq "msedge.exe"`.
- `CommandLine` of an elevated process is empty from a non-elevated shell —
  identify it by `ProcessId`, `ParentProcessId` or the executable path, or run
  the query elevated.

## Elevated runs
- `Start-Process … -Verb RunAs` returns at once; add `-Wait` (or `-PassThru`
  and `$p.WaitForExit()`) before reading anything the elevated process writes.
- The elevated process writes its result into a file it is given
  (`$env:TEMP\tvpn-<task>\…`); read it from WSL through `/mnt/c/…`, after the
  wait. "Cannot find path" on that file = it was read before the run ended or
  the run failed — check the exit code, not the path.
- A command that restarts the service or toggles a tunnel can drop the agent's
  link: `docs/dev/testing.md` «Agent network» first.
