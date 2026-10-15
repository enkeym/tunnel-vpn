---
paths:
  - "lib/tunnelvpn/buildconfig.py"
  - "lib/tunnelvpn/probe.py"
---

## Сборка конфига
- Сборка — `lib/tunnelvpn/buildconfig.py:build` из `buildconfig.sources` (`tunnels.load` + `tunnels.active_conf`;
  испорченный файл → `sys.exit` с причиной). `enabled: false` у туннеля (`tunnels._check_tunnel`, у
  основного тоже: слот основного — `tunnels.main_tunnel`, работающий — `tunnels.main_on`; его читают
  `assemble` (`main` → `v6`, `bypass`, `default` selector `out`), `probe.current_profile`, CLI, трей и окно —
  выключенный основной остаётся основным, всё остальное идёт напрямую): `sources` даёт его `active_conf` (неоднозначный → None, не `sys.exit`), `assemble`
  собирает socks, DNS, правило и боковой конфиг (пир не резолвит, испорченный конфиг — пропуск), но rule-set
  пустые и в возвращаемом `built` его нет — процесс не запускается; `tunnel._stamps` не метит, `Core._promote`
  не повышает; пробер и сторож не видят — процесса нет (`check_plan` `running` = `enabled`, `side_ids` и
  `probe._has_conf`). Туннель без `.conf` — **заглушка** `(t, None, None)` на его месте в `data["tunnels"]`:
  в полной сборке (`build` → `assemble(stubs=True)`) и на ходу (`links` с его id) — socks из `links` (в полной —
  новые), DNS из `dns` (на ходу — `tunnel_dns` работающего, в полной — `buildconfig.main` даёт `tunnel_dns`
  прошлого `config.json`), пустые `rules-<id>`/`names-<id>`, без бокового и без `built`; его `tunnel-<id>.json`,
  `tunnel2-` убирает `buildconfig.drop_side` из `build` (только своя `state\run\`, не `--out`) и из
  `tunnel.reload_sides`. Без заглушки в полной сборке первый `.conf` после перезапуска службы — новый socks
  в основном и полный перезапуск; `.conf` с DNS, которого не было в прошлой сборке, — тоже полный, кроме выключенного туннеля: на ходу
  (`stubs=False`) его DNS не из `dns` `assemble` в основной не кладёт, полный — при его включении. Читатели бокового по
  `side_ids` (`service._howto`, `probe.peer_addrs`, `tunnel._peer_ips`) без файла получают `{}`. `site.env` сборка больше не читает, окружение для неё
  `tunnel.start` не готовит; `SB_CORP_*`, `SB_STACK` — по-прежнему переменные окружения службы.
- `log_level` из `tunnels.json` → `"log"` конфига sing-box. Уровень выше `info` глушит строки, которые
  читает `lib/tunnelvpn/service.py:_read_log` (`DEAD_RE`, статистика адресов).
- Проверка туннелей без `CORP_PROBE`: домен проверки — `lib/tunnelvpn/probe.py:_probe_host` (первый include
  туннеля «по списку» без `/` и `*.`). План — `Prober.check_plan` (`tunnels.json` + `side_ids`/`tunnel_dns`
  собранного `config.json`) → `probe_slow` (часть — `_list_part`: `_slow_dns` при DNS у туннеля, иначе
  `_slow_http`; HTTPS при DNS не делается — адрес домена бывает в «не пускать» и мерил бы соседа; итог
  `_check_result`), `_slow_summary`, CLI `корп-DNS` (`HTTP` только при `corp_http`),
  `Prober.tunnel_answer` (сторож).
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
  основного — rule-set `bypass` (`BYPASS_TAG`, пустой без основного), его домены — ещё и `bypass-names`
  (`BYPASS_NAMES_TAG`, только `domain_suffix`) для DNS-правила на сервер `uplink` (`UPLINK_DNS_TAG`, `type: dhcp` —
  DNS аплинка напрямую; тег без `dns-`, иначе `tunnel_dns` и NRPT взяли бы его за туннель); его `exclude_apps` — там же отдельным
  правилом (`app_rule`, `process_path_regex` с экранированием `_RE2_SPECIAL`): внутри одного правила адрес и
  процесс sing-box складывает через «и». `route.find_process` — только пока у основного есть программы:
  первая программа и снятие последней меняют основной конфиг (`tunnel._fixed` → полный перезапуск),
  остальные правки списка — rule-set на лету. Selector `out` есть всегда: в нём `via-<id>`
  всех туннелей, `direct` и `block` (kill-switch; outbound `block` в конфиге всегда, `kill_switch` из
  `tunnels.json` в сборку не входит — его вкл/выкл не меняет `_fixed` и не роняет tun), `default` —
  `via-<id основного>` или `direct`; `route.final` и detour публичного DNS —
  всегда `out` (detour прямо в `direct` sing-box отвергает). Правило rule-set туннеля и detour его DNS — `via-<id>`
  (selector как detour sing-box 1.14.2-lx принимает); у `via-<id>` `default` всегда `socks-<id>`, живой выбор слота —
  только через clash_api (`tunnel._swap_slot`), `tunnel._fixed` его не видит.
- `allowed_ips` у endpoint бокового — `0.0.0.0/0` (+`::/0` при `v6` — v6-адрес у включённого основного): что пускать, решает rule-set
  туннеля (`buildconfig.tunnel_rule` → `rule_set`, исключение — `invert` внутри `and`).
- `add-config` (`service._add_config`) проверяет конфиг `lib/tunnelvpn/buildconfig.py:check_conf_text`: секции,
  затем `parse_text` и `buildconfig._endpoint` — те же правила, что у сборки (обязательные поля, `host:port`,
  числа), ошибка — `ValueError` строкой, без резолва. `endpoint` — обёртка над `_endpoint` с `sys.exit`, резолвом
  пира и потолком MTU для AWG. Новое правило конфига — только в `_endpoint`: правило в обёртке `endpoint`
  пропустит конфиг в `conf\`, а сборка на нём встанет — `apply` → `reload_sides` `None` → `_stop_locked` +
  `_start_locked` с отказом, VPN выключен целиком.
- Боковые процессы: `buildconfig.main` → `[(id, имя)]` собранных туннелей → `lib/tunnelvpn/tunnel.py:start`
  строит `Tunnel.sides` (`Side(tid, title)`); основной — `buildconfig.main_id` по `default` selector `out`
  (`Tunnel.main`, может быть `None`); остальные — «по списку», сторож узнаёт их по `side is not tunnel.main`.
- Дубль домена: `lib/tunnelvpn/service.py:_domains`/`_covers` повторяют форму записи из
  `lib/tunnelvpn/buildconfig.py:split_entries` (`x.su` — `domain_suffix` домен и поддомены, `*.x.su` → `.x.su` —
  только поддомены), по ней `_clash` ловит пересечение по суффиксу. Сменил форму доменной записи в
  `split_entries` — правка `_domains`/`_covers`, иначе дубль снова уйдёт в туннель, чьё правило выше в `config.json`.
