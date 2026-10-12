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
  `{place, tunnel}` у `place_config`, `{tunnel, include?, exclude?, exclude_apps?}` (текст полей) у `save_rules`,
  `{name, <поля листа>}` у `export_rules`, без аргумента у `pick_app`, булево у `log_level`/`set_autostart`, `{tunnel, on, replace, seq}` у
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
  `showConf(tid, name, text)`, `askPlace`, `showRules`, `rulesSaved`, `rulesMoved`, `rulesFailed`, `fillRules`, `checkStart`,
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
  active, include, exclude, exclude_apps})` (`_RULE_KEYS`); `_save_rules` (`set-tunnel`) →
  `rulesSaved({include, exclude, exclude_apps, rejected, mode})`,
  `rulesMoved({tunnel, name, main})` (ушёл запасным: лист закрыт, затем `_apply`)
  или `rulesFailed({error, problems})` (`problems[] {field, text, line, token}` — `_locate` по `entry` службы;
  `field` — ключ поля `rules-<field>` в `lib/tunnelvpn/ui/js/rules.js`); `validate_rules` — возвратом `send` (`request`):
  `{fields: {include|exclude: routelist.check, exclude_apps: routelist.check_apps}, max, max_apps, fate}`
  (программы в `check-rules` не уходят); форма `check` ↔ `paintField`/`paintProblems`,
  `line` — номер строки по `\n` (`routelist.tokens`) ↔ `pickToken`. Мок — `mockCheck`/`mockCheckApps` в
  `lib/tunnelvpn/ui/dev/mock-api.js`;
  `_load_rules` → `fillRules` (`_rules_from_file`: `{include, exclude}`, `exclude_apps` — только если есть в файле,
  из JSON экспорта или `{text}` в первое поле); `_export_rules` пишет поля листа в JSON — тот же формат, что
  читает `_rules_from_file`. У туннеля `all` поля `exclude` и `exclude_apps` (`ruleKeys` в странице).
  «Добавить программу…» (`.rules-pick`) → `pick_app` → `lib/tunnelvpn/window.py:_pick_app` (проводник,
  `routelist.app`) → `appPicked(path)` дописывает строку в поле; записывает только «Сохранить».
- Правки туннелей → `lib/tunnelvpn/window.py:_apply`: op `apply` (`lib/tunnelvpn/service.py:_apply`) — служба
  сама решает, перезапускать процесс туннеля или всё, выключенный не включает; затем
  `refresh(full=True)`. Окно само `stop`/`start` не шлёт — иначе интернет падал бы на каждую правку.
  С `ask: true`: «всё» служба не делает, а отвечает `applied: "ask"` → `askFull()` (лист поверх любого
  открытого, в т. ч. «Туннелирования») → `send('apply_full')` + `PENDING` restart, как у «Перезапустить».
  `roleOf(t, m)` в `lib/tunnelvpn/ui/js/cards.js` — «остальное → «основной»/напрямую» по тем же
  `main`/`direct` из `lib/tunnelvpn/ui/js/model.js:model`, что заголовок.
