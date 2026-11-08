---
paths:
  - "lib/tunnelvpn/tunnels.py"
  - "lib/tunnelvpn/routelist.py"
  - "lib/tunnelvpn/paths.py"
---

## Формат и переезд (`conf\tunnels.json`)
- Формат пишет и проверяет только `lib/tunnelvpn/tunnels.py:validate` (`load`, `save`); записи списков
  `include`/`exclude` нормализует `lib/tunnelvpn/routelist.py:parse` (`1.2.3.4` → `1.2.3.4/32`, `*.x.su`
  оставляет звёздочку), `include_apps`/`exclude_apps` — `lib/tunnelvpn/routelist.py:parse_apps` (полный путь к `.exe`,
  повторы без регистра; `tunnels._check_apps` параметризован ключом). Сборка, которая будет читать записи,
  обязана понимать ровно эти формы. `version` файла остаётся `1`: поле `include_apps` аддитивное, старому
  файлу без него `validate` подставляет пустой список.
- Файловый ввод-вывод `load`/`save` — под `lib/tunnelvpn/tunnels.py:LOCK` (RLock). Правка вне канала
  (`lib/tunnelvpn/tunnel.py:start` пишет профиль) берёт его сама вокруг свежего `load` → `save`, а не
  пишет `data`, прочитанный до долгого шага: иначе затрёт правку из окна.
- `lib/tunnelvpn/tunnels.py:migrate` читает ключи `site.env` (`CORP_DOMAINS` → `include` туннеля `work`,
  `SB_CORP_EXCLUDE` → `exclude`, `SB_LOG_LEVEL` → `log_level`) и `PROFILE_FILE` (→ `active` у `home`),
  переносит `conf\corp|personal` в `conf\tunnels\work|home`. Новый ключ `site.env`, который должен
  пережить переезд, — правка `migrate`. Id `work`/`home` — `tunnels.LEGACY`, на них опираются старые
  `kind` `corp`/`personal`. Без `site.env` и старых конфигов (свежая установка) — пустой `tunnels.empty()`:
  `by_kind` и `Core._target` по `kind` тогда отвечают «нет туннеля», окно шлёт авто-`add-config` без `kind`.
- Имена конфигов: `lib/tunnelvpn/tunnels.py:safe_name`/`check_name`, `buildconfig` их реэкспортирует.

## Сборка конфига: переезд и правка
- Переезд — `lib/tunnelvpn/service.py:_migrate` при старте службы: `buildconfig.migrate_flat` (плоский
  `conf\` → `conf\corp|personal`, профиль в `PROFILE_FILE`), затем `tunnels.migrate`. Порядок обязателен:
  второй шаг берёт то, что разложил первый. Каталоги — `lib/tunnelvpn/paths.py:ensure_dirs` (`CONF_TUNNELS`;
  `CONF_CORP`/`CONF_PERSONAL` остались только для переезда).
- Файл `site.env` читают только `lib/tunnelvpn/paths.py:site_env` → `tunnels.migrate` (переезд). Команд
  `get-site`/`set-site` больше нет.
- Правка `tunnels.json` через канал — `lib/tunnelvpn/service.py:_add_tunnel` (id `t<N>`, мимо занятых папок
  `conf\tunnels\`), `_set_tunnel` (списки текстом через `routelist.parse`, программы через `parse_apps`,
  потолок `service.MAX_RULES`),
  `_remove_tunnel` (папка конфигов удаляется после записи), `_set_tunnel` ещё меняет `mode` (all →
  `_make_main`: нужен конфиг с `0.0.0.0/0`, прежний основной → list с `pinned`; выключенный туннель основной не
  трогает — вкладка только запоминает режим, прежний основной меняется при включении) и отказывает на дубль записи между
  включёнными туннелями «по списку» (выключенный не сверяется — его включение идёт заменой,
  `_set_enabled`/`_clashing`) (`_clash_of`/`_clash`: та же запись в «пускать» или пересечение с AllowedIPs/«пускать»
  другого; «Мимо VPN» и «Программы мимо VPN» основного в сверке не участвуют; текст называет туннель и его
  активный конфиг из `Core._in_use`); порядок в файле на маршрут не влияет (решение #16),
  `move-tunnel` убран; `_set_log_level`, `_set_active`. Смена режима с листа
  (`set_mode`) шлёт только `mode` и `keep_enabled` (вкладка не включает выключенный туннель; пункт меню ⋮ без
  `keep_enabled` включает): `_idle_full` (пустые `include` и `include_apps`) тогда не срабатывает,
  авто-`spare`/`promoted` не запускаются — «Только выбранные» с пустыми списками легально. Ответ `set-tunnel`
  несёт `enabled`: выключенному окно не шлёт `apply`, правка берётся при включении. Туннель команды —
  `Core._target`: id, затем имя без регистра, без них — `kind`.
