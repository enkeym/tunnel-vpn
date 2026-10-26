---
paths:
  - "lib/tunnelvpn/service.py"
  - "lib/tunnelvpn/tunnel.py"
---

## Команды канала: проверка, флаги, запуск
- `test-config` {`tunnel`, `name`} (USER_OPS: ответ без ключей и адресов сети) → `lib/tunnelvpn/service.py:_test_conf`
  → `{ok, test: {result, answer, at, checking}}`; удачные `add-config` и `set-tunnel` (с `include`/`mode`,
  `_test_active`) запускают её сами в потоке; у `add-config` конфиг, с которым `_apply` окна перезапустит живой
  боковой (`_swap_from`), поток проверяет после перезапуска (`_await_swap` ждёт смены `Side.started` до
  `SWAP_WAIT`): иначе `trial` с тем же ключом уводит сервер у нового процесса. Итог — `st["tunnels"][i]["tests"]` `{имя: {…}}`
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
  него видят только через статус; `lib/tunnelvpn/ui/js/topbar.js:paintButtons` при всех выключенных кнопку не гасит (`missing` из
  `model.js:model` — только «конфигов нет»).
- `start`, `start-only`, `stop` и автозапуск → `lib/tunnelvpn/service.py:_command`: ждёт `lock` до `APPLY_WAIT`
  (не «уже идёт»); та же команда, что идёт (`_running.key`), — её ответ; выполняется последняя пришедшая
  (`_cmd`), опоздавшая — `{ok: True, cancelled: True}`. `stop` ставит `lib/tunnelvpn/tunnel.py:Tunnel.cancel` →
  `Tunnel.start` между шагами отвечает `tunnel.CANCELLED` → `_start_locked` убирает (`tunnel.stop`, `_restore_flags`)
  и отвечает `{ok: True, cancelled: True}` без `last_error` и повторов. `ok` у отмены — чтобы
  `lib/tunnelvpn/tray.py:_do`, `window.py:_guard` и `cli.py:main` не показывали её ошибкой; `tray.py:_quit_work`
  повторяет `stop`, пока `busy`, — теперь первый же `stop` дожидается отмены. Сторож (`_reconnect`,
  `_restart_side`) берёт `lock` без ожидания.
- Переподключение на новую сеть — `lib/tunnelvpn/service.py:_watch_once`: аплинк (`iface`, `gw`) из снимка пробера
  (`lib/tunnelvpn/probe.py:probe_fast` → `winnet.default_route`) ≠ `Tunnel.uplink` `UPLINK_SETTLE` кругов подряд и
  `lib/tunnelvpn/winnet.py:passes_traffic(iface)` → `_reconnect`. Проверка — TCP на `winnet.NET_CHECK` с адреса
  интерфейса (`iface_addrs`, без 169.254): привязка к адресу ведёт мимо tun только при сильной модели хоста Windows;
  включённый `weakhostsend` на интерфейсе пустит её через туннель, и неготовая сеть сойдёт за готовую. Не пропускает —
  круг возвращает (сеть, счёт) без `_reconnect`, проверка каждый круг (до `NET_CHECK_TIMEOUT`, круг сторожа длиннее),
  строка «не пропускает трафик» — на круге `UPLINK_SETTLE`.
- Проверка после сна на прежней сети — `lib/tunnelvpn/service.py:_watch` (`_note_sleep` → `Core._wake_left =
  WAKE_CHECKS`) ↔ `_watch_once` → `_wake_dead(cur)`: `tunnel.delay(via-<id основного>)` через clash_api основного;
  ответил или нет основного (`Tunnel.main` — `None`) — счёт в ноль, нет ответа при `winnet.passes_traffic(iface)` —
  минус проверка, ноль — полный `_reconnect`, не `_restart_side`. Сеть не пропускает трафик — проверка не тратится.
  Счёт обнуляет и выключенный VPN (`Tunnel.uplink` — `None`). Новая сеть после сна идёт пунктом выше.
  Второй взвод — в `_watch_once`: шлюз в снимке пробера пропадал при поднятом туннеле (`Core._uplink_lost`) и
  вернулся равным `Tunnel.uplink` — S0-сон службу не замораживает, и `_note_sleep` паузы не видит.
