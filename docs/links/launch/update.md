---
paths:
  - "installer/tunnelvpn.iss"
  - "installer/tunnelvpn.spec"
  - "lib/tunnelvpn/update.py"
  - "lib/tunnelvpn/icon.py"
  - "installer/make_icon.py"
---

## Один трей и окно трея: обновление и сборка
- Права трея: в манифесте `TunnelVPN-Tray.exe` (`installer/tunnelvpn.spec`) нет `uac_admin`, права поднимает
  `lib/tunnelvpn/tray.py:run` → `_relaunch_elevated`; без прав `already_running` только смотрит
  `instance.exists`, а `TRAY_OPEN` создаётся с `instance.USER_SIGNAL_SDDL`. Вернули `uac_admin` или сняли
  дескриптор — ярлык к живому трею снова спрашивает UAC или молча не открывает окно.
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
- Геометрия значка: `lib/tunnelvpn/icon.py:FULL` ↔ копия `assets/icon.svg` (`icon.svg()`, пишет
  `installer/make_icon.py --svg`) ↔ `<symbol id="i-logo">` в `lib/tunnelvpn/ui/index.html` (те же `d`, цвета — токены).
  Сверяет `tests/test_icon.py`. Поправил одну сторону — значок окна и README расходятся с exe.
- Графика сборки — в `installer/gen` (не в гите): `installer/make_icon.py:write_all` пишет `TunnelVPN.ico`
  (`icon.SIZES`) и картинки мастера по спискам `WIZARD`/`WIZARD_SMALL` ↔ те же списки в проверке `installer/build.ps1`
  и `WizardImageFile`/`WizardSmallImageFile`/`SetupIconFile` в `installer/tunnelvpn.iss` ↔ `ICON` в
  `installer/tunnelvpn.spec`. Разошёлся список — сборка падает на проверке или Inno растягивает соседний масштаб.
