---
paths:
  - "lib/tunnelvpn/instance.py"
  - "lib/tunnelvpn/tray.py"
  - "lib/tunnelvpn/window.py"
  - "lib/tunnelvpn/cli.py"
  - "lib/tunnelvpn/portable.py"
  - "installer/entry_tray.py"
  - "installer/entry_portable.py"
  - "installer/tunnelvpn.iss"
  - "installer/tunnelvpn.spec"
---

## Один трей и окно трея
- Имена объектов ядра `TRAY_MUTEX`, `TRAY_OPEN`, `WINDOW_SHOW` в `lib/tunnelvpn/instance.py`: мьютекс и
  `TRAY_OPEN` — `lib/tunnelvpn/tray.py:already_running` (второй запуск) ↔ `lib/tunnelvpn/tray.py:_start_window`
  (слушатель); `WINDOW_SHOW` — `lib/tunnelvpn/tray.py:_open_window` ↔ `lib/tunnelvpn/window.py:open_window`.
  Разошлось имя — ярлык снова ставит второй значок или окно не показывается, а трей его убивает.
- Права трея: в манифесте `TunnelVPN-Tray.exe` (`installer/tunnelvpn.spec`) нет `uac_admin`, права поднимает
  `lib/tunnelvpn/tray.py:run` → `_relaunch_elevated`; без прав `already_running` только смотрит
  `instance.exists`, а `TRAY_OPEN` создаётся с `instance.USER_SIGNAL_SDDL`. Вернули `uac_admin` или сняли
  дескриптор — ярлык к живому трею снова спрашивает UAC или молча не открывает окно.
- Трея нет — ярлык без прав запускает задачу `"TunnelVPN Tray"` (`lib/tunnelvpn/settings.py:TRAY_TASK`, в трее —
  `lib/tunnelvpn/tray.py:_run_task` → `schtasks /Run`) ↔ имя `{#MyName} Tray` в `RegisterTrayTask`
  `installer/tunnelvpn.iss`, затем ждёт `TRAY_OPEN` `TASK_OPEN_WAIT`; UAC — только без задачи или без ответа трея.
  Разошлось имя — снова UAC на каждый запуск. Задачу без неё создаёт и
  `lib/tunnelvpn/settings.py:tray_task_command` (переключатель «Значок в трее при входе»): параметры — как в
  `RegisterTrayTask`, правишь одно — правь другое.
- Флаги окна `--resident`, `--hidden`: собирает `lib/tunnelvpn/tray.py:_launch_window`, разбирает
  `lib/tunnelvpn/cli.py:main` → `lib/tunnelvpn/window.py:open_window`. Без `--resident` крестик закрывает процесс.
- Порт DevTools: `installer/dev/cdp-window.ps1` ставит `TUNNELVPN_DEVTOOLS_PORT` ↔
  `lib/tunnelvpn/window.py:DEVTOOLS_PORT_ENV` → `_devtools_port` → `webview.settings["REMOTE_DEBUGGING_PORT"]`
  в `open_window`; тот же порт — `--cdp-endpoint` сервера playwright-cdp (`claude mcp`, local) и `docs/dev/testing.md`. Разошлось имя
  или порт — playwright-cdp не цепляется. Проверку `sys.frozen` убрали — сборка открывает порт любому.
- `--background` (только значок, прогрев окна): задача `RegisterTrayTask` в `installer/tunnelvpn.iss` →
  `installer/entry_tray.py`, `lib/tunnelvpn/cli.py:main`, `installer/entry_portable.py` → `lib/tunnelvpn/tray.py:run`.
  Пропал — при входе в систему поверх всего выскакивает панель.
- Установщик гасит всё в `StopEverything` (`installer/tunnelvpn.iss`): трей с деревом (`/T` — окно-потомок
  из `lib/tunnelvpn/tray.py:_launch_window`), потом служба, потом все `tunnelvpn.exe` (окно и служба под одним
  именем); то же по старым именам `{#Legacy*}`. Окно сменило exe или стало жить без трея — обновление и
  удаление снова встают на «файл занят».
- Переезд с DualVPN: `MigrateLegacy` в `PrepareToInstall` (`installer/tunnelvpn.iss`) переносит
  `ProgramData\DualVPN` → `TunnelVPN`, удаляет службу и задачу `DualVPN`, её папку (`InstallLocation` из
  `UninstKey`, если там `dualvpn.exe` — `IsLegacyDir`, иначе `Program Files\DualVPN`), ярлыки и
  `%LOCALAPPDATA%\DualVPN\WebView2-*`. NRPT и правило IPv6 снимает остановка старой службы (`StopEverything`);
  аварийно умершей — новая по `lib/tunnelvpn/winnet.py:LEGACY_NRPT_COMMENT`/`LEGACY_V6_RULE`. Портативная
  переезжает сама: `lib/tunnelvpn/portable.py:_adopt` (`LEGACY_DATA_NAME`, `LEGACY_LOCAL_NAME`) — поэтому
  установщик чистит в `%LOCALAPPDATA%\DualVPN` только WebView2. `UsePreviousAppDir=no`/`UsePreviousGroup=no`
  обязательны: `AppId` общий, без них программа осталась бы в `Program Files\DualVPN`. Свою прошлую папку
  берёт `DefaultDirName={code:DefaultAppDir}` по тому же `InstallLocation`; `{#LegacyCli}` разошёлся с
  exe старых сборок — `IsLegacyDir` перестанет узнавать DualVPN, и TunnelVPN встанет в её папку.
- `PrepareToInstall` (`installer/tunnelvpn.iss`) отказывает, пока у службы `DeleteFlag=1` в
  `HKLM\SYSTEM\CurrentControlSet\Services\TunnelVPN` (`ServicePendingDelete`): `service install` поверх
  помеченной падает с 1072 — служба остаётся `DISABLED`. Лечится только перезагрузкой.
- Служба ставится в `InstallService` (`installer/tunnelvpn.iss`, из `CurStepChanged`), каждый шаг — по коду выхода:
  `lib/tunnelvpn/service.py:handle_command_line` возвращает код `HandleCommandLine`, `lib/tunnelvpn/cli.py:main`
  отдаёт его наружу как 1. Снова `return 0` в `cli.py` — установка опять «удаётся» без службы. Не вышло —
  «Готово» перестраивается в страницу ошибки (`ShowServiceError`, причина — `ServiceError` фразой) и код
  выхода 10 (`GetCustomSetupExitCode`). «Повторить запуск» → `StartService` (та же часть `InstallService`) →
  при успехе `RestoreAfterUpdate`; меняешь этапы запуска — они общие.
- Отказ `PrepareToInstall` (`MigrateLegacy`, `ServicePendingDelete`) задаёт `PrepareError`/`PrepareTitle`/
  `PrepareDesc` → `ShowPrepareError` во втором `CurPageChanged(wpPreparing)`; `NeedsRestart` (пометка службы на
  удаление) — радиокнопки перезагрузки Inno и код 8. Новый отказ без `PrepareError` — заголовок «Подготовка».
- Права на данные — `icacls` в `[Run]` `installer/tunnelvpn.iss` по раскладке `lib/tunnelvpn/paths.py`: `conf\` и
  `state\run\` (`paths.RUN`, собранные конфиги с ключами) — только SYSTEM и администраторы, `state\` —
  ещё `*S-1-5-11:RX`. `state\run\` идёт после `state\` со своим `/inheritance:r`, иначе наследует RX.
  Новый каталог с секретами в `paths.py` без строки `icacls` — открыт всем вошедшим.
- Тихое обновление возвращает туннель по `"up": true` в status.json (`lib/tunnelvpn/probe.py:write_status`,
  `indent=1`) и не трогает его при флаге `AUTOSTART_FILE` (`lib/tunnelvpn/service.py`): `RestoreAfterUpdate`.
  Сменился формат или ключ — после обновления туннель молча остаётся выключенным.
- WebView2 Runtime для `lib/tunnelvpn/window.py` ставит `EnsureWebView2` (`installer/tunnelvpn.iss`, из
  `CurStepChanged` после `RestoreAfterUpdate`): нет `pv` в `EdgeUpdate\Clients\{F3017226-…}` (HKLM32 или HKCU) —
  загрузчик с fwlink, запуск только с подписью Microsoft (`SignedByMicrosoft`). Неудача не фатальна: окно
  «Повторить / Продолжить без него», затем `WebView2Missing` → код выхода 12; служба и трей работают.
- Итог `RestoreAfterUpdate` — `TunnelRestore`: `TunnelLost` → плашка ⚠ на «Готово» (`ShowFinished`) и код 11,
  `TunnelBack` → «● VPN снова включён». Коды `GetCustomSetupExitCode` (10 > 11 > 12) читает процедура обновления
  в `docs/dev/install.md` и таблица в README («Установка», «Тихо») — меняешь коды, правь обе. Их же разбирает
  `lib/tunnelvpn/update.py:outcome` (`EXIT_*`; сверка — `tests/test_update.py`).
- Обновление из окна (`lib/tunnelvpn/update.py`): `SETUP_NAME` ↔ `OutputBaseFilename` в `installer/tunnelvpn.iss`,
  `SUMS_NAME` ↔ `SHA256SUMS.txt` в `installer/build.ps1` и вложения релиза в `.github/workflows/build.yml`. Setup
  ждёт PowerShell вне дерева трея: `StopEverything` снимает `{#MyExe}` с `/T` и всё под `{#MyCli}` — ждущий в
  дереве трея или под именем `tunnelvpn.exe` умрёт, кода не будет. `TRAY_IMAGE` ↔ `{#MyExe}`. Подпись setup
  сверяется с подписью работающего exe по субъекту (`check_signature`) — сменили издателя в `Sign` → обновление
  из окна откажет до ручной установки.
- Тестовые хуки `TestHook('service'|'tunnel'|'webview2')` (`installer/tunnelvpn.iss`, только под `TESTHOOKS`) ↔
  таблица хуков в README («Сборка установщика»). Метка `TESTHOOKS` в `VersionInfoDescription` под тем же define ↔
  проверка `$Hooked` в `installer/build.ps1` и шаг «Без тестовых хуков» в `.github/workflows/build.yml`; имя
  `-testhooks-setup` в `OutputBaseFilename` ↔ `$SetupName` в `build.ps1`. Сменили метку в одном месте — тестовая
  сборка уходит в релиз незамеченной или релизная сборка падает на проверке.
- Удаление чистит только `WebView2-admin`/`-user` из `lib/tunnelvpn/window.py:_storage_path`, не весь
  `%LOCALAPPDATA%\TunnelVPN`: там же запасная папка данных `lib/tunnelvpn/portable.py`.
- Код выхода `READY_LOST` в `lib/tunnelvpn/window.py` ↔ `lib/tunnelvpn/tray.py:_watch_window`: по нему трей
  один раз перезапускает окно, у которого не поднялся WebView2.
- Холодное окно создаётся спрятанным и показывается на `send('ready')` из `lib/tunnelvpn/ui/index.html` →
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
- Метка версии вёрстки: `lib/tunnelvpn/window.py:_page_url` (`?v=` — самый свежий файл `ui/`, без `ui/dev`) ↔
  загрузчик в `lib/tunnelvpn/ui/index.html`, который передаёт `location.search` в `app.css` и `js/<раздел>.js`
  (список разделов — там же, `core` первым, `main` последним). Новый файл вёрстки без загрузчика не грузится;
  ресурс без метки WebView2 после обновления берёт из кэша. `ui/dev` (мок моста) исключает
  `installer/tunnelvpn.spec:ui_datas` и подключает загрузчик только при `?mock=` без pywebview.
- Геометрия значка: `lib/tunnelvpn/icon.py:FULL` ↔ копия `assets/icon.svg` (`icon.svg()`, пишет
  `installer/make_icon.py --svg`) ↔ `<symbol id="i-logo">` в `lib/tunnelvpn/ui/index.html` (те же `d`, цвета — токены).
  Сверяет `tests/test_icon.py`. Поправил одну сторону — значок окна и README расходятся с exe.
- Графика сборки — в `installer/gen` (не в гите): `installer/make_icon.py:write_all` пишет `TunnelVPN.ico`
  (`icon.SIZES`) и картинки мастера по спискам `WIZARD`/`WIZARD_SMALL` ↔ те же списки в проверке `installer/build.ps1`
  и `WizardImageFile`/`WizardSmallImageFile`/`SetupIconFile` в `installer/tunnelvpn.iss` ↔ `ICON` в
  `installer/tunnelvpn.spec`. Разошёлся список — сборка падает на проверке или Inno растягивает соседний масштаб.
- Значок трея под DPI: `lib/tunnelvpn/tray.py:run` → `_dpi_aware` до создания окон, сторона — `_tray_size`
  (`SM_CXSMICON`), HICON — `_load_icon` вместо `pystray._win32.Icon._assert_icon_handle` (свой подкласс в `run`).
  Сняли `_dpi_aware` — при 125–150 % значок снова 16 точек, растянутый оболочкой; вернули загрузку pystray —
  ico без 20 точек и `LR_DEFAULTSIZE`, мыло. Состояние — `_tray_state` по `busy`, `up`, `v6_leak`, `out`,
  `exit_state` и работающему основному (`_main_on`: `mode all`, `enabled`) статуса службы (порядок — как у
  заголовка окна). `out`/`exit_state` мимо основного разбирает `_away` (`block` → `blocked`, `icon.TRAY["blocked"]`;
  `direct` → `fallback`); новое состояние — ключ в `icon.TRAY`, его различимость без цвета проверяет `tests/test_icon.py`.
- Задачи установщика: имена `autorun`/`desktopicon` в `[Tasks]` `installer/tunnelvpn.iss` ↔ галочки своей
  страницы `CreateTasksPage` → `WizardSelectTasks` в `NextButtonClick` ↔ `WizardIsTaskSelected('autorun')` в
  `CurStepChanged` и `Tasks: desktopicon` в `[Icons]`. Стандартную страницу задач прячет `ShouldSkipPage`.
  `TasksPage` создаётся после `wpSelectTasks`: список задач Inno собирает, проходя её, — страница раньше неё
  читает и пишет пустой список, выбор теряется.
  Новая задача без галочки на `TasksPage` — пользователь её не увидит, останется значение по умолчанию.
- Удаление: `StopForUninstall` (`CurUninstallStepChanged`, `usUninstall`) повторяет первые шаги `[UninstallRun]`
  в том же порядке ради этапов в строке прогресса; `BeforeInstall` у `[UninstallRun]` Inno зовёт при установке,
  не при удалении (проверено пробником). Меняешь порядок или шаги в `[UninstallRun]` — меняй и здесь.
  Вопрос про конфиги (`AskPurgeData`) — там же, до файлов; удаляет `ProgramData\TunnelVPN` `usPostUninstall`.
  Тот же вопрос задаёт «Удалить» в меню обслуживания (`NextButtonClick` → `RunUninstaller(Purge)`).
