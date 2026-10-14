---
paths:
  - "lib/tunnelvpn/window.py"
  - "lib/tunnelvpn/ui/js/model.js"
  - "lib/tunnelvpn/ui/js/topbar.js"
  - "lib/tunnelvpn/ui/js/cards.js"
  - "lib/tunnelvpn/ui/app.css"
---

## Мост WebView2: экран
- `render` без службы: `lib/tunnelvpn/window.py:refresh` шлёт `no_service` и `daemon`
  (из `lib/tunnelvpn/window.py:_service_installed`), у портативной (`lib/tunnelvpn/portable.py:is_portable`,
  его же зовёт `lib/tunnelvpn/tray.py:_launch_window`) — `portable: true` без `daemon`. Страница:
  `noDaemon`/`noService` в `lib/tunnelvpn/ui/js/model.js:model` → `headOf` («Нужна установка» с командой
  `INSTALL_CMD` в `lib/tunnelvpn/ui/js/topbar.js`, иначе «Служба не отвечает»). Правишь одну сторону —
  экран установки ломается.
- «Служба не отвечает» (не портативная): `lib/tunnelvpn/ui/js/topbar.js:reviveService` (`#btn-revive`,
  `REVIVING` из `model.js` — он же заголовок «Перезапускаю службу…») → `request('restart_service')` →
  `lib/tunnelvpn/window.py:_restart_service` → `lib/tunnelvpn/settings.py:restart_service`, как самолечение
  трея; ответ `{ok}` — после свежего `render`, причина сбоя — в `window.log`. Мок — `restart_service` в
  `lib/tunnelvpn/ui/dev/mock-api.js` (`&revive=error`). Без ветки в `_dispatch` кнопка отвечает тостом «нет ответа».
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
- Адрес выхода: `exit_ip` из `lib/tunnelvpn/probe.py:_slow_exit` (через `service.py:_status`) → `TOP.ip`
  в `lib/tunnelvpn/ui/js/topbar.js:paintTop` (только при `up`, `setText` меняет текст лишь со сменой адреса — иначе
  опрос снимает выделение), `copyIp` копирует его же. Заголовок утечки (`exit_state === 'leak'`) адрес сам не пишет.
- Нехватка конфигов: `missing` в `lib/tunnelvpn/ui/js/model.js:model` — ни у одного туннеля нет конфига, как у сборки
  (`lib/tunnelvpn/buildconfig.py:build` пропускает туннель без конфига и падает только без всех).
- Правка конфига: `lib/tunnelvpn/window.py:_edit_conf` ищет туннель по id в `st["tunnels"]` из
  `lib/tunnelvpn/service.py:_status` и имя в его `confs` (`conf\` окну не прочесть — ACL) →
  `tunnels.conf_path(id, имя)`. Убрал поле из статуса — правка молча `failed`.
- Галочка «подробный журнал»: `st["log_level"]` из `lib/tunnelvpn/service.py:_status` → `settings.js`; изменение —
  `log_level` → `set-log-level` → `logLevelDone`.
