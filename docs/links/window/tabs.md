---
paths:
  - "lib/tunnelvpn/window.py"
  - "lib/tunnelvpn/ui/js/settings.js"
  - "lib/tunnelvpn/ui/js/log.js"
  - "lib/tunnelvpn/ui/js/sheets.js"
  - "lib/tunnelvpn/ui/dev/**"
---

## Мост WebView2: вкладки и листы
- Переключатель «Kill-switch» (группа «Защита» в `lib/tunnelvpn/ui/js/settings.js`): `st["kill_switch"]` →
  `paintDebug`; изменение — `send('kill_switch', bool)` → `lib/tunnelvpn/window.py:_dispatch` → `set-kill-switch`
  → `killSwitchDone`; мок — `kill_switch` и сценарий `blocked` в `lib/tunnelvpn/ui/dev/mock-api.js`.
- Журнал: `lib/tunnelvpn/window.py:_push_logs` (на `refresh(full=True)` и на `send('logs')` — его шлёт
  `lib/tunnelvpn/ui/js/log.js` раз в 2 с, пока открыта вкладка) → `renderLogs(lines)`. Форма строки — из
  `lib/tunnelvpn/service.py:_tail_log`: метка `[<имя туннеля>] ` (= `Side.title`, = `st.tunnels[].name` —
  по нему чип фильтра и цвет пилюли; после замены .conf имя обновляет `lib/tunnelvpn/tunnel.py:reload_sides`)
  перед строкой sing-box `+ZZZZ YYYY-MM-DD HH:MM:SS LEVEL [id] текст` (`LOG_LINE`/`LOG_MARK` в `log.js`). Сменил формат метки или штампа — пилюли, уровни и фильтры молча пропадут.
- «Сейчас подключено»: `lib/tunnelvpn/window.py:_howto` берёт у службы op `howto` (`lib/tunnelvpn/service.py:_howto`,
  READ_OPS: `state\run\` окну не прочесть). Служба отдаёт `{endpoints: [{tag, address, mtu, awg, peer, dns, nets,
  domains}]}` по каждому включённому из `buildconfig.side_ids`: `dns` — `buildconfig.tunnel_dns`, `nets` —
  `buildconfig.tunnel_nets(cfg, id)`, `domains` — `buildconfig.tunnel_domains(cfg, id)`; окну без прав (`is_admin`
  канала) `nets`/`domains` пустые. ↔ `renderHowto` (имя туннеля — по тегу `wg-<id>` из `st["tunnels"]`).
- Вкладка «Настройки» (`lib/tunnelvpn/ui/js/settings.js`, в `parts` перед `main`): ответы возвратом `send` (`request`) —
  `settings_info` → `Api._settings_info` `{portable, admin, service: {installed, running, start}|null,
  tray_autostart: bool|null, data_dir, logs_dir, repo}` (null у портативной); `set_tray_autostart` (булево) →
  `{ok, tray_autostart, error?}` — `lib/tunnelvpn/settings.py:tray_task_command` (PowerShell, UAC через
  `_run_elevated`) меняет только триггер входа задачи `settings.TRAY_TASK` (её же `/Run` у ярлыка), без задачи —
  ставит её как `RegisterTrayTask` в `installer/tunnelvpn.iss` (правишь одну — правь другую); итог —
  `settings.tray_task` (XML `schtasks /Query`). `diagnostics` → `{ok, text}` (`settings.diagnostics` на
  `lib/tunnelvpn/cli.py:status_lines`, адреса скрыты; хвост `window.log` — `settings._window_tail`: `WINDOW_TAIL`
  строк из `paths.WINDOW_LOG` без IPv4, пишет его `lib/tunnelvpn/window.py:Api._log` в `%LOCALAPPDATA%\TunnelVPN`,
  не в `state\logs`: туда у пользователя нет записи, и `OSError` гасил журнал целиком; `open_logs` его не
  показывает); `open_logs`/`open_url` → `{ok, error?}` через explorer.exe
  (`_shell_open`), `open_url` — только `settings.repo_url`; `check_update` → `{ok, state: latest|update|error,
  latest, url, notes, install, downtime}` (`settings.check_update`: `/releases?per_page=30`, не ответил — `releases.atom`
  (`settings.feed_releases`), теги `RELEASE_TAG`, `notes` — `settings.plain_notes` из `body`, а `body` — сообщение коммита тега (шаг «Текст релиза»
  в `.github/workflows/build.yml`); `reason` ошибки
  `Api._check_update` пишет в `window.log` и со страницы убирает; TLS — `settings.tls_context` (корни Windows и
  certifi), его же берёт `update._open`; мок — `mock-api.js`; `install` — `_is_admin` и `update.can_install`,
  `downtime` — `update.DOWNTIME`). Обновление: `update_download` (версия — `Api._offer` из последней проверки) →
  поток `Api._download` → вызовы страницы `updateProgress({got, total})`, `updateStage('verify')`,
  `updateReady({version})`, `updateFailed({stage: download|verify|launch|cancel, text})`; `update_cancel`;
  `update_install` → `{ok, error?}` (`update.launch` с `Api._setup`); `update_outcome` → `{ok, pending}`
  (`update.pending`: `state installing|updated|tunnel|error`); `update_seen` → `update.clear`, кроме `installing`.
  Страница: эти вызовы и `watchUpdate` (`main.js` при старте, опрос `UPD_POLL_MS`) — в `settings.js`; состояние
  `installing`/`tunnel` — глобальный `UPD` в `model.js`: его читают `headOf`, `problemsOf`, `paintTop`/`paintButtons`
  (`topbar.js`) и `paintAbout`. `showAbout(notes)` — вкладка «Настройки» на «О программе»; пункт трея
  «Доступно обновление» (`lib/tunnelvpn/tray.py:on_about`) пишет в `paths.FOCUS_FILE` `window.FOCUS_ABOUT`
  вместо id туннеля → `Api._focus` → `js('showAbout')`. Трей читает `update.pending` сам: `installing` —
  жёлтый значок и «Обновляю TunnelVPN…» (`tray._installing`), метка при старте с `--background` — окно
  сразу, без прогрева (`Tray._start_window`). Мок: `?mock=update&upd=net|bad|link|error`, `&outcome=updated|tunnel|error|installing`.
  Переименовал вызов — страница молчит: `call` ловит отсутствие функции только в журнал окна.
- Листы конфига — `lib/tunnelvpn/ui/js/sheets.js` (в `parts` index.html после `cards`): `showConf`, `askPlace`,
  `askFull` зовёт Python; `cancel` берётся из `cards.js`. «Подробности»: `confDetails(t, name)` из `cardMenu`/
  `spareMenu` → `request('conf_details', {tunnel, name})` → `lib/tunnelvpn/window.py:_conf_details` (op
  `read-config`, UAC) → `_conf_summary` — ответ возвратом `send`, ключи `{ok, error | path, awg, endpoint,
  address[], dns[], allowed[], keepalive, mtu, mtu_file, cut, mask[[имя, значение]], problem}`. MTU сборки —
  копия правил `buildconfig.endpoint` (`AWG_MTU_MAX`, `AWG_JUNK`, умолчание 1280 из `buildconfig.build`):
  поменял там — поменяй и здесь. Время проверки — `st.tunnels[].tests[name].at`.
- Перетаскивание .conf: `lib/tunnelvpn/window.py:_listen_drop` (на `events.loaded`) подписывает
  `Api._drop` на `drop` документа — полный путь только там (`pywebviewFullPath`, pywebview ≥ 5 на WebView2);
  оверлей `DROP` и `preventDefault` у dragover/drop — `sheets.js`. Дальше `_add_file` → `_send_config`, как
  «Добавить конфиг…» без туннеля. Мок (`lib/tunnelvpn/ui/dev/mock-api.js`) на drop зовёт `add_config` сам.

## Мёртвое
- `lib/tunnelvpn/window.py:_parse_env`/`_format_env` окно больше не зовёт, держит `tests/test_site_env.py`.
  Ключей статуса `split`, `profiles`, `corp`, `last`, `checking_*` и команд `get-site`/`set-site`/`list-profiles`
  больше нет; `corp_dns`/`corp_ip`/`corp_http` окно не читает, их читает только CLI.
