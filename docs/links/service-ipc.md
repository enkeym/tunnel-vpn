---
paths:
  - "lib/tunnelvpn/service.py"
  - "lib/tunnelvpn/ipc.py"
  - "lib/tunnelvpn/probe.py"
  - "lib/tunnelvpn/tunnel.py"
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
- `test-config` {`tunnel`, `name`} (USER_OPS: ответ без ключей и адресов сети) → `lib/tunnelvpn/service.py:_test_conf`
  → `{ok, test: {result, answer, at, checking}}`; удачные `add-config` и `set-tunnel` (с `include`/`mode`,
  `_test_active`) запускают её сами в потоке. Итог — `st["tunnels"][i]["tests"]` `{имя: {…}}`
  (`_tests_of`, по метке файла `_conf_stamp`). Тот же приватный ключ в живом боковом
  (`lib/tunnelvpn/tunnel.py:side_with_key`, читает `tunnel-<id>.json`) → `lib/tunnelvpn/probe.py:check_one`;
  иначе `lib/tunnelvpn/tunnel.py:trial` (`state\run\trial-<id>.json` из `buildconfig.trial_config`, http-вход,
  журнал `trial-<id>-*.log`, при живом VPN host-маршрут к пиру без записи в `owned`, общий у проверок с одним
  пиром — счётчик `Tunnel._trial_routes`) → `probe.trial_check`. Файлы на id ↔ замок на id
  `Core._trial_locks` в `_finish_test`: разные туннели проверяются разом, конфиги одного — по одному
  (`_dns_via_proxy` — CONNECT на DNS:53, `Prober._http_code(url, proxy)`). Итоги те же, что у `check`
  (`up`/`rules`/`error`/`none`); ответ при DNS — `probe.BY_DNS`, не адрес. Отправитель — ↻ на плитке: `send('test_config')` →
  `lib/tunnelvpn/window.py:_test_config`; итог `tests[имя]` читают `cardState`/`spareState`/`testState`
  в `lib/tunnelvpn/ui/js/cards.js`, `checking` держит там «проверяю…».
- `set-enabled` {`tunnel`, `on`: bool} (USER_OPS, как `stop`) → `lib/tunnelvpn/service.py:_set_enabled`: любой
  туннель, основной тоже (выключенный остаётся основным, `tunnels.main_on` — None), пишет `enabled` в
  `tunnels.json`; живой VPN переносит `apply` отправителя (как после
  `set-active`). Флаг — `st["tunnels"][i]["enabled"]`. Отправители, все с `apply` после удачи: окно
  `send('set_enabled', {tunnel, on, replace, seq})` из кнопки ⏻ и ⋮ карточки (`flipEnabled` → `sendEnabled` в
  `lib/tunnelvpn/ui/js/cards.js`) → `lib/tunnelvpn/window.py:_dispatch` → `_flip` (порядок по `seq`) → `_apply`;
  CLI `enable|disable ТУННЕЛЬ`, основной тоже (`lib/tunnelvpn/cli.py:main` → `_apply`); трей флаг не шлёт.
  Флаг читают `cardState` («Туннель выключен») и `paintCard` в `lib/tunnelvpn/ui/js/cards.js`,
  `lib/tunnelvpn/tray.py:_state_word` («выключен»), `_conf_colors` (серый), `_main_on` (`_tray_state`,
  `_title`: выключенный основной — «всё остальное напрямую», не запасной выход),
  `lib/tunnelvpn/cli.py:status_lines` (профиль «основной выключен», выход «основной туннель выключен») и `list`.
  Включение поверх тех же адресов включённого (`lib/tunnelvpn/service.py:_clashing`: «пускать» + AllowedIPs
  включаемого против `_clash`) — `{ok: False, clash: [{id, name}], error}` без записи; окно → `js('askSwap',
  {tunnel, clash})` → «Заменить» шлёт `set_enabled {…, replace: true}` → мешающие выключаются той же записью.
  CLI `replace` не шлёт — ему только `error`. `_clashing` зовут ещё `_add_config` (новый «по списку» в те
  же сети ложится `enabled: false`) и `_wake_all` (пересекающийся с уже включённым не включает).
- `start` пишет флаги: `lib/tunnelvpn/service.py:handle` и автозапуск `Core.start` зовут `_do_start(wake=True)` →
  `_wake_all`: нет включённого туннеля с конфигом — все «по списку» `enabled=true`, нет основного — `_promote`.
  `_apply` и переподключение сторожа зовут без `wake`: иначе выключенный последним включился бы тем же
  перезапуском. `start-only` {`tunnel`} (USER_OPS) → `_do_start(only=…)` → `_only`: включён один этот туннель
  (нужен выбранный конфиг, живой VPN — отказ), не поднялся — `_restore_flags`; отправитель — `start_only` в
  `window.py:_dispatch` (⏻ плитки, `startOnly` в `cards.js`). Отправители `start` (`window.py:_dispatch`,
  `tray.py:on_toggle`, `cli.py:main`) флаги после
  него видят только через статус; `render` в `index.html` при всех выключенных кнопку не гасит (`missing` —
  только «конфигов нет»).
- `start`, `start-only`, `stop` и автозапуск → `lib/tunnelvpn/service.py:_command`: ждёт `lock` до `APPLY_WAIT`
  (не «уже идёт»); та же команда, что идёт (`_running.key`), — её ответ; выполняется последняя пришедшая
  (`_cmd`), опоздавшая — `{ok: True, cancelled: True}`. `stop` ставит `lib/tunnelvpn/tunnel.py:Tunnel.cancel` →
  `Tunnel.start` между шагами отвечает `tunnel.CANCELLED` → `_start_locked` убирает (`tunnel.stop`, `_restore_flags`)
  и отвечает `{ok: True, cancelled: True}` без `last_error` и повторов. `ok` у отмены — чтобы
  `lib/tunnelvpn/tray.py:_do`, `window.py:_guard` и `cli.py:main` не показывали её ошибкой; `tray.py:_quit_work`
  повторяет `stop`, пока `busy`, — теперь первый же `stop` дожидается отмены. Сторож (`_reconnect`,
  `_restart_side`) берёт `lock` без ожидания.

## Ключи статуса
- Словарь статуса собирают `lib/tunnelvpn/probe.py:snapshot` (через `set(**kw)`) и
  `lib/tunnelvpn/service.py:_status`, `lib/tunnelvpn/service.py:_check`; читают по строковым ключам
  `lib/tunnelvpn/cli.py:_print_status`, `lib/tunnelvpn/tray.py:_title`, `lib/tunnelvpn/tray.py:_menu`,
  `lib/tunnelvpn/tray.py:_state_word`, `lib/tunnelvpn/tray.py:_conf_colors`
  и `render` в `lib/tunnelvpn/ui/index.html`. Переименовал ключ (`up`, `busy`, `last_error`, `exit_state`,
  `corp_ip`, `v6_leak`, `profiles`, `corp`, `last`, `log_level`) — читатель молча видит пустое значение.
- `main_v6` — `lib/tunnelvpn/service.py:_status` (`_conf_facts(...)["v6"]` активного конфига `tunnels.main_on`,
  та же `buildconfig.has_v6`, что `v6` в `assemble`) → строка IPv6 в `lib/tunnelvpn/ui/js/settings.js` и
  `lib/tunnelvpn/cli.py:_print_status`. `v6_off` (правило брандмауэра) при включённом VPN истинно всегда —
  о v6 основного не говорит; читать его как «сервер не выдаёт IPv6» — прежняя ошибка окна.
- `log_level` — `lib/tunnelvpn/service.py:_status` (из `tunnels.json`) → галочка «подробный журнал» в `render`
  в `lib/tunnelvpn/ui/index.html` (`=== 'debug'`).
- `kill_switch` — `lib/tunnelvpn/service.py:_status` (из `tunnels.json`) → переключатель «Kill-switch» в
  `lib/tunnelvpn/ui/js/settings.js:paintDebug`, строка `kill-switch:` в `lib/tunnelvpn/cli.py:status_lines`.
- `last` — `lib/tunnelvpn/service.py:_last_results` (`{id: итог}`, файл `last-check.json` по id туннеля,
  пишет `_remember_check` из `st["tunnels"][i]["check"]`) → `st["tunnels"][i]["last"]` →
  `lib/tunnelvpn/tray.py:_conf_colors` и `tileState` в `lib/tunnelvpn/ui/index.html`; старый `st["last"]` по
  `corp`/`personal` (через `_kind_tunnel`) читателей больше нет — уйдёт в шаге 6. Кружки при выключенном VPN.
- `split` — `lib/tunnelvpn/service.py:_status` (есть `include`/`exclude` у `tunnels.by_kind(…, "corp")`);
  окно его больше не читает — уйдёт в шаге 6.
- `tunnels` — `lib/tunnelvpn/service.py:_status` (`[{id, name, mode, active, confs, rules, full, awg, check, answer,
  seq, last, checking}]`, без списков правил: `rules` — их число, `full` — у конфига из `_in_use` есть
  0.0.0.0/0, `awg` — `{конфиг: bool}` по `lib/tunnelvpn/buildconfig.py:is_awg` (`AWG_KEYS`); оба —
  `_conf_facts` с кэшем `_conf_cache` по `(путь, mtime_ns)`) → `lib/tunnelvpn/window.py:_edit_conf`,
  карточки окна (`paintCard`, `cardMenu`, бейдж `badge` в `lib/tunnelvpn/ui/js/cards.js`), трей (пункты меню по id —
  `_tunnel_entries`, `_sync_menu`), CLI. Старые `profiles`/`corp`/`last` считаются из туннелей через
  `tunnels.by_kind` (`_kind_tunnel`, `_in_use`) — до шага 6.
- `checking` — `lib/tunnelvpn/service.py:_status` (из `lib/tunnelvpn/probe.py:Prober.slow_busy`) → `render` в
  `lib/tunnelvpn/ui/index.html`: «проверяю…» вместо прошлого «молчит», пока идёт сетевая проверка.
- Проверка по туннелям: `check_seq` и `checks {id: {result, answer, seq}}` — `lib/tunnelvpn/probe.py:_take_slow`
  и `probe_slow` (итог `_check_result`: `up`/`rules`/`error`/`none`/''; `rules` — DNS туннеля ответил
  `probe.DNS_NO_ADDR` на домен из «пускать», `answer` — этот домен) → `lib/tunnelvpn/service.py:_status` кладёт в
  `st["tunnels"][i]` поля `check`, `answer`, `seq`, `checking` и убирает `checks` →
  `lib/tunnelvpn/ipc.py:check_by_side` (ждёт id из `st["tunnels"]` по `seq`), `lib/tunnelvpn/tray.py:_conf_colors`
  (биты lParam — `_side_bits`/`_sides_of` по id на открытие меню), `lib/tunnelvpn/cli.py:_check_text`,
  `lib/tunnelvpn/window.py:_check` (`checkSides` с id ждущих → `CHECK_LEFT` в `lib/tunnelvpn/ui/index.html`).
  Старые `corp_seq`/`personal_seq` (`probe.LEGACY_SIDES`) → `checking_corp`/`checking_personal` читателей
  больше нет — уйдут в шаге 6.
- `busy === 'переподключаю'` — `lib/tunnelvpn/service.py:_start_locked` (reconnect из
  `lib/tunnelvpn/service.py:_watch_once`) → заголовок «Переподключаю» в `render` в
  `lib/tunnelvpn/ui/index.html`. Строку менять с обеих сторон.
- `busy` с началом `перезапускаю` — `lib/tunnelvpn/service.py:_restart_side` → заголовок «Перезапускаю» в
  `render` в `lib/tunnelvpn/ui/index.html` (`startsWith('перезапускаю')`). Строку менять с обеих сторон.
- `out` = `block` (kill-switch, `buildconfig.BLOCK_TAG`; `direct` и `block` — `buildconfig.AWAY_TAGS`, выбор —
  `lib/tunnelvpn/tunnel.py:away`) → `probe._slow_exit` (`exit_state = "blocked"` без адреса), `probe._check_result`
  (основной `error`), `lib/tunnelvpn/tray.py:_away` (`_tray_state` → `blocked`, `_title`, `_state_word`, `_conf_colors`),
  `lib/tunnelvpn/cli.py:status_lines`, `blocked` в `lib/tunnelvpn/ui/js/model.js:model` (`out` решает раньше `exit_state`,
  как `_away`) → `headOf`, `problemsOf`, `cards.js:cardState`/`roleOf`, `settings.js:paintNow`. Новое значение
  `out` — правка всех этих читателей, иначе окно и трей покажут «Всё работает» при закрытом выходе.
- `out` (текущий выход selector, `via-<id основного>` | `direct` | `block` | `''`) — `lib/tunnelvpn/service.py:_mind_sides`,
  `_do_start`, `_watch_once` через `prober.set` → `lib/tunnelvpn/probe.py:_slow_exit` (`exit_state = "direct"`
  вместо `leak`), `lib/tunnelvpn/cli.py:_print_status`, `lib/tunnelvpn/tray.py:_title`,
  `lib/tunnelvpn/tray.py:_conf_colors`, `render` в `lib/tunnelvpn/ui/index.html`. Значение `direct` —
  `buildconfig.DIRECT_TAG`; в трее, CLI и окне оно строкой.
- `exit_state === 'direct'` — `lib/tunnelvpn/probe.py:_slow_exit` → те же читатели и
  `lib/tunnelvpn/service.py:_remember_check`: личный не работает, но это не утечка. Основного нет
  (`main_tag` пуст) или он выключен — тоже `direct`, и `out` тогда тоже `direct`: `render` пишет «Всё работает ·
  всё остальное — напрямую», `lib/tunnelvpn/tray.py:_title` — «всё остальное напрямую»,
  `lib/tunnelvpn/cli.py:status_lines` — «основного нет» / «основной туннель выключен»; работающий основной
  (`mode all` и `enabled`) они проверяют раньше `out`, иначе это читалось бы как запасной выход.
- Туннель жив — `check == 'up'` из `lib/tunnelvpn/probe.py:_check_result` (у основного — выход не `leak`/
  `direct`, у «по списку» — ответ DNS, а без DNS у туннеля — HTTPS, `probe._list_part`) в `lib/tunnelvpn/tray.py:_conf_colors` и
  `lib/tunnelvpn/service.py:_remember_check` и `tileState` в `lib/tunnelvpn/ui/index.html` (`check`, `answer`).
  `check == 'rules'` (сервер жив, правила не те): `tileState` — жёлтая «проверьте туннелирование»,
  `_conf_colors` — `busy`, `_remember_check` — как `up`, `lib/tunnelvpn/cli.py:_check_text` — своя строка.
  Ответ DNS «без адреса» — `lib/tunnelvpn/winnet.py:dns_ask_via` (`(ответил, адрес)`) → `probe._dns_ask`.
  `corp_ip`/`corp_http` (`probe_slow` для первого «по списку») читает только CLI — до шага 6.

## Журнал sing-box
- Мёртвый туннель — `lib/tunnelvpn/service.py:DEAD_RE` разбирает строку sing-box
  `open connection to <адрес> using outbound|endpoint/wireguard[<тег>]: context deadline exceeded` из журналов
  процессов туннелей (`tunnel-<id>-*.log`); тег ↔ `Side.tag` (`buildconfig.ep_tag(Side.tid)`: `wg-<id>`). Новая версия
  sing-box с другим текстом ошибки или тип endpoint не `wireguard` — сторож молча перестанет её видеть.
- Пауза перезапуска — `Core._side_after`/`_side_noted` по `Side.tid`: у каждого туннеля своя. Сбрасывают
  `_do_start` (все) и `_forget_side` из `_apply` (перезапущенные).
- Круг сторожа и действия человека: `_round_human` (снят в начале `_watch_once`) ≠ `_human` (`_command` — по
  приходу start/stop, ещё до замка; `_apply`) — `_mind_sides` выходит до разбора таймаутов и перед каждым туннелем,
  `_restart_side` и `_do_start(reconnect)` отказывают. Иначе таймауты прежних процессов перезапускали свежий.
- Мёртвый основной — три таймаута за `DEAD_WINDOW` к любым адресам (`DEAD_ADDRS` убран 6 октября:
  таймауты шли к одному адресу, туннель молчал три минуты); перед переводом выхода на `direct`
  `lib/tunnelvpn/service.py:_mind_sides` спрашивает `tunnel.delay(via-<id основного>)`: ответил — таймауты
  чужого адреса, выход и процесс не трогаем. Нет основного (`Tunnel.main` — `None`) — выход не трогают.
- Живая проверка перед перезапуском — `lib/tunnelvpn/service.py:_side_down` для любого туннеля, кроме
  `tunnel.main`; ответ — `lib/tunnelvpn/probe.py:tunnel_answer(side.tid)` (домен `_probe_host`, без него
  `probe.DNS_ASK_NAME`, у DNS этого туннеля; любой ответ, и NXDOMAIN, — сервер жив, не перезапускаем).
  Строка «не перезапускаю» — раз на смену состояния: `Core._side_said` (сбрасывают `_do_start`,
  `_forget_side`, молчащий DNS). Нет DNS у туннеля — сторож перезапускает по одним таймаутам.
- Возврат выхода на основной (из `direct` и из `block`) — `lib/tunnelvpn/service.py:_try_back(main, out)` зовёт `lib/tunnelvpn/tunnel.py:delay`
  (`DELAY_URL` через `via-<id основного>` в clash_api основного — замер идёт через выбранный слот). Без `clash_api` в `config.json` выход
  останется на `direct` навсегда.
- Запасной конфиг основного — `lib/tunnelvpn/service.py:_fallback` из `_mind_sides`: сторож перезапускал основной
  (`Core._main_kicked` ставит `_restart_side`, сбрасывают `_try_back` при возврате, `_forget_side`, `_do_start`), а
  `_try_back` не дошёл или процесс лежит → `_spares` (остальные `.conf` по кругу после `active`) → `_finish_test(…,
  alone=True)` (всегда `Tunnel.trial`, не `side_with_key`: тот же ключ у не везущего основного) → первый `up` —
  `_take_spare`: `_set_active` + `_apply_locked` (тело `_apply` под тем же замком, взятым без ожидания) + `_try_back`
  без паузы. Круг — не чаще `DEAD_GAP` (`_fallback_at`); ни один — выход остаётся `away()`. Итоги проверок — в
  `tests[имя]` плиток (`_note_test`), `active` в `tunnels.json` меняет служба, не окно.
- Команда `log` — `lib/tunnelvpn/service.py:_tail_log` сводит `vpn-*` и `tunnel-<id>-*` по `STAMP_RE`
  (штамп строки sing-box и заголовка `open_log`), строки туннелей с меткой `[имя туннеля] `. Туннели — из
  `Tunnel.sides`, до первого включения — из `tunnels.json`. Файлы — `lib/tunnelvpn/paths.py:log_files`
  (шаблон даты `LOG_STAMP`: иначе `work` подхватил бы `work-2`); тот же шаблон у `tunnel.open_log`.
  `renderLogs` в `lib/tunnelvpn/ui/index.html` ищет ` ERROR `/` WARN ` внутри строки — метка в начале не мешает.
- Куда что ходит — `lib/tunnelvpn/service.py:CONN_RE`, `PREMATCH_RE`, `DNS_RE` разбирают info-строки
  `endpoint/wireguard[<тег>]: outbound connection to <IP:порт>`, `pre-match: … to <IP> via outbound/…[<тег>]`
  и `dns: exchanged A <имя>. … IN A <IP>` (имя — первое по номеру запроса) → `_count_paths` → `state\paths.json`.
  Другой текст строк в новой sing-box или `SB_LOG_LEVEL` выше `info` — файл молча перестаёт пополняться.
  Трафик туннелей в журнале основного — `outbound/socks[socks-<id>]` или `[socks2-<id>]` (слот), `via-<id>` в pre-match;
  `lib/tunnelvpn/buildconfig.py:ep_of_socks` пишет его в `paths.json` под тегами туннелей (`wg-<id>`).
  Старые ключи `wg-corp`/`awg-personal` в уже накопленном `paths.json` остаются как есть.
- `lib/tunnelvpn/tunnel.py:start` пишет `Tunnel.log_start` (путь, смещение после заголовка из
  `open_log`) ↔ `lib/tunnelvpn/service.py:_read_logs` / `_read_log` читают журнал с него (позиция на
  каждый `log_start` в `Core._log_at`); `lib/tunnelvpn/tunnel.py:stop` сбрасывает в None. У боковых процессов
  свои журналы `tunnel-<id>-*.log` и `Side.log_start`
  (`lib/tunnelvpn/tunnel.py:_start_side`, сбрасывает `_stop_side`): сторож читает оттуда таймауты, в
  статистику адресов идёт только основной журнал.
- Выход наружу: selector `out` = [`via-<id>` туннелей, `direct`, `block`]. `lib/tunnelvpn/tunnel.py:start` при
  неподнятом основном зовёт `set_out(away())` (`block` при `kill_switch` и основном, иначе `direct`; `away` читает
  `tunnels.json` на каждом уводе), `_switch_main` и `_mind_sides` — тоже `away()`, повторный `start` при поднятом основном — `set_out(via-<id>)`.
  Перезапуск бокового (сторож `_restart_side`, правка `.conf`/журнала) выход не трогает: `tunnel._swap_slot`
  переключает `via-<id>` на новый процесс и `direct` сам не ставит (увод в `direct` при мёртвом основном —
  решение `_mind_sides` до перезапуска).
  Журнал бокового при этом не меняется: `Side.log_start` прежний, `Core._log_at` читает дальше.
- Host-маршруты к пирам — строки `host <ip> <шлюз> <интерфейс>` в `paths.OWNED_FILE` (пишут
  только `lib/tunnelvpn/tunnel.py:start`) ↔ `lib/tunnelvpn/tunnel.py:mend_peer_routes` берёт пиров
  оттуда и сверяет с `winnet.routes_for` на аплинке; зовут конец `start` и каждый круг
  `lib/tunnelvpn/service.py:_watch_once` на прежней сети. Другой вид строки `host` — сверка молча ничего не видит.
