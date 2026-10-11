# Testing

Check in the fastest layer that can show the behaviour. No Windows Sandbox, VM or test bench: it was removed
after a session lost to reboots and crashes without one scenario run. Something no layer below reaches →
say so in one line and ask, do not build an environment. Running commands from WSL: [run.md](run.md).

## Layers

| What changed | How to check |
| --- | --- |
| Python logic: service, tunnels, update, settings, tray menu | `python.exe -m pytest tests -q`; one test: `python.exe -m pytest tests/<file>.py -q -k <name>` |
| Window: layout, texts, states, bridge calls | mock page in Playwright (below) |
| Window against the live service | the app window over CDP (below) — not set up yet |
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

The real window is WebView2 (pywebview `edgechromium`): it opens a DevTools port when its process has
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`. WSL runs in `networkingMode=mirrored`,
so `http://127.0.0.1:9222` from WSL reaches it. The `playwright` MCP server starts its own headless Chromium;
attaching needs a server started with `--cdp-endpoint http://127.0.0.1:9222`. Set the variable inside the
elevated command (run.md, «Administrator rights»). Never leave the port open in a build or an installed copy:
anyone local could drive the window.

## Texts

All user-facing text addresses the user as «вы»: window, tray, toasts, messages from Python, installer.
A raw exception never reaches a toast or a page: a human sentence there, the detail into the log.
