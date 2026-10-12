---
paths:
  - "lib/tunnelvpn/service.py"
  - "lib/tunnelvpn/tunnel.py"
---

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
- Мёртвый основной — три таймаута за `DEAD_WINDOW` к любым адресам; перед переводом выхода на `direct`
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
  `lib/tunnelvpn/ui/js/log.js:logItem` снимает метку `LOG_MARK`, уровень — из `LOG_LINE` (без штампа — `ERROR`/`WARN`
  словом в строке).
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
