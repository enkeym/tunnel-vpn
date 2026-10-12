---
paths:
  - "lib/tunnelvpn/window.py"
  - "lib/tunnelvpn/ui/**"
---

## Мост WebView2
- Сообщения JS → Python: `send('<имя>', arg)` в `lib/tunnelvpn/ui/js/*.js` ↔ ветки
  `lib/tunnelvpn/window.py:_dispatch`. Имя без ветки молча возвращает `None` — кнопка не делает ничего.
  Форма `arg` — тоже договор: `{tunnel, name}` у `use_config`/`show_config`/`edit_config`/`test_config`
  (его же `_test_config` возвращает в `testDone`),
  `{tunnel, name, drop_tunnel}` у `del_config`, `{tunnel, name, to, drop_tunnel}` у `move_config`,
  `{tunnel, mode}` у `set_mode` (all — «всё остальное через этот туннель»; list у основного окно не шлёт —
  основной выключают кнопка ⏻ карточки (`.power`) и его ⋯ через `set_enabled`; карточки «Мой интернет» нет — прямой выход не конфиг), карточки одним списком в порядке
  `st["tunnels"]` (`.cards` в `#pane-tunnels`),
  id строкой у `del_tunnel`/`tunnel_rules`, `{}`|`{tunnel}`|`{tunnel, place: "replace"}` у `add_config`,
  `{place, tunnel}` у `place_config`, `{tunnel, include?, exclude?}` (текст полей) у `save_rules`,
  `{name, include, exclude}` у `export_rules`, булево у `log_level`/`set_autostart`, `{tunnel, on, replace, seq}` у
  `set_enabled` (любой туннель, основной тоже, на лету и при v6 основного; кнопка ⏻ карточки (`locked`) не видит
  живую правку — второй щелчок шлёт `set_enabled` посреди неё, `apply` службы ждёт замок `APPLY_WAIT`).
  `seq` — `FLIP_SEQ` в `lib/tunnelvpn/ui/js/cards.js:sendEnabled` (все отправители `set_enabled`) ↔
  `lib/tunnelvpn/window.py:_flip`: под `_flip_lock` нажатие не новее записанного для того же туннеля
  (`_flip_seq`) отбрасывается, `set-enabled` — внутри замка; `ready` обнуляет счёт. Отправитель без `seq`
  или запись `set-enabled` вне `_flip` — мост (поток на вызов) снова кладёт старое нажатие поверх нового.
  Работающий основной в окне — `main` в `lib/tunnelvpn/ui/js/model.js:model` (`mode all` и `enabled`, как
  `tray._main_on`): от него `direct` («Запасной выход»), `headOf`, `roleOf`, выход/IPv6/запасной в `settings.js`.
  Слот основного (любой `all`) — `cardMenu` «Запасным к» и `fateOf` в `rules.js`, как `tunnels.main_tunnel` в службе.
- Вызовы Python → JS: `lib/tunnelvpn/window.py:js` / `lib/tunnelvpn/window.py:jsn` с именем функции
  (`render`, `renderHowto`, `renderVersion`, `renderLogs`, `failed`, `closeSheet`,
  `showConf(tid, name, text)`, `showInfo`, `askPlace`, `showRules`, `rulesSaved`, `rulesMoved`, `rulesFailed`, `fillRules`, `checkStart`,
  `checkSides(st, ids)`, `checkDone`, `testDone({tunnel, name})`, `askSwap({tunnel, clash})`, `autostartDone`,
  `logLevelDone`, `showAbout`) ↔ одноимённые функции верхнего уровня в `lib/tunnelvpn/ui/js/*.js` (`testDone`, `askSwap` —
  `cards.js`). Переименовал функцию — ошибка уходит в `jserror`, на экране ничего.
- Статус для `render`/`checkSides` берут и отдают под `lib/tunnelvpn/window.py:Api._render_lock`: `refresh`
  (опрос, кнопки) и `lib/tunnelvpn/ipc.py:check_by_side` (`lock=`). Новый `js("render", st)` со статусом,
  взятым вне замка, снова даст старый статус поверх свежего.
- Вопрос о месте конфига: ответ `add-config` с `ask` → `lib/tunnelvpn/window.py:_send_config` держит
  `self._asked = (имя, текст)` и зовёт `askPlace({id, name, conf, file})` → кнопка шлёт `place_config` →
  `lib/tunnelvpn/window.py:_place_config` повторяет `add-config` с `place` без второго диалога.
- Лист «Туннелирование»: `lib/tunnelvpn/window.py:_tunnel_rules` (`get-tunnels`) → `showRules({id, name, mode,
  active, include, exclude})`; `_save_rules` (`set-tunnel`) → `rulesSaved({include, exclude, rejected, mode})`,
  `rulesMoved({tunnel, name, main})` (ушёл запасным: лист закрыт, затем `_apply`)
  или `rulesFailed({error, problems})` (`problems[] {field, text, line, token}` — `_locate` по `entry` службы;
  `field` — ключ поля `rules-<field>` в `lib/tunnelvpn/ui/js/rules.js`); `validate_rules` — возвратом `send` (`request`):
  `{fields: {include|exclude: routelist.check}, max, fate}`; форма `check` ↔ `paintField`/`paintProblems`,
  `line` — номер строки по `\n` (`routelist.tokens`) ↔ `pickToken`. Мок — `mockCheck` в `lib/tunnelvpn/ui/dev/mock-api.js`;
  `_load_rules` → `fillRules` (`_rules_from_file`: `{include, exclude}` из JSON экспорта или `{text}` в
  первое поле); `_export_rules` пишет `{include, exclude}` в JSON — тот же формат, что читает `_rules_from_file`.
  У туннеля `all` поле одно — `exclude` (`ruleKeys` в странице).
- Правки туннелей → `lib/tunnelvpn/window.py:_apply`: op `apply` (`lib/tunnelvpn/service.py:_apply`) — служба
  сама решает, перезапускать процесс туннеля или всё, выключенный не включает; затем
  `refresh(full=True)`. Окно само `stop`/`start` не шлёт — иначе интернет падал бы на каждую правку.
  С `ask: true`: «всё» служба не делает, а отвечает `applied: "ask"` → `askFull()` (лист поверх любого
  открытого, в т. ч. «Туннелирования») → `send('apply_full')` + `PENDING` restart, как у «Перезапустить».
  `roleOf(t, m)` в `lib/tunnelvpn/ui/js/cards.js` — «остальное → «основной»/напрямую» по тем же
  `main`/`direct` из `lib/tunnelvpn/ui/js/model.js:model`, что заголовок.
- `render` без службы: `lib/tunnelvpn/window.py:refresh` шлёт `no_service` и `daemon`
  (из `lib/tunnelvpn/window.py:_service_installed`), у портативной (`lib/tunnelvpn/portable.py:is_portable`,
  его же зовёт `lib/tunnelvpn/tray.py:_launch_window`) — `portable: true` без `daemon`. Страница:
  `noDaemon`/`noService` в `lib/tunnelvpn/ui/js/model.js:model` → `headOf` («Нужна установка» с командой
  `INSTALL_CMD` в `lib/tunnelvpn/ui/js/topbar.js`, иначе «Служба не отвечает»). Правишь одну сторону —
  экран установки ломается.
- Заголовок и проблемы: `lib/tunnelvpn/ui/js/model.js:headOf` — порядок веток как у
  `lib/tunnelvpn/tray.py:_tray_state`; `problemsOf` → пилюля и панель в `lib/tunnelvpn/ui/js/topbar.js`
  (`ACTS`: `tunnel_rules`, `add_config {tunnel}`, `showTab('log')`, `focusCard(id)` — из шага карточек).
  Кнопки: `start`/`stop`/`restart` + `PENDING` (`pend`, сброс — обработчик в `ON_FAILED` из `failed` в
  `core.js`; там же сброс `WANT` карточек); `askFull` зовёт `pend('restart')`. Кнопка VPN `#btn-toggle` — с подписью
  «Включить / Выключить»; её ореол и его дыхание в `app.css` общие с `.power` карточки (ровный — `::after`,
  у VPN цвета `--st` из `#top`; дыхание — `::before`, класс `working`). После замеченного
  подъёма VPN, пока ни один включённый туннель не ответил (`m.settling`, `model.js`; не дольше `PENDING_MS` из `topbar.js`), дышат обе — `paintButtons`
  и `paintCard`; дыхание у обеих одного цвета (`--text`); условие ответа — то же, что гасит `busy` у `cardState` (`t.check` и не `checking`). «Проверить все»
  (`checkAll`): VPN работает — `send('check')`, выключен — `testStart` выбранного конфига каждой плитки (`cards.js`);
  крутится только за свою проверку — `CHECKING && CHECK_BY_HAND` или `testingAny(s) && TEST_ALL`, а заблокирована
  при любой (`CHECKING || testingAny(s)`); `TESTING`, `TEST_ALL` — в `model.js`: `topbar.js` грузится раньше `cards.js`.
  Проверку после включения и при открытии окна показывает ↻ плиток: в `paintCard` он крутится, пока `cardState` даёт
  `busy` («проверяю…»). `checkStart/checkSides/checkDone` —
  в `model.js`. Вид карточки (`cardState`) и проблема того же туннеля (`problemsOf`) обязаны совпадать по виду:
  молчащий «по списку» — warn и там, и там.
- Ошибка запуска: `last_error` из `lib/tunnelvpn/service.py:_status` → `headOf` в
  `lib/tunnelvpn/ui/js/model.js`, заголовок «Не запускается».
- Карточки: `paintCards`/`paintCard` в `lib/tunnelvpn/ui/js/cards.js` по `st["tunnels"]` (`id`, `name`, `mode`,
  `active`, `confs`, `rules`, `full`, `awg`, `check`, `answer`, `checking`, `last`, `seq`, `tests`, `enabled`) и
  `exit_state`/`out` у основного; меню ⋮ (`cardMenu`, у запасного — `spareMenu`) — «Всё остальное через этот
  туннель»/«Запасным к» только при `full`, вопрос при удалении последнего — по `rules` (`dropAsk`),
  «Выключить»/«Включить» у всех (`flipEnabled`, только флаг), круглая кнопка ⏻ (`r.pow` → `tilePower`,
  `aria-pressed` по `enabled`, `working` пока `WANT` или VPN включается/выключается) — у любого
  туннеля с конфигом, нажать — только с выбранным (`confOf`). VPN выключен — `startOnly` шлёт
  `start_only {tunnel}` → `window.py:_dispatch` → `start-only` службы (`_do_start(only=…)`: флаги одной
  записью и start под одним замком, не поднялся — флаги как были). Выключен последний включённый при живом VPN — `toggleVpn` (stop), флаг остаётся: пустой набор
  `start` разбудил бы все (`_wake_all`). `cardState`: «Туннель выключен» — флаг
  туннеля, «ждёт включения VPN» — `st.up` ложный, «Не выбран конфиг» — без `active` при нескольких `confs`
  (`confOf`, как `tunnels.active_conf`). `focusCard(id)` зовут пилюля (`ACTS.card` в `topbar.js`) и трей: клик по туннелю в меню →
  `lib/tunnelvpn/tray.py:_open_window(focus)` → `_set_focus` пишет id в `paths.FOCUS_FILE` (без id — удаляет)
  и шлёт `WINDOW_SHOW` / запускает окно → `lib/tunnelvpn/window.py:Api._focus` (из `_show` и из `ready` у
  не спрятанного, после `refresh`) читает и удаляет файл → `js('focusCard', id)` (`FOCUS_ABOUT` → `showAbout`). Переименовал `focusCard` или
  `data-key` карточки — клик в трее молча открывает окно без прокрутки.
- Адрес выхода: `exit_ip` из `lib/tunnelvpn/probe.py:_slow_exit` (через `service.py:_status`) → `#exit-ip`
  в `render` (только при `up`, текст меняется лишь со сменой адреса — иначе опрос снимает выделение),
  `#btn-copy-ip` копирует его же. Заголовок утечки (`exit_state === 'leak'`) адрес сам не пишет.
- Нехватка конфигов: `missing` в `render` — ни у одного туннеля нет конфига, как у сборки
  (`lib/tunnelvpn/buildconfig.py:build` пропускает туннель без конфига и падает только без всех).
- Правка конфига: `lib/tunnelvpn/window.py:_edit_conf` ищет туннель по id в `st["tunnels"]` из
  `lib/tunnelvpn/service.py:_status` и имя в его `confs` (`conf\` окну не прочесть — ACL) →
  `tunnels.conf_path(id, имя)`. Убрал поле из статуса — правка молча `failed`.
- Галочка «подробный журнал»: `st["log_level"]` из `lib/tunnelvpn/service.py:_status` → `render`; изменение —
  `log_level` → `set-log-level` → `logLevelDone`.
- Переключатель «Kill-switch» (группа «Защита» в `lib/tunnelvpn/ui/js/settings.js`): `st["kill_switch"]` →
  `paintDebug`; изменение — `send('kill_switch', bool)` → `lib/tunnelvpn/window.py:_dispatch` → `set-kill-switch`
  → `killSwitchDone`; мок — `kill_switch` и сценарий `blocked` в `lib/tunnelvpn/ui/dev/mock-api.js`.
- Журнал: `lib/tunnelvpn/window.py:_push_logs` (на `refresh(full=True)` и на `send('logs')` — его шлёт
  `lib/tunnelvpn/ui/js/log.js` раз в 2 с, пока открыта вкладка) → `renderLogs(lines)`. Форма строки — из
  `lib/tunnelvpn/service.py:_tail_log`: метка `[<имя туннеля>] ` (= `Tunnel.title`, = `st.tunnels[].name` —
  по нему чип фильтра и цвет пилюли) перед строкой sing-box `+ZZZZ YYYY-MM-DD HH:MM:SS LEVEL [id] текст`
  (`LOG_LINE`/`LOG_MARK` в `log.js`). Сменил формат метки или штампа — пилюли, уровни и фильтры молча пропадут.
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
  `lib/tunnelvpn/cli.py:status_lines`, адреса скрыты); `open_logs`/`open_url` → `{ok, error?}` через explorer.exe
  (`_shell_open`), `open_url` — только `settings.repo_url`; `check_update` → `{ok, state: latest|update|error,
  latest, url, notes, install, downtime}` (`settings.check_update`: `/releases?per_page=30`, теги `RELEASE_TAG`,
  `notes` — `settings.plain_notes` из `body`, мок — `mock-api.js`; `install` — `_is_admin` и `update.can_install`,
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

## Уйдёт в шаге 6
- `lib/tunnelvpn/window.py:_parse_env`/`_format_env` окно больше не зовёт, держит `tests/test_site_env.py`.
  Op `get-site/set-site` (переходник в `tunnels.json`, `lib/tunnelvpn/service.py:_read_site`/`_set_site`)
  отправителей в окне нет; статус `split`, `profiles`, `corp`, `last`, `checking_*`, `corp_*` окно не читает.
