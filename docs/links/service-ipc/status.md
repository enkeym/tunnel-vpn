---
paths:
  - "lib/tunnelvpn/service.py"
  - "lib/tunnelvpn/probe.py"
---

## Ключи статуса
- Словарь статуса собирают `lib/tunnelvpn/probe.py:snapshot` (через `set(**kw)`) и
  `lib/tunnelvpn/service.py:_status`, `lib/tunnelvpn/service.py:_check`; читают по строковым ключам
  `lib/tunnelvpn/cli.py:_print_status`, `lib/tunnelvpn/tray.py:_title`, `lib/tunnelvpn/tray.py:_menu`,
  `lib/tunnelvpn/tray.py:_state_word`, `lib/tunnelvpn/tray.py:_conf_colors`
  и `lib/tunnelvpn/ui/js/core.js:render` (вид — `model.js:model`, `cards.js`, `topbar.js`, `settings.js`). Переименовал ключ (`up`, `busy`, `last_error`, `exit_state`,
  `corp_ip`, `v6_leak`, `profiles`, `corp`, `last`, `log_level`) — читатель молча видит пустое значение.
- `main_v6` — `lib/tunnelvpn/service.py:_status` (`_conf_facts(...)["v6"]` активного конфига `tunnels.main_on`,
  та же `buildconfig.has_v6`, что `v6` в `assemble`) → строка IPv6 в `lib/tunnelvpn/ui/js/settings.js` и
  `lib/tunnelvpn/cli.py:_print_status`. `v6_off` (правило брандмауэра) при включённом VPN истинно всегда —
  о v6 основного не говорит; читать его как «сервер не выдаёт IPv6» — прежняя ошибка окна.
- `log_level` — `lib/tunnelvpn/service.py:_status` (из `tunnels.json`) → галочка «подробный журнал» в
  `lib/tunnelvpn/ui/js/settings.js` (`=== 'debug'`).
- `kill_switch` — `lib/tunnelvpn/service.py:_status` (из `tunnels.json`) → переключатель «Kill-switch» в
  `lib/tunnelvpn/ui/js/settings.js:paintDebug`, строка `kill-switch:` в `lib/tunnelvpn/cli.py:status_lines`.
- `last` — `lib/tunnelvpn/service.py:_last_results` (`{id: итог}`, файл `last-check.json` по id туннеля,
  пишет `_remember_check` из `st["tunnels"][i]["check"]`) → `st["tunnels"][i]["last"]` →
  `lib/tunnelvpn/tray.py:_conf_colors` и `lib/tunnelvpn/ui/js/cards.js:cardState` («раньше работал»); старый `st["last"]` по
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
- `checking` — `lib/tunnelvpn/service.py:_status` (из `lib/tunnelvpn/probe.py:Prober.slow_busy`) → `checking` в
  `lib/tunnelvpn/ui/js/model.js:model` и `cards.js:cardState`: «проверяю…», пока идёт сетевая проверка.
- Проверка по туннелям: `check_seq` и `checks {id: {result, answer, seq}}` — `lib/tunnelvpn/probe.py:_take_slow`
  и `probe_slow`, итог туннеля «по списку» без смены `seq` — ещё `check_one` (↻ живого, `_note_check`).
  Обе спрашивают туннель «по списку» через `_list_ask`: процесс моложе `probe.SIDE_SETTLE` по
  `lib/tunnelvpn/tunnel.py:Side.started` (ставят `_start_side`/`_swap_slot`, пробер читает через
  `lib/tunnelvpn/service.py:_side_started`) молчит и с рабочим конфигом — его спрашивают снова короткой
  попыткой (`_slow_dns`/`_slow_http` с `quick`: одна попытка `SETTLE_*_TIMEOUT`, пауза `SETTLE_PAUSE`;
  по сроку — полная, `CORP_TRIES`), а туннель
  до итога остаётся `checking` (пилюля `model.js:problemsOf` его пропускает); `probe_slow` идёт дольше,
  `check_now` ждёт её до `CHECK_WAIT`
  (итог `_check_result`: `up`/`rules`/`error`/`none`/''; `rules` — DNS туннеля ответил
  `probe.DNS_NO_ADDR` на домен из «пускать», `answer` — этот домен) → `lib/tunnelvpn/service.py:_status` кладёт в
  `st["tunnels"][i]` поля `check`, `answer`, `seq`, `checking` и убирает `checks` →
  `lib/tunnelvpn/ipc.py:check_by_side` (ждёт id из `st["tunnels"]` по `seq`), `lib/tunnelvpn/tray.py:_conf_colors`
  (биты lParam — `_side_bits`/`_sides_of` по id на открытие меню), `lib/tunnelvpn/cli.py:_check_text`,
  `lib/tunnelvpn/window.py:_check` (`checkSides` с id ждущих → `CHECK_LEFT` в `lib/tunnelvpn/ui/js/model.js`).
  Старые `corp_seq`/`personal_seq` (`probe.LEGACY_SIDES`) → `checking_corp`/`checking_personal` читателей
  больше нет — уйдут в шаге 6.
- `busy === 'переподключаю'` — `lib/tunnelvpn/service.py:_start_locked` (reconnect из
  `lib/tunnelvpn/service.py:_watch_once`) → заголовок «Переподключаю» в
  `lib/tunnelvpn/ui/js/model.js:headOf`. Строку менять с обеих сторон.
- `busy` с началом `перезапускаю` — `lib/tunnelvpn/service.py:_restart_side` → заголовок «Перезапускаю» в
  `lib/tunnelvpn/ui/js/model.js:headOf` (`startsWith('перезапускаю')`). Строку менять с обеих сторон.
- `out` = `block` (kill-switch, `buildconfig.BLOCK_TAG`; `direct` и `block` — `buildconfig.AWAY_TAGS`, выбор —
  `lib/tunnelvpn/tunnel.py:away`) → `probe._slow_exit` (`exit_state = "blocked"` без адреса), `probe._check_result`
  (основной `error`), `lib/tunnelvpn/tray.py:_away` (`_tray_state` → `blocked`, `_title`, `_state_word`, `_conf_colors`),
  `lib/tunnelvpn/cli.py:status_lines`, `blocked` в `lib/tunnelvpn/ui/js/model.js:model` (`out` решает раньше `exit_state`,
  как `_away`) → `headOf`, `problemsOf`, `cards.js:cardState`/`roleOf`, `settings.js:paintNow`. Новое значение
  `out` — правка всех этих читателей, иначе окно и трей покажут «Всё работает» при закрытом выходе.
- `out` (текущий выход selector, `via-<id основного>` | `direct` | `block` | `''`) — `lib/tunnelvpn/service.py:_mind_sides`,
  `_do_start`, `_watch_once` через `prober.set` → `lib/tunnelvpn/probe.py:_slow_exit` (`exit_state = "direct"`
  вместо `leak`), `lib/tunnelvpn/cli.py:_print_status`, `lib/tunnelvpn/tray.py:_title`,
  `lib/tunnelvpn/tray.py:_conf_colors`, `lib/tunnelvpn/ui/js/model.js:model`. Значение `direct` —
  `buildconfig.DIRECT_TAG`; в трее, CLI и окне оно строкой.
- `exit_state === 'direct'` — `lib/tunnelvpn/probe.py:_slow_exit` → те же читатели и
  `lib/tunnelvpn/service.py:_remember_check`: личный не работает, но это не утечка. Основного нет
  (`main_tag` пуст) или он выключен — тоже `direct`, и `out` тогда тоже `direct`: `headOf` пишет «Всё работает ·
  всё остальное — напрямую, без VPN», `lib/tunnelvpn/tray.py:_title` — «всё остальное напрямую»,
  `lib/tunnelvpn/cli.py:status_lines` — «основного нет» / «основной туннель выключен»; работающий основной
  (`mode all` и `enabled`) они проверяют раньше `out`, иначе это читалось бы как запасной выход.
- Туннель жив — `check == 'up'` из `lib/tunnelvpn/probe.py:_check_result` (у основного — выход не `leak`/
  `direct`, у «по списку» — ответ DNS, а без DNS у туннеля — HTTPS, `probe._list_part`) в `lib/tunnelvpn/tray.py:_conf_colors` и
  `lib/tunnelvpn/service.py:_remember_check` и `lib/tunnelvpn/ui/js/cards.js:cardState` (`check`, `answer`).
  `check == 'rules'` (сервер жив, правила не те): `cardState` — warn «работает · «домен» не открывается»,
  `model.js:problemsOf` — «проверьте туннелирование», `cards.js:splitState` — красный ⇄,
  `_conf_colors` — `busy`, `_remember_check` — как `up`, `lib/tunnelvpn/cli.py:_check_text` — своя строка.
  Ответ DNS «без адреса» — `lib/tunnelvpn/probe.py:_dns_addr` (`''` на NXDOMAIN) → `probe._dns_via_proxy`.
  DNS туннеля «по списку» `_slow_dns` спрашивает через вход его процесса: `Prober.link` ←
  `lib/tunnelvpn/service.py:_side_link` ← `lib/tunnelvpn/tunnel.py:link_of` (слот `Side.slot`). Не через tun:
  там запрос берёт `hijack-dns` основного процесса — ответ из его кэша и после замены на нерабочий конфиг.
  `corp_ip`/`corp_http` (`probe_slow` для первого «по списку») читает только CLI — до шага 6.
