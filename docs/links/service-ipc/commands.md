---
paths:
  - "lib/tunnelvpn/service.py"
  - "lib/tunnelvpn/ipc.py"
---

## Команды канала
- Имя команды (`op`) — строка: ветка в `lib/tunnelvpn/service.py:handle` ↔ отправители
  `lib/tunnelvpn/cli.py:main`, `lib/tunnelvpn/tray.py:_poll_locked`, `lib/tunnelvpn/tray.py:_check_work`,
  `lib/tunnelvpn/tray.py:_quit_work`, `lib/tunnelvpn/window.py:_dispatch`,
  `lib/tunnelvpn/window.py:refresh`, `lib/tunnelvpn/window.py:_check`. Переименовал ветку — отправитель
  получит «неизвестная команда», граф этого не видит.
- Права команды — `lib/tunnelvpn/ipc.py:READ_OPS` и `lib/tunnelvpn/ipc.py:USER_OPS` через
  `lib/tunnelvpn/ipc.py:requires_admin`. Новая ветка в `lib/tunnelvpn/service.py:handle` без записи
  туда закрыта для обычного пользователя: окно зовёт её через `lib/tunnelvpn/window.py:_admin_call` (UAC),
  трей и CLI — получат отказ.
- Туннель в payload — ключ `tunnel` (id или имя), запасной — `kind` (`corp`/`personal`) у `add-config`,
  `remove-config`, `read-config`; `set-active` (USER_OPS) ↔ `lib/tunnelvpn/cli.py:main` (`use`).
- `howto` (READ_OPS) ↔ `lib/tunnelvpn/window.py:_howto`: единственная команда, где ответ зависит от `is_admin`
  обработчика (`lib/tunnelvpn/service.py:_howto` — подсети и домены туннеля только администратору). Читает
  `state\run\` — закрытый каталог; в ответ не должны попасть ключи, пароли socks и секрет `clash_api`.
  `get-tunnels`, `add-tunnel`, `set-tunnel`, `remove-tunnel`, `set-log-level`, `set-kill-switch` — только
  администратору; отправитель — `lib/tunnelvpn/window.py:_dispatch` (`_tunnel_rules`, `_save_rules`, `del_tunnel`,
  `set_mode` → `set-tunnel {mode}`, `log_level`, `kill_switch` → `set-kill-switch {on}` строго bool, без `apply`:
  `lib/tunnelvpn/service.py:_set_kill_switch` сам переводит выход `direct` ↔ `block`), у `add-tunnel` отправителей нет. Отказ `set-tunnel`
  (дубль записи, нет `0.0.0.0/0`, у уходящего в запасные непустой «не пускать», несколько конфигов или тот же
  файл у основного) — `error` строкой и `problems [{field: include|exclude, text, entry?}]`
  (`lib/tunnelvpn/service.py:_clash_of`, `_set_tunnel`; `entry` — у дублей). `check-rules` (только администратору,
  закрыта по умолчанию) — тот же `_plan_tunnel` без записи: `{include, exclude, rejected, problems, fate}`;
  отправитель — `lib/tunnelvpn/window.py:_validate_rules`, только при `_is_admin()`. Правка расчёта
  `set-tunnel` — в `_plan_tunnel`, иначе проверка на лету и сохранение разойдутся; окно показывает `rulesFailed({error, problems})` красным у
  поля `field`, без `problems` — `error` под первым полем. Ответ `set-tunnel` — `rejected`
  `{include|exclude: [...]}`, сохранённые `include`/`exclude` и `mode` (полный конфиг без «пускать» без основного
  стал `all`; `rulesSaved` тогда перерисовывает лист с одним полем). При живом основном такой конфиг уходит к
  нему запасным (`_spare_blocks`, `_to_spare` → `_move_config(…, "all", drop_tunnel=True)`), туннель удалён,
  ответ `{ok, moved: {tunnel, name, main}}` без списков → окно `rulesMoved(moved)` и `apply`; `handle` проверяет
  конфиг уже по `moved.tunnel` (`_test_conf`), а не `_test_active` удалённого.
- Повышение в основной (`lib/tunnelvpn/service.py:_promote`, `_idle_full`) — в `_set_tunnel` (при `include`),
  `_remove_tunnel`, `_remove_config` с `drop_tunnel`, `_move_config` с `drop_tunnel`: только когда основной ушёл
  этой командой. `set-tunnel {mode: list}` у основного («Всё остальное напрямую») его не зовёт — иначе выбор
  человека отменялся бы следующим удалением.
- `lib/tunnelvpn/cli.py:main` (`list`, `status`) читает `st["tunnels"]` (`id`, `name`, `mode`, `active`, `confs`,
  `check`, `answer`, `checking`). `add-config` с `tunnel=<id>` без `place` (окно `add_config {tunnel}` →
  `lib/tunnelvpn/window.py:_send_config`; трей конфиги не добавляет): конфиг становится активным, прежние остаются;
  общий частный DNS с выбранным конфигом туннеля «по списку» (`lib/tunnelvpn/service.py:_same_dns`, `_list_confs` —
  те же, что у `_place`) → `place: replace`, прежние удалены; затем `apply`. Ответ `check` — `{ok, status}`,
  `corp_probe` в нём нет.
- `apply` (USER_OPS) ↔ `lib/tunnelvpn/window.py:_apply`, `lib/tunnelvpn/cli.py:_apply`
  → `lib/tunnelvpn/service.py:_apply`:
  ответ `{ok, applied: off|sides|full, restarted?: [id], error?}`. Занятый `lock` ждёт до `APPLY_WAIT`, а не
  отвечает «уже идёт»: правка уже на диске (`set-enabled` пишет без замка). Канал — поток на клиента
  (`ipc.Server._serve_one`), статус ожидание не держит. Под `lock` без `busy` — трей не сереет, а
  `_watch_once` пропускает круг по `lock.locked()`; `_human += 1` гасит начатый круг (`_mind_sides` выходит).
  `sides` — `lib/tunnelvpn/tunnel.py:reload_sides` (перезапуск боковых со сменившейся меткой `Side.source`),
  затем `_forget_side` (таймауты и пауза по туннелю); `None` от неё → `_stop_locked` + `_start_locked` под тем
  же `lock` (между ними никто не вклинится: ни вторая правка с «off», ни start с отказом). Отказ sing-box
  перечитать rule-set виден после ответа `sides`: `_watch_sets` → `Core._sets_rejected(proc)` ждёт `lock`
  (`APPLY_WAIT`) и перезапускает, пока работает тот же процесс основного (`Tunnel.proc`), что при правке. CLI `use`/
  `profile` шлёт `apply` после удачного `set-active`/`set-profile` и печатает по `applied`; отказ `apply` — код 1.
  `{ask: true}` шлёт только окно (`lib/tunnelvpn/window.py:_apply`): `lib/tunnelvpn/tunnel.py:live_plan` `None` →
  ответ `applied: "ask"` без перезапуска → `js('askFull')` в `lib/tunnelvpn/ui/index.html` → «Переподключить» шлёт
  `send('apply_full')` → `apply` без `ask`. План из `live_plan` уходит в `reload_sides(plan)` — сборка одна.
  CLI шлёт без `ask`: полный перезапуск у него без вопроса; трей `apply` не шлёт. Новый `applied` — CLI `_apply` его не печатает.
- `add-config` без `tunnel`/`kind` — авто: `lib/tunnelvpn/service.py:_place` по `buildconfig.is_full`/`allowed_nets`
  и частному `[Interface] DNS` (`_private_dns`) → `place` в ответе: `all` (запасным в основной, active прежний),
  `list` (новый туннель `t<N>`, `name` = имя файла), `replace` (молча), иначе `{ok: False, ask: {id, name, conf}}`
  без записи — окно повторяет с `place: "replace"` + `tunnel` или `place: "new"`. Замена (и старый `kind=corp`)
  переименовывает туннель в имя файла. `remove-config` с `drop_tunnel` убирает туннель без конфигов.
  `move-config` {`tunnel`, `name`, `to`: `new`|`all`, `drop_tunnel`} — только полный конфиг; не в
  `READ_OPS/USER_OPS` → администратору. Отправитель авто, `place`, `drop_tunnel`, `move-config` —
  `lib/tunnelvpn/window.py:_send_config`/`_place_config` и `_dispatch` (`del_config`, `move_config`); `set-active` —
  ещё и `use_config` окна.
