---
paths:
  - "lib/tunnelvpn/tunnels.py"
  - "lib/tunnelvpn/routelist.py"
  - "lib/tunnelvpn/paths.py"
---

## Формат и переезд (`conf\tunnels.json`)
- Формат пишет и проверяет только `lib/tunnelvpn/tunnels.py:validate` (`load`, `save`); записи списков
  `include`/`exclude` нормализует `lib/tunnelvpn/routelist.py:parse` (`1.2.3.4` → `1.2.3.4/32`, `*.x.su`
  оставляет звёздочку), `exclude_apps` — `lib/tunnelvpn/routelist.py:parse_apps` (полный путь к `.exe`,
  повторы без регистра). Сборка, которая будет читать записи, обязана понимать ровно эти формы.
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
- Файл `site.env` читают только `lib/tunnelvpn/paths.py:site_env` → `tunnels.migrate` (переезд). Команды
  `get-site`/`set-site` (отправителей с 5б нет, уйдут в шаге 6) — переходник в `tunnels.json`
  (`lib/tunnelvpn/service.py:_read_site`/`_set_site`):
  `CORP_DOMAINS` ↔ `include`, `SB_CORP_EXCLUDE` ↔ `exclude` первого туннеля «по списку»
  (`tunnels.by_kind(…, "corp")`), `SB_LOG_LEVEL` ↔ `log_level`; разбор текста — `paths.parse_env`.
- Правка `tunnels.json` через канал — `lib/tunnelvpn/service.py:_add_tunnel` (id `t<N>`, мимо занятых папок
  `conf\tunnels\`), `_set_tunnel` (списки текстом через `routelist.parse`, потолок `service.MAX_RULES`),
  `_remove_tunnel` (папка конфигов удаляется после записи), `_set_tunnel` ещё меняет `mode` (all →
  `_make_main`: нужен конфиг с `0.0.0.0/0`, прежний основной → list) и отказывает на дубль записи между
  включёнными туннелями «по списку» (выключенный не сверяется — его включение идёт заменой,
  `_set_enabled`/`_clashing`) (`_clash_of`/`_clash`: та же запись в «пускать» или пересечение с AllowedIPs/«пускать»
  другого, не вынесенное в «не пускать» ни одного из двух; текст называет туннель и его активный конфиг из
  `Core._in_use`); порядок в файле на маршрут не влияет (решение #16),
  `move-tunnel` убран; `_set_log_level`, `_set_active`. Туннель команды —
  `Core._target`: id, затем имя без регистра, без них — `kind`.
