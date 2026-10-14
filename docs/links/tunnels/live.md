---
paths:
  - "lib/tunnelvpn/tunnel.py"
  - "lib/tunnelvpn/buildconfig.py"
---

## Сборка конфига: на ходу
- `state\run\config.json` (`paths.CONFIG_JSON`) и `state\run\tunnel-<id>.json` (`lib/tunnelvpn/buildconfig.py:side_json`,
  префикс — чтобы id `config` не затёр основной): каталог `paths.RUN` закрыт, как `conf\` (`installer/tunnelvpn.iss`
  `[Dirs]` + `icacls` после `state\`; создаёт и `paths.ensure_dirs`, но ACL ставит только установщик).
  Пишет только `buildconfig.build` на включении, `buildconfig._drop_stale_sides` удаляет лишние боковые в
  `run\`, `buildconfig.drop_legacy` (из `buildconfig.main` и `lib/tunnelvpn/service.py:_migrate`) — собранные
  конфиги в открытом `state\` от прошлых версий (`config.json`, `tunnel-*.json`, `corp.json`/`personal.json`).
  Читают только процессы службы; окну — `lib/tunnelvpn/service.py:_howto` через канал. `lib/tunnelvpn/tunnel.py:restart_side` пира не
  перерезолвивает (DNS внутри туннеля отдаёт корп-имя не тем адресом); `lib/tunnelvpn/tunnel.py:_swap_side`
  (смена конфига на ходу) резолвит только новый пир и ставит ему host-маршрут в `owned` до старта процесса. Читают `lib/tunnelvpn/tunnel.py:start`
  (`sing-box check`), `_start_side`, `_peer_ips` (по `side_ids` из `config.json`, не по `Tunnel.sides`:
  после перезапуска службы список пуст), `lib/tunnelvpn/probe.py:peer_addrs` (по `side_ids`; тег основного —
  `Prober.main_tag` → `_slow_exit`) и `lib/tunnelvpn/service.py:_howto` (op `howto` → `lib/tunnelvpn/window.py:_howto`
  → `lib/tunnelvpn/ui/js/settings.js:renderHowto`; ключи и пароли не отдаёт, подсети и домены — только администратору).
  `side_ids` даёт и выключенных (собраны без процесса): `start` зовёт `_peer_ips` только по `Tunnel.sides`
  (имя пира выключенного не резолвит: неотвечающее держало включение 12 с; host-маршрут ему ставит
  `_place_side` при включении), `stop` без журнала — по всем `side_ids`; имена резолвятся разом, `peer_addrs` сверяет только тег основного, `service.py:_howto` отбрасывает их по
  `tunnels.json`, `probe.py:check_plan` — по `enabled`.
- Порт, логин и пароль socks связывают outbound `socks-<id>` в `config.json` и inbound `socks-in` (тип `mixed`:
  по нему же `probe._dns_via_proxy` спрашивает DNS туннеля, `Tunnel.link_of`) в `tunnel-<id>.json`: пара из одного `lib/tunnelvpn/buildconfig.py:new_link` в `assemble` (`_slot_links`: по связи
  на слот, `SLOTS = 2`). Второй слот (`socks2-<id>`) — файл `tunnel2-<id>.json` (`side_json(tid, 1)`): пишет
  `lib/tunnelvpn/tunnel.py:_spawn` из `tunnel-<id>.json` через `buildconfig.slot_config` перед стартом, удаляет
  `_drop_stale_sides` на каждой сборке. Перезапуск живого бокового — `tunnel.restart_side` → `_swap_slot`: новый
  процесс на свободном слоте (журнал — тот же открытый `Side.logfile`, `log_start` прежний), `PUT /proxies/via-<id>`
  (`_select`), стоп старого, `Side.slot`; не вышло — старый работает. Лежащий — `_start_side` на `Side.slot`.
  Основной процесс конфиг не перечитывает. `lib/tunnelvpn/tunnel.py:reload_sides` собирает `assemble` со связями
  (`buildconfig.links_of` — все слоты; одна связь вместо пары дала бы новые и полный перезапуск) и `clash_api` (`_api_link`) работающего `config.json`, `resolve=False`: совпал основной
  (`tunnel._fixed`: json round-trip, без `"log"` и `default` selector `out`) — на лету: новый в `built` —
  `_enable_side` (`_place_side`: резолв пира, host-маршрут, конфиг; старт с ожиданием socks), сменился `.conf` —
  `_swap_side`, `"log"` — `_relog_side`; затем `_write_sets` (только изменённые файлы; не записать или отказ
  sing-box → `None`, полный перезапуск в том же `Core._apply`; `_await_sets` ждёт до `RULE_SET_WAIT` строку
  `reload rule-set` с тегом в журнале основного — успех sing-box не пишет, поэтому принятие = тишина всего срока, и
  ответ `apply`/⏻ на это время дольше), `_renew_nrpt`, стоп ушедших из `built`,
  `_switch_main` (`set_out` + `config.json` с новым `default` — его читают `main_id`, `Prober.main_tag`),
  `buildconfig.drop_side` у заглушек (`side_ids` без бокового в сборке), в конце `_drop_stale` (были записаны
  rule-set): `winnet.flush_dns`, clash_api `POST /cache/dns/flush`, `DELETE /connections/<id>` у соединений, чьи
  `metadata` `host`/`destinationIP`/`processPath` попадают в смену записей (`_set_entries` — поля
  `tunnel._ENTRY_KEYS`; новое поле в rule-set из `buildconfig` без записи туда соединения по нему не закроет). `live_plan` отдаёт `assemble`
  ещё `dns=tunnel_dns(running)`: удалённый и вернувшийся `.conf` — на лету (заглушка, `build.md`).
  Иначе `None` и полное включение (`Core._apply`). Основной конфиг меняют: новый туннель или первый
  `.conf` туннеля, собранного без него (новый `socks-<id>`), удалённый туннель,
  адрес DNS туннеля, `SB_TUN_MTU` (MTU tun — `buildconfig.TUN_MTU`, от MTU туннелей не зависит: правка MTU в
  `.conf` — перезапуск одного бокового) — тогда tun и маршруты пересоздаются, сеть падает. v6-адрес у tun
  постоянный (`tun_address`): v6 основного (вкл/выкл, смена основного, запасной или выбранный конфиг без v6) —
  на лету, без v6-выхода (`not v6`, основного нет тоже) IPv6 отвергает rule-set `NO_V6_TAG`. Правило `reject`
  стоит первым, до `sniff` (на нём pre-match TCP останавливается — отказ пришёл бы закрытием открытого
  соединения), `no_drop` и без порта 53 (DNS на v6-адрес tun берёт `hijack-dns`).
  `probe.py:_slow_v6` при отказе видит «без утечки».
  Основной берёт `log_level` на следующем включении. Новое поле основного конфига, зависящее от случайного или резолва, сделает каждую правку полной; `lib/tunnelvpn/tunnel.py:_start_side`
  ждёт порт через `lib/tunnelvpn/buildconfig.py:side_link`.
- `clash_api` (адрес и секрет) пишет `lib/tunnelvpn/buildconfig.py:build` в `state\run\config.json` (секрет
  закрыт от пользователей вместе с каталогом); читает
  `lib/tunnelvpn/buildconfig.py:api_of` из `lib/tunnelvpn/tunnel.py:_clash` (`set_out`, `out_now`, `delay`).
  Теги — `buildconfig.OUT_TAG`, `via_tag(<id основного>)` (`set_out`, `delay` в `service._try_back`/`_mind_sides`,
  `probe.check_one`), `DIRECT_TAG`; `via_tag(<id>)` ↔ `socks_tag(<id>, слот)` — `_select` из `tunnel._swap_slot`.
- Профиль = `active` основного туннеля в `tunnels.json`: пишут `lib/tunnelvpn/tunnel.py:start(profile)`
  (проверка по `tunnels.list_confs` до остановки), `lib/tunnelvpn/service.py:_set_profile`, `_add_config`
  (через пункт туннеля — активный; авто в основной — запасным, `_keep_active`), `_remove_config` (удалён
  активный → первый оставшийся), `_move_config` (полный конфиг между основным и своим «по списку»); читают
  `tunnels.active_conf` (сборка) и `lib/tunnelvpn/probe.py:current_profile`. `state\profile` читает только
  переезд. «Уже работает» в `start` — по `Tunnel.profile` прошлого старта.
- `state\peer-ips.json` (`paths.PEER_IPS_FILE`, `{имя пира: IPv4}`): пишет и читает только
  `lib/tunnelvpn/buildconfig.py:resolve_peer` (`_remember_peer_ip`, `_known_peer_ips`) — последний удачный
  адрес, когда не ответил ни системный, ни публичный DNS. Журнал запасного пути идёт через
  `buildconfig.main(log=…)` из `lib/tunnelvpn/tunnel.py:start`; без `log` строки уходят в `print` и в `service.log` не попадают.
