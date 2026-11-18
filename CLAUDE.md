# TunnelVPN

Windows app (service, tray, WebView2, sing-box), edited from WSL Ubuntu, run on Windows.

| Before you | Read |
| --- | --- |
| run anything: tests, CLI, tray, build, elevated commands | @docs/dev/run.md |
| check a change, write or run tests | [docs/dev/testing.md](docs/dev/testing.md) |
| check the window in Playwright: mock page, open a sheet, screenshots, console | [docs/dev/playwright.md](docs/dev/playwright.md) |
| install or update the build on this machine | [docs/dev/install.md](docs/dev/install.md) |
| release a version: `VERSION`, tag, push | [docs/dev/install.md](docs/dev/install.md), «Release» — build, install and check live before the tag |
| reach anything on Windows loopback: CDP window, `windows-mcp`, `playwright-cdp` | [docs/dev/testing.md](docs/dev/testing.md), «Agent network» first (in `awg-term` WSL does not reach them), then «Window over CDP» in [docs/dev/playwright.md](docs/dev/playwright.md) or «Windows-MCP» (the agent drives the desktop with it on its own; the server starts with the session) |

