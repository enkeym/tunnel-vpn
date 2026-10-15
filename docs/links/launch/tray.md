---
paths:
  - "lib/tunnelvpn/instance.py"
  - "lib/tunnelvpn/tray.py"
  - "lib/tunnelvpn/window.py"
  - "lib/tunnelvpn/cli.py"
  - "lib/tunnelvpn/portable.py"
---

## Один трей и окно трея
- Имена объектов ядра `TRAY_MUTEX`, `TRAY_OPEN`, `WINDOW_SHOW` в `lib/tunnelvpn/instance.py`: мьютекс и
  `TRAY_OPEN` — `lib/tunnelvpn/tray.py:already_running` (второй запуск) ↔ `lib/tunnelvpn/tray.py:_start_window`
  (слушатель); `WINDOW_SHOW` — `lib/tunnelvpn/tray.py:_open_window` ↔ `lib/tunnelvpn/window.py:open_window`.
  Разошлось имя — ярлык снова ставит второй значок или окно не показывается, а трей его убивает.
- Флаги окна `--resident`, `--hidden`: собирает `lib/tunnelvpn/tray.py:_launch_window`, разбирает
  `lib/tunnelvpn/cli.py:main` → `lib/tunnelvpn/window.py:open_window`. Без `--resident` крестик закрывает процесс.
- `--tray-pid` (`os.getpid()` трея): `lib/tunnelvpn/tray.py:_launch_window` → `lib/tunnelvpn/cli.py:main` →
  `lib/tunnelvpn/window.py:open_window` → `lib/tunnelvpn/instance.py:on_exit` (`os._exit(0)`, когда трей
  завершился). Не `getppid` окна: у портативной окно — дочерний бутлоадера. Потеряли флаг — окно, пережившее
  трей, остаётся спрятанной сиротой, а следующий трей открывает второе.
- Порт DevTools: `installer/dev/cdp-window.ps1` ставит `TUNNELVPN_DEVTOOLS_PORT` ↔
  `lib/tunnelvpn/window.py:DEVTOOLS_PORT_ENV` → `_devtools_port` → `webview.settings["REMOTE_DEBUGGING_PORT"]`
  в `open_window`; тот же порт — `--cdp-endpoint` сервера playwright-cdp (`claude mcp`, local) и `docs/dev/testing.md`. Разошлось имя
  или порт — playwright-cdp не цепляется. Проверку `sys.frozen` убрали — сборка открывает порт любому.
- Код выхода `READY_LOST` в `lib/tunnelvpn/window.py` ↔ `lib/tunnelvpn/tray.py:_watch_window`: по нему трей
  один раз перезапускает окно, у которого не поднялся WebView2.
- Холодное окно создаётся спрятанным и показывается на `send('ready')` из `lib/tunnelvpn/ui/js/main.js` →
  `lib/tunnelvpn/window.py:_dispatch` → `Api.reveal`, запасной таймер `COLD_SHOW_WAIT` в `open_window`;
  `_show` (сигнал `WINDOW_SHOW` от `lib/tunnelvpn/tray.py:_open_window`) показывает сразу. `ready` шлётся в конце
  скрипта страницы. Убрали `ready` или таймер — окно появляется пустым, с опозданием или не появляется.
- Спрятанное окно pywebview создаёт через `Opacity=0, Show, Hide` (`<site-packages>/webview/platforms/winforms.py:create_window`);
  этот показ уводит за край экрана `lib/tunnelvpn/window.py:_park` в `before_show` (синхронно, до `Show`), на
  место в центре форму возвращает `_unpark` из `Api.reveal` и `_show` по `api._home`. Показ бывает раньше
  `before_show` (сигнал трея, таймер `COLD_SHOW_WAIT`): `_unpark` ставит `api._show_wanted`, и `_park` после
  него не паркует; оба под `api._park_lock`. Сняли обработчик или pywebview перенёс `before_show` после
  `Show` — после UAC снова мелькает белое пред-окно; потеряли `_unpark` или флаг — окно показывается за краем
  экрана. `_home` с подчёркиванием: публичные атрибуты `js_api` pywebview обходит, .NET `Point` там — рекурсия.
- Передний план: `lib/tunnelvpn/tray.py:_open_window` → `instance.allow_foreground` даёт право, а `window.show()`
  pywebview (`Show` + `Activate`) его тратит; без права `Activate` молча не срабатывает, и окно встаёт под
  активным. Поэтому после каждого `window.show()` в `Api.reveal` и `_show` идёт `lib/tunnelvpn/window.py:_to_front`
  → `_raise` (TOPMOST/NOTOPMOST, затем `AttachThreadInput`). Новый путь показа без `_to_front` — окно снова
  бывает под редактором.
- Левый клик по значку: `WM_LBUTTONUP` в `lib/tunnelvpn/tray.py:_hook_menu` → `on_window`, мимо
  обработчика pystray (`<site-packages>/pystray/_win32.py:_on_notify`: на `WM_LBUTTONUP` он зовёт default-пункт меню, на
  `WM_LBUTTONDBLCLK` ничего не знает; двойной клик обёртка гасит — окно уже открыл первый). Обёртку
  потеряли или pystray поставил пункту `default=True` — клик молчит или включает VPN. Пункта «Открыть
  панель управления…» в `lib/tunnelvpn/tray.py:_menu` нет (шаг 5в): из трея окно открывает только этот клик.
  Повторный клик по открытому окну: `lib/tunnelvpn/window.py:_show` по `api.hidden`/`api.cold` только
  выводит его вперёд; флаги ставят `_hide_on_close`, `open_window`, `Api.reveal`. Сломали флаг — открытое
  окно на каждый клик перерисовывается и снова проверяет туннели.
- Правый клик по значку: `lib/tunnelvpn/tray.py:_hook_menu` → `_popup_menu` вместо ветки `WM_RBUTTONUP` из
  `<site-packages>/pystray/_win32.py:_on_notify` (тот же `SetForegroundWindow` + `TrackPopupMenuEx`, но меню по центру значка
  (`TPM_CENTERALIGN`), точка прижата к `rcWork` монитора: при открытом «Пуске» нижние пункты уходили под панель задач). Поля `icon._menu_handle`,
  `_menu_hwnd`, `_hwnd` — pystray; обновили pystray и они переименованы — меню не открывается.
- Метка версии вёрстки: `lib/tunnelvpn/window.py:_page_url` (`?v=` — самый свежий файл `ui/`, без `ui/dev`) ↔
  загрузчик в `lib/tunnelvpn/ui/index.html`, который передаёт `location.search` в `app.css` и `js/<раздел>.js`
  (список разделов — там же, `core` первым, `main` последним). Новый файл вёрстки без загрузчика не грузится;
  ресурс без метки WebView2 после обновления берёт из кэша. `ui/dev` (мок моста) исключает
  `installer/tunnelvpn.spec:ui_datas` и подключает загрузчик только при `?mock=` без pywebview.
- Значок трея под DPI: `lib/tunnelvpn/tray.py:run` → `_dpi_aware` до создания окон, сторона — `_tray_size`
  (`SM_CXSMICON`), HICON — `_load_icon` вместо `pystray._win32.Icon._assert_icon_handle` (свой подкласс в `run`).
  Сняли `_dpi_aware` — при 125–150 % значок снова 16 точек, растянутый оболочкой; вернули загрузку pystray —
  ico без 20 точек и `LR_DEFAULTSIZE`, мыло. Состояние — `_tray_state` по `busy`, `up`, `v6_leak`, `out`,
  `exit_state` и работающему основному (`_main_on`: `mode all`, `enabled`) статуса службы (порядок — как у
  заголовка окна). `out`/`exit_state` мимо основного разбирает `_away` (`block` → `blocked`, `icon.TRAY["blocked"]`;
  `direct` → `fallback`); новое состояние — ключ в `icon.TRAY`, его различимость без цвета проверяет `tests/test_icon.py`.
- Самолечение службы: `lib/tunnelvpn/tray.py:_poll_locked` → `_maybe_heal` (канал молчит `HEAL_AFTER`, не чаще
  `HEAL_EVERY`; мимо — `update.pending`, `portable.is_portable`, `settings.service_state` с `start: disabled`) →
  `_heal` → `lib/tunnelvpn/settings.py:restart_service`, итог — в `LOGS\tray.log`. Установщик и деинсталлятор
  снимают трей раньше службы (`installer/tunnelvpn.iss`: `KillImage` до `StopService`, `[UninstallRun]` `KillTray`
  до `StopService`). Поменяли порядок — живой трей через `HEAL_AFTER` запустит службу посреди установки или удаления.
