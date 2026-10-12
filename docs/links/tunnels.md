---
paths:
  - "lib/tunnelvpn/tunnel.py"
  - "lib/tunnelvpn/buildconfig.py"
  - "lib/tunnelvpn/paths.py"
  - "lib/tunnelvpn/tunnels.py"
  - "lib/tunnelvpn/routelist.py"
---

## Формат и переезд (`conf\tunnels.json`)
- Формат пишет и проверяет только `lib/tunnelvpn/tunnels.py:validate` (`load`, `save`); записи списков
  `include`/`exclude` нормализует `lib/tunnelvpn/routelist.py:parse` (`1.2.3.4` → `1.2.3.4/32`, `*.x.su`
  оставляет звёздочку). Сборка, которая будет читать записи, обязана понимать ровно эти формы.
- `lib/tunnelvpn/tunnels.py:migrate` читает ключи `site.env` (`CORP_DOMAINS` → `include` туннеля `work`,
  `SB_CORP_EXCLUDE` → `exclude`, `SB_LOG_LEVEL` → `log_level`) и `PROFILE_FILE` (→ `active` у `home`),
  переносит `conf\corp|personal` в `conf\tunnels\work|home`. Новый ключ `site.env`, который должен
  пережить переезд, — правка `migrate`. Id `work`/`home` — `tunnels.LEGACY`, на них опираются старые
  `kind` `corp`/`personal`. Без `site.env` и старых конфигов (свежая установка) — пустой `tunnels.empty()`:
  `by_kind` и `Core._target` по `kind` тогда отвечают «нет туннеля», окно шлёт авто-`add-config` без `kind`.
- Имена конфигов: `lib/tunnelvpn/tunnels.py:safe_name`/`check_name`, `buildconfig` их реэкспортирует.

## Сборка конфига
- Переезд — `lib/tunnelvpn/service.py:_migrate` при старте службы: `buildconfig.migrate_flat` (плоский
  `conf\` → `conf\corp|personal`, профиль в `PROFILE_FILE`), затем `tunnels.migrate`. Порядок обязателен:
  второй шаг берёт то, что разложил первый. Каталоги — `lib/tunnelvpn/paths.py:ensure_dirs` (`CONF_TUNNELS`;
  `CONF_CORP`/`CONF_PERSONAL` остались только для переезда).
- Сборка — `lib/tunnelvpn/buildconfig.py:build` из `buildconfig.sources` (`tunnels.load` + `tunnels.active_conf`;
  испорченный файл → `sys.exit` с причиной). `enabled: false` у туннеля (`tunnels._check_tunnel`, у
  основного тоже: слот основного — `tunnels.main_tunnel`, работающий — `tunnels.main_on`; его читают
  `assemble` (`main` → `v6`, `bypass`, `default` selector `out`), `probe.current_profile`, CLI, трей и окно —
  выключенный основной остаётся основным, всё остальное идёт напрямую): `sources` даёт его `active_conf` (неоднозначный → None, не `sys.exit`), `assemble`
  собирает socks, DNS, правило и боковой конфиг (пир не резолвит, испорченный конфиг — пропуск), но rule-set
  пустые и в возвращаемом `built` его нет — процесс не запускается; `tunnel._stamps` не метит, `Core._promote`
  не повышает; пробер и сторож не видят — процесса нет (`check_plan` `running` = `enabled` и `side_ids`). `site.env` сборка больше не читает, окружение для неё
  `tunnel.start` не готовит; `SB_CORP_*`, `SB_STACK` — по-прежнему переменные окружения службы.
- `log_level` из `tunnels.json` → `"log"` конфига sing-box. Уровень выше `info` глушит строки, которые
  читает `lib/tunnelvpn/service.py:_read_log` (`DEAD_RE`, статистика адресов).
- Проверка туннелей без `CORP_PROBE`: домен проверки — `lib/tunnelvpn/probe.py:_probe_host` (первый include
  туннеля «по списку» без `/` и `*.`). План — `Prober.check_plan` (`tunnels.json` + `side_ids`/`tunnel_dns`
  собранного `config.json`) → `probe_slow` (часть — `_list_part`: `_slow_dns` при DNS у туннеля, иначе
  `_slow_http`; HTTPS при DNS не делается — адрес домена бывает в «не пускать» и мерил бы соседа; итог
  `_check_result`), `_slow_summary`, CLI `корп-DNS` (`HTTP` только при `corp_http`),
  `Prober.tunnel_answer` (сторож); `Prober.corp_probe` — только показ `CORP_PROBE` в `get-site` до шага 6.
- Теги по id туннеля — только через `buildconfig.ep_tag` (`wg-<id>`), `socks_tag` (`socks-<id>`, слот 1 —
  `socks2-<id>`: не начинается с `socks-`, `side_ids` его не берёт), `via_tag` (`via-<id>`, selector над слотами), `dns_tag`
  (`dns-<id>`); постоянные — `out`, `direct`, `dns` (публичный, `PUBLIC_DNS_TAG`), без префикса с дефисом,
  чтобы не совпасть с id. Читатели по префиксу: `buildconfig.side_ids` (socks-выходы → id боковых),
  `ep_of_socks` (→ `lib/tunnelvpn/service.py:_count_paths`, статистика под тегом endpoint), `tunnel_dns`
  (→ `lib/tunnelvpn/probe.py:check_plan`; `dns-<id>` есть у каждого туннеля с DNS в `.conf`, основного тоже —
  `check_plan` отдаёт `dns` только «по списку», иначе сторож и `корп-DNS` спрашивали бы DNS основного), `tunnel_domains`
  (→ `lib/tunnelvpn/tunnel.py:_corp_domains` → `winnet.nrpt_set`), `tunnel_nets` (→ `window.py:_howto`).
  Сменил форму тега — правка этих функций, иначе NRPT не ставится и боковые не находят socks.
- Что забирает туннель «по списку» — не в `config.json`, а в локальных rule-set `state\run\rules-<id>.json`
  (маршрут) и `names-<id>.json` (домены его DNS): `buildconfig.rules_tag`/`names_tag`/`rule_set_json`, пишет
  `build` раньше `config.json` (`sing-box check` без файла отказывает), убирает `_drop_stale_sides`. В основном
  конфиге — `route.rule_set` и правила со ссылкой у каждого туннеля: с пустым списком, выключенного и
  «весь остальной трафик» тоже (пустой rule-set не совпадает ни с чем) — иначе смена основного и «Всё остальное
  напрямую» меняли бы основной конфиг (`tunnel._fixed`) и шли в полный перезапуск. `names-*` — только `domain_suffix`: rule-set с `ip_cidr` в правиле DNS sing-box 1.14 при
  перечитывании отвергает и молча держит старый. Читают через `buildconfig.rule_set_rules`: `tunnel_domains`
  (NRPT), `tunnel_nets` (окно). Домен его же endpoint выкидывает `buildconfig.off_endpoint`. «Не пускать»
  основного — rule-set `bypass` (`BYPASS_TAG`, пустой без основного). Selector `out` есть всегда: в нём `via-<id>`
  всех туннелей, `direct` и `block` (kill-switch; outbound `block` в конфиге всегда, `kill_switch` из
  `tunnels.json` в сборку не входит — его вкл/выкл не меняет `_fixed` и не роняет tun), `default` —
  `via-<id основного>` или `direct`; `route.final` и detour публичного DNS —
  всегда `out` (detour прямо в `direct` sing-box отвергает). Правило rule-set туннеля и detour его DNS — `via-<id>`
  (selector как detour sing-box 1.14.2-lx принимает); у `via-<id>` `default` всегда `socks-<id>`, живой выбор слота —
  только через clash_api (`tunnel._swap_slot`), `tunnel._fixed` его не видит.
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
  → `renderHowto` в `lib/tunnelvpn/ui/index.html`; ключи и пароли не отдаёт, подсети и домены — только администратору).
  `side_ids` даёт и выключенных (собраны без процесса): `_peer_ips` ставит host-маршрут и их пирам (включение
  находит его в `owned`), `peer_addrs` сверяет только тег основного, `service.py:_howto` отбрасывает их по
  `tunnels.json`, `probe.py:check_plan` — по `enabled`.
- `allowed_ips` у endpoint бокового — `0.0.0.0/0` (+`::/0` при `v6` — v6-адрес у включённого основного): что пускать, решает rule-set
  туннеля (`buildconfig.tunnel_rule` → `rule_set`, исключение — `invert` внутри `and`).
- Порт, логин и пароль socks связывают outbound `socks-<id>` в `config.json` и inbound `socks-in` в
  `tunnel-<id>.json`: пара из одного `lib/tunnelvpn/buildconfig.py:new_link` в `assemble` (`_slot_links`: по связи
  на слот, `SLOTS = 2`). Второй слот (`socks2-<id>`) — файл `tunnel2-<id>.json` (`side_json(tid, 1)`): пишет
  `lib/tunnelvpn/tunnel.py:_spawn` из `tunnel-<id>.json` через `buildconfig.slot_config` перед стартом, удаляет
  `_drop_stale_sides` на каждой сборке. Перезапуск живого бокового — `tunnel.restart_side` → `_swap_slot`: новый
  процесс на свободном слоте (журнал — тот же открытый `Side.logfile`, `log_start` прежний), `PUT /proxies/via-<id>`
  (`_select`), стоп старого, `Side.slot`; не вышло — старый работает. Лежащий — `_start_side` на `Side.slot`.
  Основной процесс конфиг не перечитывает. `lib/tunnelvpn/tunnel.py:reload_sides` собирает `assemble` со связями
  (`buildconfig.links_of` — все слоты; одна связь вместо пары дала бы новые и полный перезапуск) и `clash_api` (`_api_link`) работающего `config.json`, `resolve=False`: совпал основной
  (`tunnel._fixed`: json round-trip, без `"log"` и `default` selector `out`) — на лету: новый в `built` —
  `_enable_side` (`_place_side`: резолв пира, host-маршрут, конфиг; старт с ожиданием socks), сменился `.conf` —
  `_swap_side`, `"log"` — `_relog_side`; затем `_write_sets` (только изменённые файлы, не записать → `None`;
  без ожидания: `_watch_sets` в фоне через `RULE_SET_WAIT` ищет в журнале основного `reload rule-set` с тегом →
  `on_reject` — `Core._sets_rejected`), `_renew_nrpt`, стоп ушедших из `built`,
  `_switch_main` (`set_out` + `config.json` с новым `default` — его читают `main_id`, `Prober.main_tag`).
  Иначе `None` и полное включение (`Core._apply`). Основной конфиг меняют: новый туннель (новый `socks-<id>`),
  адрес DNS туннеля, `SB_TUN_MTU` (MTU tun — `buildconfig.TUN_MTU`, от MTU туннелей не зависит: правка MTU в
  `.conf` — перезапуск одного бокового) — тогда tun и маршруты пересоздаются, сеть падает. v6-адрес у tun
  постоянный (`tun_address`): v6 основного (вкл/выкл, смена основного, запасной или выбранный конфиг без v6) —
  на лету, без v6-выхода (`not v6`, основного нет тоже) IPv6 отвергает rule-set `NO_V6_TAG`. Правило `reject`
  стоит первым, до `sniff` (на нём pre-match TCP останавливается — отказ пришёл бы закрытием открытого
  соединения), `no_drop` и без порта 53 (DNS на v6-адрес tun берёт `hijack-dns`).
  `probe.py:_slow_v6` при отказе видит «без утечки».
  Основной берёт `log_level` на следующем включении. Новое поле основного конфига, зависящее от случайного или резолва, сделает каждую правку полной; `lib/tunnelvpn/tunnel.py:_start_side`
  ждёт порт через `lib/tunnelvpn/buildconfig.py:side_link`.
- `add-config` (`service._add_config`) проверяет конфиг `lib/tunnelvpn/buildconfig.py:check_conf_text`: секции,
  затем `parse_text` и `buildconfig._endpoint` — те же правила, что у сборки (обязательные поля, `host:port`,
  числа), ошибка — `ValueError` строкой, без резолва. `endpoint` — обёртка над `_endpoint` с `sys.exit`, резолвом
  пира и потолком MTU для AWG. Новое правило конфига — только в `_endpoint`: правило в обёртке `endpoint`
  пропустит конфиг в `conf\`, а сборка на нём встанет — `apply` → `reload_sides` `None` → `_stop_locked` +
  `_start_locked` с отказом, VPN выключен целиком.
- Боковые процессы: `buildconfig.main` → `[(id, имя)]` собранных туннелей → `lib/tunnelvpn/tunnel.py:start`
  строит `Tunnel.sides` (`Side(tid, title)`); основной — `buildconfig.main_id` по `default` selector `out`
  (`Tunnel.main`, может быть `None`); остальные — «по списку», сторож узнаёт их по `side is not tunnel.main`.
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
- Дубль домена: `lib/tunnelvpn/service.py:_domains`/`_covers` повторяют форму записи из
  `lib/tunnelvpn/buildconfig.py:split_entries` (`x.su` — `domain_suffix` домен и поддомены, `*.x.su` → `.x.su` —
  только поддомены), по ней `_clash` ловит пересечение по суффиксу. Сменил форму доменной записи в
  `split_entries` — правка `_domains`/`_covers`, иначе дубль снова уйдёт в туннель, чьё правило выше в `config.json`.
