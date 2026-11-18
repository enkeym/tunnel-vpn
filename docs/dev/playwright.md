# Playwright

Window checks without Windows: the mock page, and the live window over CDP. Which layer a change needs —
[testing.md](testing.md); running commands from WSL — [run.md](run.md).

## Mock page

1. `python3 -m http.server -b 127.0.0.1 -d lib/tunnelvpn/ui 8765` via Bash `run_in_background`.
2. `browser_resize` 1040×720 (the window size), then `http://127.0.0.1:8765/index.html?mock=<scenario>`.
   Scenarios, `tab`, `open`, `tick`, `upd`, `outcome` — header of `lib/tunnelvpn/ui/dev/mock-api.js`.
3. Screenshots into `.playwright-mcp/<topic>-*.png`; console must show nothing but the `favicon.ico` 404.

No `playwright` in `claude mcp list` (new machine):
`claude mcp add -s local playwright -- "$(command -v playwright-mcp)" --headless --browser chromium --isolated --timeout-settle 200`.

`mock-api.js` repeats what `lib/tunnelvpn/window.py` returns: change a bridge answer or a text in Python →
change the fixture in the same commit. A changed user-facing string → `grep -rn` it in `tests/`.

## Open a sheet for a check

`?open=rules:<tunnel id>` opens the «Туннелирование» sheet; `?mock=rules` is the same with `rules:work`. The sheet
appears a moment after the first status render (the mock's `ready` → 30 ms answer + 50 ms), so a snapshot taken
right after `browser_navigate` still shows the closed page: wait for the sheet (a second snapshot or a poll for
`.sheet`) before judging it. If it stays closed, open it the way the user does — the tunnel's «Туннелирование: …»
button; «Отмена» closes it without saving.

The tabs exist only where the service allows switching (`switchable`): the main tunnel, a `pinned` «по списку» one,
or a «по списку» one no protocol has as its main. In `?mock=ok` that is the main tunnel «Личный» (`home`) alone:
`work` and `lab` open the sheet without tabs, so use `?open=rules:home` for a mode check.

The mode tabs are one `radiogroup` «Режим туннеля»; after switching, the sheet must stay open and the fields
change (`exclude` + `exclude_apps` ↔ `include` + `include_apps` + `exclude`). Check both directions.

## Window over CDP

1. `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w installer/dev/cdp-window.ps1)"` —
   asks UAC, opens the window from source against the live service with DevTools on `127.0.0.1:9222`.
   The installed tray and window stay untouched.
2. Wait for the port: `Get-NetTCPConnection -State Listen -LocalPort 9222` from `powershell.exe` (a few seconds
   after the script returns).
3. Drive it:
   - In `awg-term` — `installer/dev/cdp.ps1` from Windows:
     `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w installer/dev/cdp.ps1)" -EvalFile "$(wslpath -w /tmp/cdp/x.js)" -Shot "$(wslpath -w /tmp/cdp)\\s1.png"`.
     JS with quotes goes in a file (`-EvalFile`, written with `Write`), an async IIFE returning what to check;
     click by selector (`button.power[aria-label*="…"]`, `[data-tab="log"]`), read the PNG with `Read`. Hidden rows
     keep their `textContent`: filter on `hidden` and `.closest('[hidden]')`, not on `innerText`.
   - Or the `playwright-cdp` MCP server tools: it attaches on the first call, so window first.
   Screenshots and console — as in the mock page.
4. Done → close the window: `cdp.ps1 -Close` (asks UAC); by hand the cross ends the process (no `--resident`).
   Do not leave it open: its WebView2 dies later and shows as a black frame.

The port comes from `TUNNELVPN_DEVTOOLS_PORT`, read only when running from source (`window._devtools_port`):
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` does not open it, pywebview sets its own `AdditionalBrowserArguments`.
A build never opens the port: anyone local could drive the window. The script also gives the window its own
`LOCALAPPDATA`, so WebView2 gets a separate data folder.

No `playwright-cdp` in `claude mcp list` (new machine; Node on Windows):
`claude mcp add -s local playwright-cdp -- powershell.exe -NoProfile -Command 'Set-Location $env:TEMP; npx.cmd -y @playwright/mcp@0.0.81 --cdp-endpoint http://127.0.0.1:9222 --timeout-settle 200'`.

## Tabs and network

- Playwright works with the tabs of its own MCP client only: `browser_tabs` `action: "list"`, then `select` the
  one whose title and URL match the check; `(current)` is the selected one. A page open in Chrome but missing from
  the list is not attached to this client — ask the user to attach it, do not open a new tab.
- The mock server lives in the namespace and works as is; Windows loopback (CDP) needs `powershell.exe` — see
  «Agent network» in [testing.md](testing.md).
