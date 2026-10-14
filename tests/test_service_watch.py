"""Сторож сети: смена аплинка и упавший sing-box поднимают туннель заново,
мигание сети и команда человека — нет, повторы конечны.

Core без службы: туннель — заглушка, аплинк кладём в снимок пробера руками,
круги сторожа гоняем вызовом _watch_once без ожиданий."""

import json
import threading
import time
import types

import pytest

from tunnelvpn import buildconfig, probe, service, tunnel, tunnels

OFFICE = (12, "192.168.19.1")
HOME = (7, "192.168.0.1")
# Выход через основной туннель home.
HOME_VIA = buildconfig.via_tag("home")
# Порты входов процессов: через вход основного идёт обход.
LINK_PORTS = {"work": 1080, "home": 1081}


@pytest.fixture(autouse=True)
def _paths_file(monkeypatch, tmp_path):
    """Статистика адресов — во временную папку, не в настоящий state."""
    path = tmp_path / "paths.json"
    monkeypatch.setattr(service, "PATHS_FILE", str(path))
    return path


@pytest.fixture(autouse=True)
def _detours_file(monkeypatch, tmp_path):
    """Память обхода и внешний адрес включения — свои у каждого теста; адреса
    нет, пока тест его не запишет (_real_ip)."""
    path = tmp_path / "detours.json"
    monkeypatch.setattr(service, "DETOURS_FILE", str(path))
    monkeypatch.setattr(service.paths, "REAL_IP_FILE", str(tmp_path / "real-ip"))
    return path


def _timeout(addr, tag="wg-home"):
    return (f"+0300 2026-10-05 09:59:57 ERROR [2478390845 15.1s] connection: open "
            f"connection to {addr} using outbound/wireguard[{tag}]: "
            f"context deadline exceeded\n")


class _Proc:
    returncode = 1

    def poll(self):
        return self.returncode


class _Alive:
    returncode = None

    def poll(self):
        return None


class _Tunnel:
    # Как у настоящего: основной — по main_id.
    main = tunnel.Tunnel.main

    def __init__(self, prober):
        self.prober = prober
        self.uplink = OFFICE
        self.proc = None
        self.log_start = None
        self.fail = False
        self.starts = 0
        self.work = tunnel.Side("work", "Работа")
        self.home = tunnel.Side("home", "Личный")
        self.sides = [self.work, self.home]
        self.main_id = "home"
        for side in self.sides:
            side.proc = _Alive()
        self.out = HOME_VIA
        self.outs = []           # переключения выхода по порядку
        self.restarts = []       # id перезапущенных туннелей
        self.side_fail = ""
        self.delay_ms = None     # что ответит проверка через основной
        self.kill_switch = False  # что away() прочла бы из tunnels.json
        self.mends = 0           # сверок маршрутов к пирам
        self.detours = []        # (id, адрес) set_detour: None — снова напрямую
        self.cancel = threading.Event()

    def start(self, _profile):
        self.starts += 1
        if self.fail:
            return "не поднялся"
        s = self.prober.snapshot()
        self.uplink = (s["iface"], s["gw"])
        return ""

    def stop(self):
        self.uplink = None

    def restart_side(self, side):
        self.restarts.append(side.tid)
        if self.side_fail:
            side.proc = None
            return self.side_fail
        side.proc = _Alive()
        return ""

    def set_detour(self, side, peer=None):
        self.detours.append((side.tid, peer))
        if self.side_fail:
            return self.side_fail
        main = self.main
        side.detour = None if peer is None else {
            "of": (main.tid, main.started), "link": self.link_of(main), "peer": peer}
        side.proc = _Alive()
        return ""

    def link_of(self, side):
        return {"port": LINK_PORTS[side.tid], "username": "u", "password": "p"}

    def mend_peer_routes(self):
        self.mends += 1
        return []

    def out_now(self):
        return self.out

    def set_out(self, tag):
        self.out = tag
        self.outs.append(tag)
        return True

    def away(self):
        return buildconfig.BLOCK_TAG if self.kill_switch else buildconfig.DIRECT_TAG

    def delay(self, _tag):
        return self.delay_ms


def _core(monkeypatch):
    core = service.Core.__new__(service.Core)
    core.lock = threading.Lock()
    core.busy = ""
    core.last_error = ""
    core.log_lock = threading.Lock()
    core._paths, core._ip_names = {}, {}
    core._log_at, core._side_after, core._side_noted = {}, {}, {}
    core._side_said, core._mend_tried = set(), {}
    core._side_heard, core._known_tried = {}, {}
    core._tests_lock, core._trial_locks = threading.Lock(), {}
    core.prober = probe.Prober()
    core.tunnel = _Tunnel(core.prober)
    core.logged = []
    monkeypatch.setattr(core, "log", core.logged.append)
    # Запасных конфигов нет: иначе сторож читал бы tunnels.json других тестов.
    monkeypatch.setattr(core, "_spares", lambda tid: (None, []))
    monkeypatch.setattr(core, "_any_conf", lambda: True)
    monkeypatch.setattr(core.prober, "probe_fast", lambda: None)
    monkeypatch.setattr(probe.Prober, "current_profile", staticmethod(lambda: "p"))
    monkeypatch.setattr(service, "RECONNECT_GAP", 0.0)
    # Новая сеть пропускает трафик, пока тест не скажет иначе: без сокетов наружу.
    monkeypatch.setattr(service.winnet, "passes_traffic", lambda _idx: True)
    _net(core, OFFICE)
    return core


def _sing_log(core, tmp_path, old=""):
    """Журнал основного sing-box: old — строки прошлого запуска до начала текущего."""
    path = tmp_path / "vpn.log"
    path.write_bytes(old.encode())
    core.tunnel.log_start = (str(path), len(old.encode()))
    return path


def _side_log(core, tmp_path, tid="home", old=""):
    """Журнал процесса туннеля: таймауты туннеля теперь там."""
    side = next(s for s in core.tunnel.sides if s.tid == tid)
    path = tmp_path / f"{tid}.log"
    path.write_bytes(old.encode())
    side.log_start = (str(path), len(old.encode()))
    return path


def _append(path, *lines):
    with open(path, "a", encoding="utf-8") as fh:
        fh.writelines(lines)


def _net(core, uplink):
    core.prober.set(iface=uplink[0], gw=uplink[1])


def _rounds(core, n, state=(None, 0, 0.0)):
    for _ in range(n):
        state = core._watch_once(*state)
    return state


def test_новая_сеть_после_устойчивых_кругов_переподключает(monkeypatch):
    core = _core(monkeypatch)
    _net(core, HOME)

    state = _rounds(core, service.UPLINK_SETTLE - 1)
    assert core.tunnel.starts == 0

    _rounds(core, 1, state)
    assert core.tunnel.starts == 1
    assert core.tunnel.uplink == HOME
    assert core.busy == ""
    # Ответы корпа и адрес выхода мерили старую сеть — нужна новая проверка.
    assert core.prober._remeasure.is_set()


def test_сеть_без_трафика_не_переподключает_пока_не_оживёт(monkeypatch):
    """14 октября в 15:17 сторож перешёл на Ethernet без сети и уронил VPN через Wi-Fi."""
    core = _core(monkeypatch)
    ready = {HOME[0]: False}
    monkeypatch.setattr(service.winnet, "passes_traffic", lambda idx: ready[idx])
    _net(core, HOME)

    state = _rounds(core, service.UPLINK_SETTLE * 3)

    assert core.tunnel.starts == 0
    assert core.tunnel.uplink == OFFICE
    # Одна строка, а не каждый круг.
    assert sum("не пропускает трафик" in line for line in core.logged) == 1

    ready[HOME[0]] = True
    _rounds(core, 1, state)

    assert core.tunnel.starts == 1
    assert core.tunnel.uplink == HOME


def test_сеть_без_трафика_пропала_счёт_кругов_снова(monkeypatch):
    core = _core(monkeypatch)
    monkeypatch.setattr(service.winnet, "passes_traffic", lambda idx: idx != HOME[0])
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE + 1)

    _net(core, OFFICE)
    state = _rounds(core, 1, state)

    assert state[:2] == (None, 0)
    assert any("так и не пропустив трафик" in line for line in core.logged)
    assert core.tunnel.starts == 0


def test_неудачное_переподключение_проверку_не_просит(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.fail = True
    core.tunnel.proc = _Proc()

    _rounds(core, 1)

    assert not core.prober._remeasure.is_set()


def test_мигание_сети_не_переподключает(monkeypatch):
    core = _core(monkeypatch)

    state = (None, 0, 0.0)
    for _ in range(5):
        _net(core, HOME)
        state = _rounds(core, service.UPLINK_SETTLE - 1, state)
        _net(core, OFFICE)
        state = _rounds(core, 1, state)

    assert core.tunnel.starts == 0


def test_пропавший_шлюз_не_переподключает(monkeypatch):
    core = _core(monkeypatch)
    core.prober.set(iface=None, gw="")

    _rounds(core, service.UPLINK_SETTLE * 3)

    assert core.tunnel.starts == 0


@pytest.mark.parametrize("back, starts", [(HOME, 1), (OFFICE, 0)])
def test_сеть_пропала_и_вернулась(monkeypatch, back, starts):
    """Вернулась другой — переподключаемся после устойчивых кругов, как при
    прямой смене; вернулась та же — туннель не трогаем."""
    core = _core(monkeypatch)
    core.prober.set(iface=None, gw="")
    state = _rounds(core, service.UPLINK_SETTLE * 2)

    _net(core, back)
    _rounds(core, service.UPLINK_SETTLE, state)

    assert core.tunnel.starts == starts
    assert core.tunnel.uplink == back


def test_сам_упавший_sing_box_поднимается_заново(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.proc = _Proc()

    _rounds(core, 1)

    assert core.tunnel.starts == 1


def test_маршруты_к_пирам_сверяются_каждый_круг(monkeypatch):
    core = _core(monkeypatch)

    _rounds(core, 3)

    assert core.tunnel.mends == 3


def test_на_новой_сети_и_без_шлюза_маршруты_не_сверяются(monkeypatch):
    """Ставить пира через старый шлюз бессмысленно: маршруты поставит переподключение."""
    core = _core(monkeypatch)
    _net(core, HOME)
    _rounds(core, service.UPLINK_SETTLE - 1)
    core.prober.set(iface=None, gw="")
    _rounds(core, 2)

    assert core.tunnel.mends == 0


def test_выключенный_туннель_не_трогаем(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.uplink = None
    core.tunnel.proc = _Proc()
    _net(core, HOME)

    _rounds(core, service.UPLINK_SETTLE * 3)

    assert core.tunnel.starts == 0


def test_повторы_после_неудачи_конечны(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.fail = True
    _net(core, HOME)

    _rounds(core, service.UPLINK_SETTLE + service.RECONNECT_TRIES * 3)

    assert core.tunnel.starts == 1 + service.RECONNECT_TRIES
    assert core._retry_left == 0


def test_поднялся_на_повторе_повторы_прекращаются(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.fail = True
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE)
    assert core.tunnel.starts == 1

    core.tunnel.fail = False
    _rounds(core, service.RECONNECT_TRIES * 3, state)

    assert core.tunnel.starts == 2
    assert core.tunnel.uplink == HOME
    assert core._retry_left == 0


def test_автоподключение_без_сети_ждёт_шлюз_и_повторяет(monkeypatch):
    """Служба стартует при загрузке раньше сети: 9 октября первая попытка упала
    на «нет маршрута по умолчанию», и VPN стоял выключенным до ручного включения."""
    core = _core(monkeypatch)
    core.tunnel.uplink = None
    core.prober.set(iface=None, gw="")
    core.server = types.SimpleNamespace(serve_forever=lambda: None)
    started = []

    def thread(target, kwargs=None, daemon=None):
        return types.SimpleNamespace(
            start=lambda: started.append((target, kwargs or {})))

    monkeypatch.setattr(service, "threading",
                        types.SimpleNamespace(Thread=thread, Event=threading.Event))
    monkeypatch.setattr(service.winnet, "on_error", None)
    monkeypatch.setattr(core, "_migrate", lambda: None)
    monkeypatch.setattr(service.Core, "autostart_enabled", staticmethod(lambda: True))
    core.start()
    target, kwargs = started[-1]

    core.tunnel.fail = True
    target(**kwargs)
    state = _rounds(core, service.UPLINK_SETTLE * 3)
    assert core.tunnel.starts == 1

    core.tunnel.fail = False
    _net(core, HOME)
    _rounds(core, 1, state)

    assert core.tunnel.starts == 2
    assert core.tunnel.uplink == HOME


def test_выключение_человеком_отменяет_повторы(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.fail = True
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE)
    assert core._retry_left == service.RECONNECT_TRIES

    assert core._do_stop() == {"ok": True}
    _rounds(core, service.RECONNECT_TRIES * 3, state)

    assert core.tunnel.starts == 1


def test_выключение_посреди_круга_новую_сеть_не_включает(monkeypatch):
    core = _core(monkeypatch)
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE - 1)
    # Последний круг ждёт проверку личного, а человек тем временем выключает.
    core.tunnel.out = buildconfig.DIRECT_TAG
    monkeypatch.setattr(core.tunnel, "delay", lambda _tag: core._do_stop() and None)

    _rounds(core, 1, state)

    assert core.tunnel.starts == 0
    assert core.tunnel.uplink is None


def _in_thread(fn, **kwargs):
    """fn в потоке; ответ ляжет в список, когда она закончится."""
    out = []
    t = threading.Thread(target=lambda: out.append(fn(**kwargs)), daemon=True)
    t.start()
    return t, out


def _held_start(core):
    """start туннеля ждёт, пока его отпустят; entered — он уже идёт."""
    entered, release = threading.Event(), threading.Event()
    start = core.tunnel.start

    def held(profile):
        entered.set()
        release.wait(5)
        return start(profile)

    core.tunnel.start = held
    return entered, release


def test_правка_во_время_полного_перезапуска_ждёт_его(monkeypatch):
    """Перезапуск правки отдавал замок между stop и start: вторая правка видела
    выключенный VPN («off»), а start, заставший её под замком, отказывал —
    и VPN оставался выключенным."""
    core = _core(monkeypatch)
    core.tunnel.live_plan = lambda: None
    entered, release = _held_start(core)
    first, first_out = _in_thread(core._apply)
    assert entered.wait(5)

    second, second_out = _in_thread(core._apply)
    second.join(0.2)
    assert second.is_alive()
    release.set()
    first.join(5)
    second.join(5)

    assert [first_out[0]["applied"], second_out[0]["applied"]] == ["full", "full"]
    assert core.tunnel.uplink == OFFICE


def test_повторное_включение_ждёт_идущее_и_отдаёт_его_итог(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.uplink = None
    entered, release = _held_start(core)
    first, first_out = _in_thread(core._do_start)
    assert entered.wait(5)

    second, second_out = _in_thread(core._do_start)
    second.join(0.2)
    assert second.is_alive()
    release.set()
    first.join(5)
    second.join(5)

    assert first_out == second_out == [{"ok": True}]
    assert core.tunnel.starts == 1


def test_без_конфигов_включать_нечего_и_это_не_ошибка(monkeypatch):
    """Свежая установка: трей и автоподключение жмут «Включить» сами —
    окно без last_error пишет «Добавьте конфиг», а не «Не запускается»."""
    core = _core(monkeypatch)
    core.tunnel.uplink = None
    core.last_error = "прежняя"
    monkeypatch.setattr(core, "_any_conf", lambda: False)
    core.tunnel.start = lambda _profile: pytest.fail("собирал без конфигов")

    out = core._do_start(retries=service.RECONNECT_TRIES, wake=True)

    assert out == {"ok": False, "error": service.NO_CONFS}
    assert core.last_error == "" and core._retry_left == 0 and core.busy == ""


def test_есть_ли_конфиги_по_папкам_туннелей(monkeypatch):
    monkeypatch.setattr(service.tunnels, "load",
                        lambda: {"tunnels": [{"id": "a"}, {"id": "b"}]})
    monkeypatch.setattr(service.tunnels, "list_confs",
                        lambda tid: ["x"] if tid == "b" else [])
    assert service.Core._any_conf() is True
    monkeypatch.setattr(service.tunnels, "list_confs", lambda tid: [])
    assert service.Core._any_conf() is False


def test_выключение_посреди_включения_отменяет_его(monkeypatch):
    """Включение идёт до 30 с, а «Выключить» отвечало «уже идёт»."""
    core = _core(monkeypatch)
    core.tunnel.uplink = None
    entered = threading.Event()

    def start(_profile):
        entered.set()
        return tunnel.CANCELLED if core.tunnel.cancel.wait(5) else ""

    core.tunnel.start = start
    began, out = _in_thread(core._do_start, retries=service.RECONNECT_TRIES)
    assert entered.wait(5)

    assert core._do_stop() == {"ok": True}
    began.join(5)

    assert out == [{"ok": True, "cancelled": True}]
    assert core.tunnel.uplink is None and core.last_error == ""
    assert core._retry_left == 0 and not core.tunnel.cancel.is_set()


def test_выключение_между_проверкой_и_повтором_повторы_не_зацикливает(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.fail = True
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE)
    starts = core.tunnel.starts
    real = service.time.monotonic
    stopped = []

    def monotonic():
        # «Выключить» успевает между проверкой счётчика повторов и его уменьшением.
        if not stopped:
            stopped.append(core._do_stop())
        return real()

    monkeypatch.setattr(service, "time", type(
        "T", (), {"monotonic": staticmethod(monotonic), "time": service.time.time}))

    _rounds(core, service.RECONNECT_TRIES * 3, state)

    assert core.tunnel.starts == starts
    assert core._retry_left == 0


def test_после_остановки_службы_туннель_не_поднимается(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.proc = _Proc()
    core.prober.stop_event.set()

    _rounds(core, 1)

    assert core.tunnel.starts == 0


def test_занятая_служба_сторожа_не_пускает(monkeypatch):
    core = _core(monkeypatch)
    core.busy = "включаю"
    _net(core, HOME)

    _rounds(core, service.UPLINK_SETTLE * 3)

    assert core.tunnel.starts == 0


# ------------------------------------------------------- мёртвый туннель

def test_таймауты_личного_уводят_выход_и_перезапускают_только_его(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, _timeout("34.117.59.81:443"), _timeout("172.217.23.238:443"))
    state = _rounds(core, 1)
    assert core.tunnel.restarts == []

    _append(log, _timeout("160.79.104.10:443"))
    _rounds(core, 1, state)

    assert core.tunnel.restarts == ["home"]
    assert core.tunnel.outs == [buildconfig.DIRECT_TAG]
    assert core.tunnel.starts == 0
    assert core.busy == ""


def test_таймауты_в_основном_журнале_туннель_не_трогают(monkeypatch, tmp_path):
    """Таймауты туннеля пишет его процесс; основной их не видит."""
    core = _core(monkeypatch)
    _append(_sing_log(core, tmp_path), *_three_dead())

    _rounds(core, 2)

    assert core.tunnel.restarts == [] and core.tunnel.starts == 0


def test_таймауты_к_одному_адресу_при_живой_задержке_не_трогают(monkeypatch, tmp_path):
    """Лежит один сайт, а не туннель: личный отвечает через свой socks."""
    core = _core(monkeypatch)
    core.tunnel.delay_ms = 120
    log = _side_log(core, tmp_path)
    _append(log, *[_timeout("160.79.104.10:443")] * 5)

    _rounds(core, 3)

    assert core.tunnel.restarts == [] and core.tunnel.outs == []


def test_таймауты_к_одному_адресу_без_задержки_уводят_выход_и_перезапускают(
        monkeypatch, tmp_path):
    """13:25 6 октября: таймауты шли к одному адресу, личный молчал."""
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, *[_timeout("2.16.106.32:80")] * 3)

    _rounds(core, 1)

    assert core.tunnel.restarts == ["home"]
    assert core.tunnel.outs == [buildconfig.DIRECT_TAG]


def test_таймауты_разных_туннелей_не_складываются(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    _append(_side_log(core, tmp_path, "work"), _timeout("10.10.0.5:443", "wg-work"))
    _append(_side_log(core, tmp_path), _timeout("34.117.59.81:443"),
            _timeout("172.217.23.238:443"))

    _rounds(core, 1)

    assert core.tunnel.restarts == []


def test_повтор_раньше_паузы_не_перезапускает(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, *_three_dead())
    state = _rounds(core, 1)
    assert core.tunnel.restarts == ["home"]

    core.tunnel.out = HOME_VIA
    _append(log, *_three_dead())
    _rounds(core, 3, state)

    assert core.tunnel.restarts == ["home"]
    # Выход уводим и внутри паузы: интернет перезапуска не ждёт.
    assert core.tunnel.out == buildconfig.DIRECT_TAG


def test_ошибки_прошлого_запуска_не_в_счёт(monkeypatch, tmp_path):
    """open_log продолжает свежий файл: строки до заголовка — чужой сеанс."""
    core = _core(monkeypatch)
    _side_log(core, tmp_path, old="".join(_three_dead()))

    _rounds(core, 3)

    assert core.tunnel.restarts == []


def test_недописанная_строка_дочитывается_на_следующем_круге(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    line = _timeout("9.9.9.9:443")
    _append(log, _timeout("1.1.1.1:443"), _timeout("8.8.8.8:443"), line[:40])
    state = _rounds(core, 1)
    assert core.tunnel.restarts == []

    _append(log, line[40:])
    _rounds(core, 1, state)

    assert core.tunnel.restarts == ["home"]


def test_упавший_корп_перезапускается_один(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.work.proc = _Proc()

    _rounds(core, 1)

    assert core.tunnel.restarts == ["work"]
    assert core.tunnel.outs == [] and core.tunnel.starts == 0
    [line] = _said(core, "перезапускаю процесс «Работа»")
    assert "код 1" in line
    assert core.prober._remeasure.is_set()


def test_упавший_личный_выход_напрямую_и_перезапуск(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.home.proc = _Proc()

    _rounds(core, 1)

    assert core.tunnel.outs == [buildconfig.DIRECT_TAG]
    assert core.tunnel.restarts == ["home"]
    assert core.prober.snapshot()["out"] == buildconfig.DIRECT_TAG


def test_мёртвый_конфиг_перезапускается_раз_в_паузу(monkeypatch):
    """Человек выбрал: без предела попыток, но не чаще DEAD_GAP."""
    core = _core(monkeypatch)
    core.tunnel.side_fail = "процесс «Личный» упал на старте: bad config"
    core.tunnel.home.proc = _Proc()
    state = _rounds(core, 5)
    assert core.tunnel.restarts == ["home"]
    assert _said(core, "bad config")
    assert len(_said(core, "жду")) == 1

    core._side_after["home"] = 0.0              # пауза прошла
    _rounds(core, 1, state)

    assert core.tunnel.restarts == ["home", "home"]
    assert core.tunnel.starts == 0


def test_ожил_личный_выход_возвращается_на_него(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.out = buildconfig.DIRECT_TAG
    core.tunnel.delay_ms = 120

    _rounds(core, 1)

    assert core.tunnel.outs == [HOME_VIA]
    assert core.tunnel.restarts == []
    assert _said(core, "«Личный» везёт (проверка за 120 мс)")
    assert core.prober.snapshot()["out"] == HOME_VIA


def test_упавший_личный_с_kill_switch_выход_в_block(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.kill_switch = True
    core.tunnel.home.proc = _Proc()

    _rounds(core, 1)

    assert core.tunnel.outs == [buildconfig.BLOCK_TAG]
    assert core.tunnel.restarts == ["home"]
    assert core.prober.snapshot()["out"] == buildconfig.BLOCK_TAG


def test_ожил_личный_выход_из_block_возвращается_на_него(monkeypatch):
    """Задержка меряется через via-<id>, а не через out: из block видно, что основной ожил."""
    core = _core(monkeypatch)
    core.tunnel.kill_switch = True
    core.tunnel.out = buildconfig.BLOCK_TAG
    core.tunnel.delay_ms = 120

    _rounds(core, 1)

    assert core.tunnel.outs == [HOME_VIA]
    assert core.prober.snapshot()["out"] == HOME_VIA


def test_личный_не_ожил_выход_остаётся_в_block(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.kill_switch = True
    core.tunnel.out = buildconfig.BLOCK_TAG

    _rounds(core, 1)

    assert core.tunnel.outs == []
    assert core.tunnel.restarts == ["home"]
    assert core.prober.snapshot()["out"] == buildconfig.BLOCK_TAG


def test_уведённый_выход_не_мечется_обратно_раньше_паузы(monkeypatch, tmp_path):
    """7 октября в 10:37 выход за 40 с прыгал direct ↔ personal-socks пять раз:
    одна удачная проверка через 2 с после увода возвращала трафик в полуживой туннель."""
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, *_three_dead())
    state = _rounds(core, 1)
    assert core.tunnel.outs == [buildconfig.DIRECT_TAG]

    core.tunnel.delay_ms = 300
    state = _rounds(core, 3, state)
    assert core.tunnel.outs == [buildconfig.DIRECT_TAG]

    core._back_at = 0.0                          # пауза прошла
    _rounds(core, 1, state)

    assert core.tunnel.outs == [buildconfig.DIRECT_TAG, HOME_VIA]


def test_живой_но_не_везущий_личный_перезапускается(monkeypatch):
    """Выход напрямую — трафика через личный нет, таймаутов не будет: решает проверка."""
    core = _core(monkeypatch)
    core.tunnel.out = buildconfig.DIRECT_TAG

    state = _rounds(core, 1)
    assert core.tunnel.restarts == ["home"]
    assert core.tunnel.outs == []

    core._side_after["home"] = 0.0
    _rounds(core, 3, state)                     # проверка не чаще BACK_EVERY
    assert core.tunnel.restarts == ["home"]


def test_выключение_посреди_круга_перезапуск_не_делает(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.work.proc = _Proc()
    core.lock.acquire()                          # идёт «Выключить»

    _rounds(core, 1)

    assert core.tunnel.restarts == []


def test_выключил_и_включил_посреди_круга_новый_туннель_не_перезапускает(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.out = buildconfig.DIRECT_TAG

    def delay(_tag):
        # Пока круг ждёт проверку личного, человек выключает и включает снова.
        core._do_stop()
        core._do_start()
        return None

    monkeypatch.setattr(core.tunnel, "delay", delay)

    _rounds(core, 1)

    assert core.tunnel.restarts == []


def test_таймауты_прочитанные_до_перезапуска_vpn_свежий_туннель_не_трогают(
        monkeypatch, tmp_path):
    """9 октября в 23:52 круг, заставший выключение и включение, досчитал
    таймауты прежних процессов и назвал свежий процесс «не запущенным»."""
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, *_three_dead())

    def mend():
        core._do_stop()
        core._do_start()

    monkeypatch.setattr(core.tunnel, "mend_peer_routes", mend)

    _rounds(core, 1)

    assert not _said(core, "перезапускаю процесс")
    assert core._side_after == {} and core.tunnel.outs == []


def test_правка_конфигов_ставит_круг_сторожа_на_паузу(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.work.proc = _Proc()
    core.lock.acquire()                          # идёт _apply: busy пуст

    _rounds(core, 1)

    assert core.tunnel.restarts == [] and core.tunnel.mends == 0


# ---------------------------------------------- запасные конфиги основного


HOME_T = {"id": "home", "name": "Личный", "active": "nl-1"}


def _spare_confs(monkeypatch, core, answers, during=None):
    """Запасные конфиги основного в порядке answers {имя: итог проверки}.
    (проверенные (имя, alone), сделанные активными (туннель, имя)); during — что
    случается, пока идёт проверка."""
    tried, made = [], []

    def run_test(t, name, alone=False):
        tried.append((name, alone))
        if during:
            during()
        return answers[name], ""

    monkeypatch.setattr(core, "_spares", lambda tid: (HOME_T, list(answers)))
    monkeypatch.setattr(core, "_run_test", run_test)
    monkeypatch.setattr(core, "_conf_stamp", lambda tid, name: None)
    monkeypatch.setattr(core, "_set_active",
                        lambda name, tunnel="": made.append((tunnel, name)) or {"ok": True})
    return tried, made


def test_основной_не_ожил_после_перезапуска_берётся_ответивший_запасной(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.out = buildconfig.DIRECT_TAG
    tried, made = _spare_confs(monkeypatch, core, {"nl-2": "error", "nl-3": "up"})
    home = core.tunnel.home

    def carried():
        # Процесс на запасном конфиге везёт.
        core.tunnel.delay_ms = 90
        return [(home, "")]

    seen = _reload(monkeypatch, core, carried)

    state = _rounds(core, 1)
    # До перезапуска сторожем запасные не трогаем: мог лежать процесс.
    assert core.tunnel.restarts == ["home"] and tried == []

    core._back_at = 0.0
    _rounds(core, 1, state)

    assert tried == [("nl-2", True), ("nl-3", True)]
    assert made == [("home", "nl-3")]
    assert len(seen) == 1 and seen[0]["locked"]
    assert core.tunnel.outs == [HOME_VIA]
    assert core.prober.snapshot()["out"] == HOME_VIA
    assert core.tunnel.restarts == ["home"]
    assert _said(core, "запасной nl-3.conf отвечает")
    assert core._main_kicked is False


def test_запасные_не_отвечают_выход_остаётся_в_block(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.kill_switch = True
    core.tunnel.out = buildconfig.BLOCK_TAG
    tried, made = _spare_confs(monkeypatch, core, {"nl-2": "error", "nl-3": "error"})
    state = _rounds(core, 1)

    core._back_at = 0.0
    state = _rounds(core, 1, state)
    assert len(tried) == 2 and made == []
    assert core.tunnel.outs == []
    assert _said(core, "не отвечают — выход остаётся block")

    core._back_at = 0.0                          # круг запасных — не чаще DEAD_GAP
    _rounds(core, 1, state)
    assert len(tried) == 2


def test_не_поднявшийся_основной_тоже_уходит_на_запасной(monkeypatch):
    """Процесс на этом конфиге не стартует — проверять задержку не через что."""
    core = _core(monkeypatch)
    core.tunnel.side_fail = "процесс «Личный» упал на старте: bad config"
    core.tunnel.home.proc = _Proc()
    tried, made = _spare_confs(monkeypatch, core, {"nl-2": "up"})
    _reload(monkeypatch, core, [(core.tunnel.home, "")])

    _rounds(core, 2)

    assert tried == [("nl-2", True)] and made == [("home", "nl-2")]


def test_команда_человека_посреди_проверки_запасной_не_берёт(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.out = buildconfig.DIRECT_TAG

    def human():
        core._human += 1

    tried, made = _spare_confs(monkeypatch, core, {"nl-2": "up", "nl-3": "up"}, during=human)
    state = _rounds(core, 1)

    core._back_at = 0.0
    _rounds(core, 1, state)

    assert tried == [("nl-2", True)] and made == []


def test_запасные_по_кругу_после_активного(monkeypatch):
    data = tunnels.validate({"tunnels": [{"id": "home", "name": "Личный", "mode": "all",
                                          "active": "b"}]})
    monkeypatch.setattr(service.tunnels, "load", lambda: data)
    monkeypatch.setattr(service.tunnels, "list_confs", lambda tid: ["a", "b", "c"])

    t, names = service.Core._spares("home")
    assert t["id"] == "home" and names == ["c", "a"]

    data["tunnels"][0]["active"] = ""
    assert service.Core._spares("home")[1] == ["a", "b", "c"]
    assert service.Core._spares("нет")[1] == []


# ------------------------------------------------------- правка конфигов


def _reload(monkeypatch, core, result):
    """reload_sides туннеля: отдаёт result и запоминает, что видел в этот момент.
    result None — live_plan тоже None: на лету не выйдет."""
    seen = []

    def reload_sides(plan=None, on_reject=None):
        seen.append({"busy": core.busy, "locked": core.lock.locked(), "plan": plan})
        core.tunnel.on_reject = on_reject
        return result() if callable(result) else result

    monkeypatch.setattr(core.tunnel, "live_plan",
                        lambda: None if result is None else "plan", raising=False)
    monkeypatch.setattr(core.tunnel, "reload_sides", reload_sides, raising=False)
    return seen


def test_правка_при_выключенном_vpn_ничего_не_поднимает(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.uplink = None
    seen = _reload(monkeypatch, core, [])

    assert core._apply() == {"ok": True, "applied": "off"}

    assert seen == [] and core.tunnel.starts == 0


def test_правка_перезапускает_только_сменившийся_туннель(monkeypatch):
    core = _core(monkeypatch)
    home, work = core.tunnel.home, core.tunnel.work
    core._dead_hits = ((0.0, home.tag, "1.1.1.1"), (0.0, work.tag, "10.53.0.4"))
    core._side_after = {"home": 9e9, "work": 9e9}
    core._side_noted = {"home": 9e9, "work": 9e9}
    seen = _reload(monkeypatch, core, [(home, "")])

    reply = core._apply()

    assert reply == {"ok": True, "applied": "sides", "restarted": ["home"]}
    # Трей не сереет: busy пуст, а замок держим — сторож ждёт. Сборка — та же, что
    # решила «на лету», второй раз не собирается.
    assert seen == [{"busy": "", "locked": True, "plan": "plan"}]
    assert core.tunnel.starts == 0
    # Таймауты и пауза перезапуска были у прежнего конфига.
    assert [h[1] for h in core._dead_hits] == [work.tag]
    assert core._side_after == {"work": 9e9} and core._side_noted == {"work": 9e9}
    assert core.prober._remeasure.is_set()


def test_правка_без_смены_процессов_проверку_не_просит(monkeypatch):
    core = _core(monkeypatch)
    _reload(monkeypatch, core, [])

    assert core._apply() == {"ok": True, "applied": "sides", "restarted": []}

    assert not core.prober._remeasure.is_set()


def test_ошибка_перезапуска_туннеля_в_ответе_и_журнале(monkeypatch):
    core = _core(monkeypatch)
    _reload(monkeypatch, core, [(core.tunnel.work, "«Работа»: не удалось разрешить x")])

    reply = core._apply()

    assert reply["ok"] is False and reply["applied"] == "sides"
    assert reply["error"] == "«Работа»: не удалось разрешить x"
    assert _said(core, "!! «Работа»: не удалось разрешить x")


def test_отвергнутый_rule_set_перезапускает_vpn(monkeypatch):
    """sing-box не принял правила после ответа правки — полный перезапуск под замком."""
    core = _core(monkeypatch)
    core.tunnel.proc = _Proc()
    _reload(monkeypatch, core, [])
    assert core._apply()["applied"] == "sides"
    first = core.tunnel.on_reject
    core._apply()                     # вторая правка файл уже не переписала
    locked = []
    monkeypatch.setattr(core, "_start_locked",
                        lambda: locked.append(core.lock.locked()) or {"ok": True})

    first()

    assert locked == [True] and not core.lock.locked()
    assert _said(core, "не принял правила на лету")


def test_отказ_rule_set_после_перезапуска_vpn_уже_не_нужен(monkeypatch):
    """Человек выключил и включил: новый основной прочёл rule-set сам."""
    core = _core(monkeypatch)
    core.tunnel.proc = _Proc()
    _reload(monkeypatch, core, [])
    core._apply()
    stale = core.tunnel.on_reject
    core.tunnel.proc = _Proc()
    starts = []
    monkeypatch.setattr(core, "_start_locked", lambda: starts.append(1) or {"ok": True})

    stale()

    assert starts == []
    assert not _said(core, "не принял правила на лету")


def test_сменился_основной_конфиг_полный_перезапуск(monkeypatch):
    core = _core(monkeypatch)
    _reload(monkeypatch, core, None)

    reply = core._apply()

    assert reply == {"ok": True, "applied": "full"}
    assert core.tunnel.starts == 1 and core.tunnel.uplink == OFFICE
    assert _said(core, "сменилось то, что держит основной процесс")


def test_полный_перезапуск_с_ask_только_спрашивает(monkeypatch):
    """Окно спрашивает человека до падения tun: служба ничего не трогает."""
    core = _core(monkeypatch)
    seen = _reload(monkeypatch, core, None)

    assert core._apply(ask=True) == {"ok": True, "applied": "ask"}

    assert seen == [] and core.tunnel.starts == 0 and core.tunnel.uplink == OFFICE
    assert not _said(core, "сменилось то, что держит основной процесс")


def test_правка_на_лету_с_ask_не_спрашивает(monkeypatch):
    core = _core(monkeypatch)
    _reload(monkeypatch, core, [(core.tunnel.home, "")])

    assert core._apply(ask=True) == {"ok": True, "applied": "sides", "restarted": ["home"]}


def test_правка_посреди_круга_сторожа_перезапуск_не_делает(monkeypatch):
    """Процесс, который правка как раз перезапускает, сторож увидел бы лежащим."""
    core = _core(monkeypatch)
    core.tunnel.out = buildconfig.DIRECT_TAG
    core.tunnel.work.proc = _Proc()
    _reload(monkeypatch, core, [(core.tunnel.work, "")])

    def delay(_tag):
        core._apply()
        return None

    monkeypatch.setattr(core.tunnel, "delay", delay)

    _rounds(core, 1)

    assert core.tunnel.restarts == []
    assert not _said(core, "перезапускаю процесс") and core._side_after == {}


def test_правка_пока_идёт_включение_отказ(monkeypatch):
    monkeypatch.setattr(service, "APPLY_WAIT", 0.01)
    core = _core(monkeypatch)
    core.busy = "включаю"
    core.lock.acquire()

    assert core._apply() == {"ok": False, "error": "уже идёт: включаю"}


def test_правка_ждёт_идущую_и_применяется(monkeypatch):
    """Второй щелчок тумблера посреди живой правки первого: флаг уже записан,
    apply дожидается замка, а не отвечает «уже идёт»."""
    core = _core(monkeypatch)
    core.tunnel.uplink = None
    core.lock.acquire()
    threading.Timer(0.1, core.lock.release).start()

    assert core._apply() == {"ok": True, "applied": "off"}


def _corp_dead():
    return (_timeout("172.15.0.228:3000", "wg-work"),
            _timeout("10.160.138.4:443", "wg-work"),
            _timeout("10.160.138.4:443", "wg-work"))


def _corp_says(monkeypatch, core, ip):
    asked = []
    monkeypatch.setattr(core.prober, "tunnel_answer",
                        lambda tid, **_kw: asked.append(tid) or ip)
    return asked


def test_таймауты_корпа_при_живом_корпе_не_перезапускают(monkeypatch, tmp_path):
    """6 октября в 12:03 таймауты корпа перезапустили sing-box, хотя корп работал."""
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path, "work")
    asked = _corp_says(monkeypatch, core, "10.20.0.4")
    _append(log, *_corp_dead())

    _rounds(core, 1)

    assert core.tunnel.restarts == []
    assert asked == ["work"]
    [line] = _said(core, "«Работа» отвечает")
    assert "wg-work" in line and "DNS 10.20.0.4" in line
    assert core._dead_hits == ()


def test_dns_ответил_без_адреса_не_перезапускает_и_пишет_один_раз(monkeypatch, tmp_path):
    """Правила от другой сети: таймауты пачка за пачкой, а сервер жив —
    раньше процесс перезапускался каждые DEAD_GAP."""
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path, "work")
    asked = _corp_says(monkeypatch, core, probe.DNS_NO_ADDR)

    for _ in range(2):
        _append(log, *_corp_dead())
        _rounds(core, 1)

    assert core.tunnel.restarts == []
    assert asked == ["work", "work"]
    [line] = _said(core, "«Работа» отвечает")
    assert f"DNS {probe.DNS_NO_ADDR}" in line and "не перезапускаю" in line


def test_dns_замолчал_после_ответа_перезапуск_и_снова_строка(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path, "work")
    answers = iter(["10.20.0.4", "", "10.20.0.4"])
    monkeypatch.setattr(core.prober, "tunnel_answer", lambda tid: next(answers))
    monkeypatch.setattr(service, "DEAD_GAP", 0.0)

    for _ in range(3):
        _append(log, *_corp_dead())
        _rounds(core, 1)

    assert core.tunnel.restarts == ["work"]
    assert len(_said(core, "«Работа» отвечает")) == 2


def test_таймауты_второго_по_списку_спрашивают_его_dns(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    lab = tunnel.Side("lab", "Лаб")
    lab.proc = _Alive()
    core.tunnel.sides.append(lab)
    log = _side_log(core, tmp_path, "lab")
    asked = _corp_says(monkeypatch, core, "10.30.0.4")
    _append(log, *(line.replace("wg-work", "wg-lab") for line in _corp_dead()))

    _rounds(core, 1)

    assert core.tunnel.restarts == []
    assert asked == ["lab"]
    assert _said(core, "«Лаб» отвечает")


def test_таймауты_корпа_при_молчащем_корпе_перезапускают_только_его(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path, "work")
    asked = _corp_says(monkeypatch, core, "")
    _append(log, *_corp_dead())

    _rounds(core, 1)

    assert core.tunnel.restarts == ["work"]
    assert core.tunnel.outs == [] and core.tunnel.starts == 0
    assert asked == ["work"]
    assert not _said(core, "отвечает")


def test_мёртвый_личный_корп_не_спрашивает(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    asked = _corp_says(monkeypatch, core, "10.20.0.4")
    _append(log, *_three_dead())

    _rounds(core, 1)

    assert core.tunnel.restarts == ["home"]
    assert asked == []


def test_живой_корп_не_сбивает_счёт_новой_сети(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path, "work")
    _corp_says(monkeypatch, core, "10.20.0.4")
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE - 1)

    _append(log, *_corp_dead())
    _rounds(core, 1, state)

    assert core.tunnel.starts == 1
    assert core.tunnel.uplink == HOME


def test_без_основного_упавший_по_списку_перезапускается_выход_не_трогаем(monkeypatch):
    """Нет туннеля «всё остальное»: selector out нет, выход уводить некуда."""
    core = _core(monkeypatch)
    core.tunnel.sides = [core.tunnel.work]
    core.tunnel.main_id = None
    core.tunnel.out = ""
    core.tunnel.delay_ms = 120
    core.tunnel.work.proc = _Proc()

    _rounds(core, 1)

    assert core.tunnel.restarts == ["work"]
    assert core.tunnel.outs == []
    assert core.prober._remeasure.is_set()


def test_пауза_перезапуска_у_каждого_туннеля_своя(monkeypatch):
    """Два туннеля «по списку»: пауза одного не держит другой."""
    core = _core(monkeypatch)
    second = tunnel.Side("work-2", "Склад")
    core.tunnel.sides.insert(1, second)
    core.tunnel.side_fail = "не поднялся"
    core.tunnel.work.proc = _Proc()
    second.proc = _Proc()
    state = _rounds(core, 1)
    assert core.tunnel.restarts == ["work", "work-2"]

    core._side_after["work-2"] = 0.0            # пауза прошла только у второго
    _rounds(core, 1, state)

    assert core.tunnel.restarts == ["work", "work-2", "work-2"]
    [line] = _said(core, "жду")
    assert "«Работа»" in line


# ------------------------------------------------------------ обход через основной

KOROLEV = "203.0.113.77"


def _peer_dns(monkeypatch, name="vpn.korolev.example", ip=KOROLEV):
    """Сервер в .conf по имени name; публичный DNS через вход основного отвечает ip.
    Отдаёт запросы: (порт входа, DNS, имя)."""
    asked = []
    monkeypatch.setattr(buildconfig, "peer_name", lambda tid: name)
    monkeypatch.setattr(probe, "_dns_via_proxy", lambda link, server, host, **_kw: (
        asked.append((link["port"], server, host)), ip)[1])
    return asked


def _work_dead(monkeypatch, core, tmp_path, age=1.0):
    """«Работа» запущен age секунд назад, в журнале таймауты, DNS туннеля молчит."""
    core.tunnel.work.started = time.monotonic() - age
    _corp_says(monkeypatch, core, "")
    _append(_side_log(core, tmp_path, "work"), *_corp_dead())


def _detoured(core, of_started=None, peer=KOROLEV):
    home = core.tunnel.home
    core.tunnel.work.detour = {
        "of": ("home", home.started if of_started is None else of_started),
        "link": core.tunnel.link_of(home), "peer": peer}


def test_свежий_по_списку_с_таймаутами_идёт_через_основной(monkeypatch, tmp_path):
    """14 октября из офисной подсети сервер wg0-korolev не отвечал напрямую,
    а через личный рукопожатие прошло сразу."""
    core = _core(monkeypatch)
    asked = _peer_dns(monkeypatch)
    _work_dead(monkeypatch, core, tmp_path)

    _rounds(core, 1)

    assert core.tunnel.detours == [("work", KOROLEV)]
    assert core.tunnel.restarts == []
    # Имя — у публичного DNS через вход основного: DNS офиса отдал бы внутренний адрес.
    assert asked == [(LINK_PORTS["home"], buildconfig.PUBLIC_DNS[0], "vpn.korolev.example")]
    [line] = _said(core, "подключаюсь к нему через «Личный»")
    assert "сервер «Работа» напрямую не отвечает" in line
    assert core.tunnel.work.detour["of"] == ("home", core.tunnel.home.started)
    assert core.prober._remeasure.is_set()


def test_давно_работавший_по_списку_сперва_перезапускается_напрямую(monkeypatch, tmp_path):
    """В других сетях всё как раньше: обход — только свежему процессу."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _work_dead(monkeypatch, core, tmp_path, age=service.DETOUR_WITHIN + 1)

    _rounds(core, 1)

    assert core.tunnel.restarts == ["work"]
    assert core.tunnel.detours == []
    assert _said(core, "перезапускаю процесс «Работа»")


def test_пока_выход_напрямую_обхода_нет(monkeypatch, tmp_path):
    """Основной не везёт: через него к серверу тоже не дойти."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    core.tunnel.out = buildconfig.DIRECT_TAG
    core._back_at = time.monotonic() + 1e9      # назад на личный ещё рано
    _work_dead(monkeypatch, core, tmp_path)

    _rounds(core, 1)

    assert core.tunnel.detours == []
    assert core.tunnel.restarts == ["work"]


def test_обход_не_помог_снова_напрямую(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    asked = _peer_dns(monkeypatch)
    _detoured(core)
    _work_dead(monkeypatch, core, tmp_path)

    _rounds(core, 1)

    assert core.tunnel.detours == [("work", None)]
    assert core.tunnel.work.detour is None
    assert asked == []
    assert _said(core, "через обход не работает — подключаю «Работа» напрямую")


def test_сменился_процесс_основного_обход_переподключается_через_новый(monkeypatch):
    """Прежний процесс основного погашен: туннель молчал бы до таймаутов."""
    core = _core(monkeypatch)
    asked = _peer_dns(monkeypatch)
    core.tunnel.home.started = 9.0
    _detoured(core, of_started=5.0)

    _rounds(core, 1)

    # Адрес сервера — прежний обхода: DNS не спрашиваем.
    assert core.tunnel.detours == [("work", KOROLEV)]
    assert asked == []
    assert core.tunnel.work.detour["of"] == ("home", 9.0)
    assert _said(core, "процесс «Личный» сменился — переподключаю «Работа» через него")


def test_неудачное_переподключение_обхода_не_повторяется_каждый_круг(monkeypatch):
    """Старый процесс в обходе жив: его таймауты разберёт сторож."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    core.tunnel.home.started = 9.0
    _detoured(core, of_started=5.0)
    core.tunnel.side_fail = "процесс «Работа» не открыл socks"

    state = _rounds(core, 3)
    assert core.tunnel.detours == [("work", KOROLEV)]

    core.tunnel.home.started = 12.0          # основной снова сменился
    _rounds(core, 1, state)

    assert core.tunnel.detours == [("work", KOROLEV)] * 2


def test_тот_же_процесс_основного_обход_не_трогаем(monkeypatch):
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _detoured(core)

    _rounds(core, 3)

    assert core.tunnel.detours == [] and core.tunnel.restarts == []


def test_лежащий_основной_обход_ждёт(monkeypatch):
    """Основной поднимет сторож; обход переподключится на следующем круге."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    core.tunnel.home.started = 9.0
    _detoured(core, of_started=5.0)
    core.tunnel.home.proc = _Proc()

    _rounds(core, 1)

    assert core.tunnel.detours == []
    assert core.tunnel.restarts == ["home"]


def test_без_основного_обход_снимается(monkeypatch):
    core = _core(monkeypatch)
    _detoured(core)
    core.tunnel.sides = [core.tunnel.work]
    core.tunnel.main_id = None
    core.tunnel.out = ""

    _rounds(core, 1)

    assert core.tunnel.detours == [("work", None)]
    assert _said(core, "основного туннеля для обхода нет — подключаю «Работа» напрямую")


def test_сервер_по_адресу_dns_не_спрашиваем(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    asked = _peer_dns(monkeypatch, name="198.51.100.10")
    _work_dead(monkeypatch, core, tmp_path)

    _rounds(core, 1)

    assert core.tunnel.detours == [("work", "198.51.100.10")]
    assert asked == []


def test_имя_сервера_не_резолвится_обход_на_прежний_адрес(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    asked = _peer_dns(monkeypatch, ip="")
    _work_dead(monkeypatch, core, tmp_path)

    _rounds(core, 1)

    assert core.tunnel.detours == [("work", "")]
    assert [server for _port, server, _name in asked] == list(buildconfig.PUBLIC_DNS)
    assert _said(core, "не резолвится — беру прежний адрес")


# ------------------------------------------- быстрая проверка и память обхода по сети

REAL = "192.0.2.10"
OFFICE_KEY = f"{REAL} {OFFICE[1]}"


def _real_ip(tmp_path, ip=REAL):
    (tmp_path / "real-ip").write_text(ip, encoding="utf-8")


def _work_silent(monkeypatch, core, age, said=""):
    """«Работа» запущен age секунд назад, таймаутов нет (нет трафика), его DNS
    отвечает said. Отдаёт запросы: (id, quick)."""
    core.tunnel.work.started = time.monotonic() - age
    asked = []
    monkeypatch.setattr(core.prober, "tunnel_answer", lambda tid, quick=False: (
        asked.append((tid, quick)), said)[1])
    return asked


def test_молчащий_свежий_по_списку_идёт_в_обход_без_таймаутов(monkeypatch, tmp_path):
    """14 октября обход включался через ~25 с: ждал трёх таймаутов по 15 с. Теперь
    хватает молчания DNS дольше SIDE_PROBE, и обход запоминается для сети."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _real_ip(tmp_path)
    asked = _work_silent(monkeypatch, core, service.SIDE_PROBE + 1)

    _rounds(core, 1)

    assert asked == [("work", True)]
    assert core.tunnel.detours == [("work", KOROLEV)]
    [line] = _said(core, "DNS «Работа» молчит")
    assert "подключаюсь к нему через «Личный»" in line
    assert json.loads((tmp_path / "detours.json").read_text()) == {OFFICE_KEY: ["work"]}


def test_молчит_меньше_SIDE_PROBE_ждём(monkeypatch):
    """Свежий рабочий процесс отвечает не сразу (13 октября — через 10 с)."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    asked = _work_silent(monkeypatch, core, 1.0)

    _rounds(core, 2)

    assert asked == [("work", True)] * 2
    assert core.tunnel.detours == [] and core.tunnel.restarts == []


def test_ответивший_процесс_больше_не_спрашиваем(monkeypatch):
    core = _core(monkeypatch)
    asked = _work_silent(monkeypatch, core, 1.0, said="10.20.0.4")

    _rounds(core, 3)

    assert asked == [("work", True)]
    assert core.tunnel.detours == []


@pytest.mark.parametrize("case", ["без DNS", "выход напрямую", "давно запущен"])
def test_молчание_не_уводит(monkeypatch, case):
    """Без DNS спросить нечем — как раньше, по таймаутам; при выходе мимо основного
    обходить не через что; давно работающий — не свежий."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    age = service.DETOUR_WITHIN + 1 if case == "давно запущен" else service.SIDE_PROBE + 1
    asked = _work_silent(monkeypatch, core, age, said=None if case == "без DNS" else "")
    if case == "выход напрямую":
        core.tunnel.out = buildconfig.DIRECT_TAG
        core._back_at = time.monotonic() + 1e9

    _rounds(core, 1)

    assert core.tunnel.detours == [] and core.tunnel.restarts == []
    assert asked == ([("work", True)] if case == "без DNS" else [])


def test_обход_молчит_снова_напрямую_и_забывается(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _real_ip(tmp_path)
    (tmp_path / "detours.json").write_text(json.dumps({OFFICE_KEY: ["work"]}))
    _detoured(core)
    _work_silent(monkeypatch, core, service.SIDE_PROBE + 1)

    _rounds(core, 1)

    assert core.tunnel.detours == [("work", None)]
    assert json.loads((tmp_path / "detours.json").read_text()) == {}
    assert _said(core, "забыл обход «Работа»")


def test_в_знакомой_сети_обход_сразу_после_запуска(monkeypatch, tmp_path):
    """После ⏻ и переподключения на ту же сеть обход не ищется заново."""
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _real_ip(tmp_path)
    (tmp_path / "detours.json").write_text(json.dumps({OFFICE_KEY: ["work"]}))
    asked = _work_silent(monkeypatch, core, 0.5)

    _rounds(core, 3)

    assert core.tunnel.detours == [("work", KOROLEV)]
    assert asked == []
    assert _said(core, "в этой сети сервер «Работа» напрямую не отвечал")


def test_неудачный_обход_по_памяти_один_раз_на_процесс(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _real_ip(tmp_path)
    (tmp_path / "detours.json").write_text(json.dumps({OFFICE_KEY: ["work"]}))
    _work_silent(monkeypatch, core, 0.5)
    core.tunnel.side_fail = "процесс «Работа» не открыл socks"

    _rounds(core, 3)

    assert core.tunnel.detours == [("work", KOROLEV)]


@pytest.mark.parametrize("real, memory", [
    (REAL, {f"{REAL} 192.168.20.1": ["work"]}),     # другая подсеть офиса
    ("198.51.100.1", {OFFICE_KEY: ["work"]}),       # тот же шлюз, другой выход
    ("", {f" {OFFICE[1]}": ["work"]}),               # внешний адрес не узнали
])
def test_память_обхода_только_для_той_же_сети(monkeypatch, tmp_path, real, memory):
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _real_ip(tmp_path, real)
    (tmp_path / "detours.json").write_text(json.dumps(memory))
    _work_silent(monkeypatch, core, 0.5)

    _rounds(core, 1)

    assert core.tunnel.detours == []


def test_без_внешнего_адреса_обход_не_запоминается(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _work_silent(monkeypatch, core, service.SIDE_PROBE + 1)

    _rounds(core, 1)

    assert core.tunnel.detours == [("work", KOROLEV)]
    assert not (tmp_path / "detours.json").exists()


def test_память_хранит_последние_сети(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    _peer_dns(monkeypatch)
    _real_ip(tmp_path)
    old = {f"198.51.100.{i} 10.0.0.1": ["work"] for i in range(service.DETOURS_KEEP)}
    (tmp_path / "detours.json").write_text(json.dumps(old))
    _work_silent(monkeypatch, core, service.SIDE_PROBE + 1)

    _rounds(core, 1)

    kept = json.loads((tmp_path / "detours.json").read_text())
    assert len(kept) == service.DETOURS_KEEP
    assert list(kept)[-1] == OFFICE_KEY
    assert "198.51.100.0 10.0.0.1" not in kept


# ------------------------------------------------------------ журнал сторожа

def _said(core, text):
    return [line for line in core.logged if text in line]


def _three_dead():
    return (_timeout("1.1.1.1:443"), _timeout("8.8.8.8:443"), _timeout("9.9.9.9:443"))


def test_мёртвый_туннель_в_журнале_с_тегом_числом_и_адресами(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, _timeout("34.117.59.81:443"), _timeout("172.217.23.238:443"),
            _timeout("34.117.59.81:443"))

    _rounds(core, 1)

    [line] = _said(core, "перезапускаю процесс «Личный»")
    assert "wg-home" in line
    assert "таймаутов 3" in line
    assert "172.217.23.238, 34.117.59.81" in line


def test_мёртвый_в_паузе_пишется_один_раз(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, *_three_dead())
    state = _rounds(core, 1)

    for _ in range(3):
        _append(log, *_three_dead())
        state = _rounds(core, 1, state)

    assert len(_said(core, "жду")) == 1


def test_в_паузе_мёртвого_смена_сети_переподключает(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _append(log, *_three_dead())
    state = _rounds(core, 1)
    assert core.tunnel.restarts == ["home"] and core.tunnel.starts == 0

    _net(core, HOME)
    for _ in range(service.UPLINK_SETTLE):
        _append(log, *_three_dead())
        state = _rounds(core, 1, state)

    assert core.tunnel.starts == 1


def test_таймауты_старой_сети_новому_туннелю_не_в_счёт(monkeypatch, tmp_path):
    """После переподключения туннель другой: два таймаута через мёртвый кабель
    и один на Wi-Fi — не три подряд."""
    core = _core(monkeypatch)
    core.tunnel.delay_ms = None
    log = _side_log(core, tmp_path)
    _append(log, _timeout("1.1.1.1:443"), _timeout("8.8.8.8:443"))
    state = _rounds(core, 1)
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE, state)
    assert core.tunnel.starts == 1

    _append(log, _timeout("9.9.9.9:443"))
    _rounds(core, 1, state)

    assert core.tunnel.restarts == []


def test_пауза_перезапуска_со_старой_сети_новую_не_держит(monkeypatch, tmp_path):
    """Личный перезапускали на кабеле; на Wi-Fi он упал — поднимать сразу, не ждать DEAD_GAP."""
    core = _core(monkeypatch)
    core.tunnel.home.proc = _Proc()
    state = _rounds(core, 1)
    assert core.tunnel.restarts == ["home"]
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE, state)
    assert core.tunnel.starts == 1

    core.tunnel.home.proc = _Proc()
    waits = len(_said(core, "перезапуска нет"))
    _rounds(core, 1, state)

    assert core.tunnel.restarts == ["home", "home"]
    assert len(_said(core, "перезапуска нет")) == waits


def test_без_шлюза_мёртвый_не_перезапускает(monkeypatch, tmp_path):
    """Wi-Fi отвалился или компьютер проснулся: таймауты от сети, а не от
    туннеля, и без сети перезапуск ничего не даст."""
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _net(core, (None, ""))
    _append(log, *_three_dead())
    core.tunnel.work.proc = _Proc()

    _rounds(core, 3)

    assert core.tunnel.restarts == [] and core.tunnel.outs == []
    assert not _said(core, "перезапускаю")
    assert not _said(core, "жду")


def test_таймауты_без_шлюза_не_копятся(monkeypatch, tmp_path):
    core = _core(monkeypatch)
    log = _side_log(core, tmp_path)
    _net(core, (None, ""))
    _append(log, _timeout("1.1.1.1:443"), _timeout("8.8.8.8:443"))
    state = _rounds(core, 1)

    _net(core, OFFICE)
    _append(log, _timeout("9.9.9.9:443"))
    _rounds(core, 1, state)

    assert core.tunnel.restarts == []


def test_выключенный_туннель_выход_в_статусе_пуст(monkeypatch):
    core = _core(monkeypatch)
    _rounds(core, 1)
    assert core.prober.snapshot()["out"] == HOME_VIA

    core.tunnel.uplink = None
    _rounds(core, 1)

    assert core.prober.snapshot()["out"] == ""


def test_команда_log_сводит_журналы_по_времени(monkeypatch, tmp_path):
    """Строки туннелей помечены именами тех, что подняли при включении."""
    core = _core(monkeypatch)
    monkeypatch.setattr(service.paths, "LOGS", str(tmp_path))
    (tmp_path / "vpn-2026-10-06_090000.log").write_text(
        "+0300 2026-10-06 09:00:01 INFO a\n+0300 2026-10-06 09:00:05 INFO c\n",
        encoding="utf-8")
    (tmp_path / "tunnel-work-2026-10-06_090000.log").write_text(
        "+0300 2026-10-06 09:00:03 ERROR b\n  трассировка\n", encoding="utf-8")
    (tmp_path / "tunnel-home-2026-10-06_090000.log").write_text(
        "+0300 2026-10-06 09:00:09 WARN d\n", encoding="utf-8")

    lines = core._tail_log(4)

    assert lines == ["[Работа] +0300 2026-10-06 09:00:03 ERROR b",
                     "[Работа]   трассировка",
                     "+0300 2026-10-06 09:00:05 INFO c",
                     "[Личный] +0300 2026-10-06 09:00:09 WARN d"]


def test_повторы_пишутся_с_номером(monkeypatch):
    core = _core(monkeypatch)
    core.tunnel.fail = True
    _net(core, HOME)

    _rounds(core, service.UPLINK_SETTLE + service.RECONNECT_TRIES * 3)

    n = service.RECONNECT_TRIES
    assert _said(core, f"повтор 1 из {n}")
    assert _said(core, f"повтор {n} из {n}")


def test_время_подъёма_туннеля_в_журнале(monkeypatch):
    core = _core(monkeypatch)

    assert core._do_start("p") == {"ok": True}
    core.tunnel.fail = True
    core._do_start("p")

    assert _said(core, "туннель поднят за")
    assert _said(core, "не поднялся (за")


def test_мигание_сети_в_журнале(monkeypatch):
    core = _core(monkeypatch)
    _net(core, HOME)
    state = _rounds(core, service.UPLINK_SETTLE - 1)
    _net(core, OFFICE)

    _rounds(core, 1, state)

    assert len(_said(core, "вижу новую сеть")) == 1
    [line] = _said(core, "сеть моргнула")
    assert f"шлюз {HOME[1]}" in line


def test_сон_между_кругами_в_журнале(monkeypatch):
    core = _core(monkeypatch)

    core._note_sleep(1000.0, 1000.0 + 25 * 60)

    [line] = _said(core, "компьютер спал")
    assert "25.0 мин" in line


def test_обычный_круг_сном_не_считается(monkeypatch):
    core = _core(monkeypatch)

    core._note_sleep(1000.0, 1000.0 + service.SLEEP_GAP)

    assert not _said(core, "спал")


class _Ticks(threading.Event):
    """stop_event сторожа: каждое ожидание сдвигает часы на очередную паузу,
    паузы кончились — стоп."""

    def __init__(self, monkeypatch, gaps):
        super().__init__()
        self.gaps, self.now = list(gaps), 1000.0
        monkeypatch.setattr(service.time, "time", lambda: self.now)

    def wait(self, _timeout):
        if not self.gaps:
            return True
        self.now += self.gaps.pop(0)
        return False


@pytest.mark.parametrize("after, starts", [(1, 0), (service.UPLINK_SETTLE, 1)])
def test_круги_до_сна_новую_сеть_не_подтверждают(monkeypatch, after, starts):
    """12 октября в 21:08: два круга до сна и один после переподключили
    в ещё не поднятую сеть. После сна новая сеть держится все круги заново."""
    core = _core(monkeypatch)
    _net(core, HOME)
    step = probe.FAST_EVERY
    core.prober.stop_event = _Ticks(
        monkeypatch, [step] * (service.UPLINK_SETTLE - 1) + [25 * 60] + [step] * (after - 1))

    core._watch()

    assert _said(core, "компьютер спал")
    assert core.tunnel.starts == starts, core.logged


# ------------------------------------------------------- куда что ходит

_PFX = "+0300 2026-10-05 22:45:33 INFO "


def _dns(qid, name, kind, value):
    return f"{_PFX}[{qid} 8ms] dns: exchanged {kind} {name}. 10 IN {kind} {value}\n"


def _conn(addr, tag="awg-personal", packet=""):
    return (f"{_PFX}[2734387054 1ms] endpoint/wireguard[{tag}]: "
            f"outbound {packet}connection to {addr}\n")


def _prematch(ip, tag="awg-personal"):
    return (f"{_PFX}[639815585 1ms] router: pre-match: forward udp connection "
            f"from 172.19.0.1 to {ip} via outbound/wireguard[{tag}]\n")


def _saved(path):
    return json.loads(path.read_text(encoding="utf-8"))["tags"]


def test_адреса_по_тегу_с_доменом_из_dns(monkeypatch, tmp_path, _paths_file):
    core = _core(monkeypatch)
    log = _sing_log(core, tmp_path)
    _append(log,
            _dns(1, "watson.events.data.microsoft.com", "CNAME",
                 "blobcollectorcommon.trafficmanager.net."),
            _dns(1, "blobcollectorcommon.trafficmanager.net", "A", "20.42.65.92"),
            _dns(2, "git.corp.example", "A", "10.20.0.4"),
            _conn("20.42.65.92:443"), _conn("20.42.65.92:443"),
            _conn("10.20.0.4:443", "wg-corp"),
            _conn("140.82.121.6:443"),
            _conn("172.15.0.110:53", "wg-corp", packet="packet "),
            _prematch("91.189.91.157"))

    assert core._do_stop() == {"ok": True}

    assert _saved(_paths_file) == {
        "awg-personal": {"watson.events.data.microsoft.com": 2,
                         "140.82.121.6": 1, "91.189.91.157": 1},
        "wg-corp": {"git.corp.example": 1},
    }


def test_статистика_пишется_раз_в_период(monkeypatch, tmp_path, _paths_file):
    core = _core(monkeypatch)
    log = _sing_log(core, tmp_path)
    _append(log, _conn("1.1.1.1:443"))
    state = _rounds(core, 1)
    assert _saved(_paths_file) == {"awg-personal": {"1.1.1.1": 1}}

    _append(log, _conn("1.1.1.1:443"))
    state = _rounds(core, 1, state)
    assert _saved(_paths_file) == {"awg-personal": {"1.1.1.1": 1}}

    core._paths_at = 0.0
    _rounds(core, 1, state)
    assert _saved(_paths_file) == {"awg-personal": {"1.1.1.1": 2}}


def test_статистика_копится_между_запусками_и_режется(monkeypatch, tmp_path,
                                                     _paths_file):
    monkeypatch.setattr(service, "PATHS_KEEP", 2)
    first = _core(monkeypatch)
    _append(_sing_log(first, tmp_path), _conn("1.1.1.1:443"), _conn("2.2.2.2:443"))
    first._do_stop()

    second = _core(monkeypatch)
    _append(_sing_log(second, tmp_path), _conn("2.2.2.2:443"), _conn("3.3.3.3:443"))
    second._do_stop()

    assert _saved(_paths_file) == {"awg-personal": {"2.2.2.2": 2, "1.1.1.1": 1}}


def test_испорченная_статистика_не_мешает_выключить(monkeypatch, tmp_path, _paths_file):
    _paths_file.write_text('{"tags": {"awg-personal": {"1.1.1.1": "x"}}}',
                           encoding="utf-8")
    core = _core(monkeypatch)
    _append(_sing_log(core, tmp_path), _conn("2.2.2.2:443"))

    assert core._do_stop() == {"ok": True}

    assert core.tunnel.uplink is None
    assert _saved(_paths_file) == {"awg-personal": {"2.2.2.2": 1}}
