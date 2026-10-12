---
paths:
  - "installer/tunnelvpn.iss"
  - "lib/tunnelvpn/paths.py"
  - "installer/entry_tray.py"
  - "installer/entry_portable.py"
---

## Один трей и окно трея: установщик
- Трея нет — ярлык без прав запускает задачу `"TunnelVPN Tray"` (`lib/tunnelvpn/settings.py:TRAY_TASK`, в трее —
  `lib/tunnelvpn/tray.py:_run_task` → `schtasks /Run`) ↔ имя `{#MyName} Tray` в `RegisterTrayTask`
  `installer/tunnelvpn.iss`, затем ждёт `TRAY_OPEN` `TASK_OPEN_WAIT`; UAC — только без задачи или без ответа трея.
  Разошлось имя — снова UAC на каждый запуск. Задачу без неё создаёт и
  `lib/tunnelvpn/settings.py:tray_task_command` (переключатель «Значок в трее при входе»): параметры — как в
  `RegisterTrayTask`, правишь одно — правь другое.
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
