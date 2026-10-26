---
paths:
  - "lib/tunnelvpn/tunnel.py"
  - "lib/tunnelvpn/buildconfig.py"
  - "lib/tunnelvpn/winnet.py"
  - "lib/tunnelvpn/probe.py"
---

## Маршруты tun и пиров
- Пиры мимо tun и без host-маршрута: `lib/tunnelvpn/buildconfig.py:peer_excludes` → `route_exclude_address`
  у `tun-in`, sing-box тогда ставит на tun дробные префиксы в обход пиров, а не половинки. Покрытие tun —
  `lib/tunnelvpn/winnet.py:tun_cover` (шлюз tun или `/0`, `/1`) ↔ `lib/tunnelvpn/tunnel.py:start` (`_await_cover`
  до `TUN_ROUTE_WAIT`, половинки `HALVES` ставит сам только без маршрутов sing-box), `_verify_clean`,
  `lib/tunnelvpn/probe.py:probe_fast` (`r_low`/`r_high` — половина задета маршрутом tun; по ним `up` в
  `service._status`). Уборка дробных — `stop` шаг 4 (`routes_on_interface`), в `owned` их нет. Проверка
  половинки по `routes_for("0.0.0.0/1")` на tun снова звала бы поднятый VPN лежащим, добивание поверх дробных
  возвращало пира под tun: SSH к нему рвётся RST при моргании Wi-Fi. Host-маршруты к пирам — `singbox-log.md`.
- На лету исключение не пересчитывается: `lib/tunnelvpn/tunnel.py:live_plan` передаёт `assemble`
  `peers=buildconfig.tun_excludes(running)` (`live.md`); новый адрес пира до полного включения держит
  host-маршрут `/32`.
