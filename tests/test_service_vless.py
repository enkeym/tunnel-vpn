
import base64
import os
import shutil
import threading
import types
from urllib.parse import quote, urlencode

import pytest

from tunnelvpn import netguard, paths, probe, service, subscriptions, tunnels, vless
from tunnelvpn.service import Core

UUID1 = "00000000-0000-4000-8000-000000000001"
UUID2 = "00000000-0000-4000-8000-000000000002"
UUID3 = "00000000-0000-4000-8000-000000000003"
UUID4 = "00000000-0000-4000-8000-000000000004"
HOST = "vpn.example.test"
SUB = "https://sub.example.test/sub"

WG_FULL = ("[Interface]\nPrivateKey = x\nAddress = 10.0.0.2/32\n\n"
           "[Peer]\nPublicKey = y\nEndpoint = 203.0.113.9:51820\n"
           "AllowedIPs = 0.0.0.0/0\n")
WG_PART = ("[Interface]\nPrivateKey = x\nAddress = 10.0.0.2/32\n\n"
           "[Peer]\nPublicKey = y\nEndpoint = 203.0.113.9:51820\n"
           "AllowedIPs = 10.0.0.0/24\n")


def _link(uuid=UUID1, name="Node", path="/ws"):
    query = {"type": "ws", "security": "tls", "encryption": "none",
             "path": path, "host": HOST, "sni": HOST, "fp": "chrome"}
    return (f"vless://{uuid}@{HOST}:443?{urlencode(query)}"
            f"#{quote(name)}")


def _reality(sid="a1b2"):
    key = base64.urlsafe_b64encode(bytes([1] * 32)).decode("ascii").rstrip("=")
    query = {"encryption": "none", "security": "reality", "type": "tcp",
             "sni": HOST, "fp": "chrome", "pbk": key, "sid": sid}
    return f"vless://{UUID1}@{HOST}:443?{urlencode(query)}#Reality"


def _clean():
    for folder in (paths.CONF_TUNNELS, paths.CONF_CORP, paths.CONF_PERSONAL):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(paths.CONF, exist_ok=True)
    for name in os.listdir(paths.CONF):
        path = os.path.join(paths.CONF, name)
        if os.path.isfile(path):
            os.remove(path)


@pytest.fixture(autouse=True)
def _empty_conf():
    _clean()
    yield
    _clean()


def _core():
    core = Core.__new__(Core)
    core.logged = []
    core.log = core.logged.append
    core.prober = probe.Prober()
    core.busy = ""
    core.last_error = ""
    core._retry_left = 0
    core.pending_apply = False
    core.tunnel = types.SimpleNamespace(main_id=None)
    return core


def _serve(monkeypatch, *links):
    monkeypatch.setattr(netguard, "open_https",
                        lambda url, allow_private=False, **kwargs: "\n".join(links))


def _transport_open(monkeypatch, failures, body):
    calls = []

    def check(url, allow_private=False):
        return {"url": url, "host": HOST, "port": 443, "path": "/sub",
                "ips": ["93.184.216.34"]}

    def exchange(info, ctx, timeout, max_bytes):
        calls.append(timeout)
        if len(calls) <= failures:
            raise OSError("нет маршрута")
        return 200, None, body.encode("utf-8")

    monkeypatch.setattr(netguard, "check_url", check)
    monkeypatch.setattr(netguard, "_tls_context", lambda cadata="": cadata)
    monkeypatch.setattr(netguard, "_exchange", exchange)
    return calls


def _sleeps(monkeypatch):
    slept = []
    clock = types.SimpleNamespace(monotonic=lambda: 0.0, sleep=slept.append)
    monkeypatch.setattr(subscriptions, "time", clock, raising=False)
    monkeypatch.setattr(netguard, "time", clock)
    return slept


def _tunnel(reply):
    return tunnels.find(tunnels.load(), reply["tunnel"])


def _clone_sub(sid, url, updated_at, uuid, serial):
    data = subscriptions.load()
    src = next(s for s in data["subscriptions"] if s["id"] == sid)
    mine = [p for p in data["profiles"]
            if p["source"] == {"subscription": sid}]
    new_id = f"s_{serial:016x}"
    data["subscriptions"].append(dict(src, id=new_id, url=url,
                                      updated_at=updated_at))
    for i, record in enumerate(mine):
        data["profiles"].append(
            dict(record, id=f"p_{(serial << 8) + i:016x}",
                 source={"subscription": new_id},
                 **{record["proto"]: dict(record[record["proto"]], uuid=uuid)}))
    subscriptions.save(data)
    return new_id


def _auto_core(monkeypatch, *links):
    core = _core()
    core.tunnel = types.SimpleNamespace(main_id=None, cancel=threading.Event())
    core._human = core._round_human = 0
    _serve(monkeypatch, "\n".join(links))
    reply = core.handle("add-subscription", {"url": SUB}, True)
    assert reply["ok"] is True, reply
    return core, reply


class _StartTunnel:
    def __init__(self):
        self.main = None
        self.uplink = None
        self.cancel = threading.Event()
        self.sides = []
        self.started = []

    def start(self, profile=""):
        self.started.append(profile)
        self.uplink = ("iface", "gw")
        return ""

    def stop(self):
        self.uplink = None

    def out_now(self):
        return ""


def _start_core(monkeypatch, *links):
    core, reply = _auto_core(monkeypatch, *links)
    core.tunnel = _StartTunnel()
    core.lock = threading.Lock()
    core._cmd_lock = threading.Lock()
    core._running = None
    core._cmd = 0
    core._human = core._round_human = 0
    core._retry_left = 0
    core._conf_checks = {}
    core._tests_lock = threading.Lock()
    core._kicked = ()
    core._fallback_at = {}
    core.prober = types.SimpleNamespace(
        stop_event=threading.Event(), new_route=lambda: None,
        set=lambda **_: None)
    core._mark = lambda *args, **kwargs: None
    core._probe_now = lambda: None
    core._test_active = lambda tid: None
    return core, reply


def _prober(core, monkeypatch, up, calls=None, during=None):
    def probe(t, name, **kwargs):
        if calls is not None:
            calls.append((name, kwargs.get("alone")))
        if during is not None:
            during(name)
        return {"result": "up" if name in up else "down", "answer": ""}
    monkeypatch.setattr(core, "_finish_test", probe)


def test_включение_через_службу_выбирает_рабочий_профиль(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    dead, live = subscriptions.list_profiles(sid)
    calls = []
    _prober(core, monkeypatch, {live["id"]}, calls)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result == {"ok": True}
    assert _tunnel({"tunnel": tid})["active"] == live["id"]
    assert calls == [(dead["id"], True), (live["id"], True), (dead["id"], True)]
    assert core.tunnel.started == [""]


def test_включение_через_службу_сохраняет_правила_и_kill_switch(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    data = tunnels.load()
    t = tunnels.find(data, tid)
    t["mode"] = "list"
    t["include"] = ["corp.example"]
    t["exclude"] = ["198.51.100.7/32"]
    t["exclude_apps"] = ["C:\\Games\\Steam\\steam.exe"]
    data["kill_switch"] = True
    tunnels.save(data)
    live = subscriptions.list_profiles(sid)[1]
    _prober(core, monkeypatch, {live["id"]})

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result == {"ok": True}
    data = tunnels.load()
    t = tunnels.find(data, tid)
    assert t["active"] == live["id"]
    assert (t["mode"], t["include"], t["exclude"], t["exclude_apps"]) == (
        "list", ["corp.example"], ["198.51.100.7/32"],
        ["C:\\Games\\Steam\\steam.exe"])
    assert data["kill_switch"] is True


def test_включение_не_меняет_работающий_активный_профиль(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    calls = []
    _prober(core, monkeypatch, {active}, calls)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result == {"ok": True}
    assert _tunnel({"tunnel": tid})["active"] == active
    assert calls == [(active, True)]
    assert core.tunnel.started == [""]


def test_включение_без_рабочих_профилей_честно_отказывает(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    _prober(core, monkeypatch, set())

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert "рабочих профилей" in result["error"]
    assert core.tunnel.started == []
    assert _tunnel({"tunnel": tid})["active"] == active


def test_включение_не_применяет_результат_после_бюджета(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    calls = []
    _prober(core, monkeypatch, {active}, calls)
    monkeypatch.setattr(service, "AUTO_BUDGET", -1.0)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert _tunnel({"tunnel": tid})["active"] == active
    assert calls == [(active, True)]


def test_включение_возвращает_оживший_активный_профиль(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    live = subscriptions.list_profiles(sid)[1]
    calls = []

    def probe(t, name, **kwargs):
        calls.append((name, kwargs.get("alone")))
        if name == active:
            return {"result": "down" if calls.count((active, True)) == 1 else "up",
                    "answer": ""}
        return {"result": "up", "answer": ""}
    monkeypatch.setattr(core, "_finish_test", probe)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result == {"ok": True}
    assert _tunnel({"tunnel": tid})["active"] == active
    assert [name for name, _ in calls] == [active, live["id"], active]
    assert core.tunnel.started == [""]


def test_включение_отбрасывает_результат_после_смены_поколения(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    live = subscriptions.list_profiles(sid)[1]

    def during(name):
        if name == live["id"]:
            core._human += 1

    _prober(core, monkeypatch, {live["id"]}, during=during)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert _tunnel({"tunnel": tid})["active"] == active
    assert core.tunnel.started == []


def test_включение_отбрасывает_результат_после_отмены(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    live = subscriptions.list_profiles(sid)[1]

    def during(name):
        if name == live["id"]:
            core.tunnel.cancel.set()

    _prober(core, monkeypatch, {live["id"]}, during=during)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert _tunnel({"tunnel": tid})["active"] == active
    assert core.tunnel.started == []


def test_включение_отбрасывает_результат_после_остановки_службы(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    live = subscriptions.list_profiles(sid)[1]

    def during(name):
        if name == live["id"]:
            core.prober.stop_event.set()

    _prober(core, monkeypatch, {live["id"]}, during=during)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert _tunnel({"tunnel": tid})["active"] == active
    assert core.tunnel.started == []


def test_включение_отбрасывает_результат_после_обновления_подписки(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    live = subscriptions.list_profiles(sid)[1]

    def during(name):
        if name == live["id"]:
            subscriptions.set_auto(sid, False)

    _prober(core, monkeypatch, {live["id"]}, during=during)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert "устарел" in result["error"]
    assert _tunnel({"tunnel": tid})["active"] == active
    assert core.tunnel.started == []


def test_включение_не_берёт_больше_лимита_кандидатов(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"),
                              _link(uuid=UUID3, name="Third", path="/c"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    last = subscriptions.list_profiles(sid)[2]
    monkeypatch.setattr(service, "AUTO_TRIES", 1)
    calls = []
    _prober(core, monkeypatch, {last["id"]}, calls)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert _tunnel({"tunnel": tid})["active"] == active
    assert [name for name, _ in calls] == [
        active, subscriptions.list_profiles(sid)[1]["id"]]


def test_включение_отбрасывает_результат_после_пропажи_кандидата(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    live = subscriptions.list_profiles(sid)[1]

    def during(name):
        if name == live["id"]:
            subscriptions.remove_profile(live["id"])

    _prober(core, monkeypatch, {live["id"]}, during=during)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert "устарел" in result["error"]
    assert _tunnel({"tunnel": tid})["active"] == active
    assert core.tunnel.started == []


def test_финальная_запись_авто_держит_замок_подписки(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    live = subscriptions.list_profiles(sid)[1]
    _prober(core, monkeypatch, {live["id"]})
    real_save = tunnels.save
    entered, done = threading.Event(), threading.Event()

    def save(data):
        t = tunnels.find(data, tid)
        if t["active"] == live["id"] and not entered.is_set():
            entered.set()

            def off():
                core.handle("set-subscription-auto",
                            {"id": sid, "on": False}, True)
                done.set()
            threading.Thread(target=off, daemon=True).start()
            assert not done.wait(0.2)
        return real_save(data)

    monkeypatch.setattr(tunnels, "save", save)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result == {"ok": True}
    assert done.wait(2)
    assert _tunnel({"tunnel": tid})["active"] == live["id"]
    assert subscriptions.list_subscriptions()[0]["auto"] is False


def test_неудача_запуска_после_зелёной_пробы_возвращает_прежний_профиль(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    live = subscriptions.list_profiles(sid)[1]
    _prober(core, monkeypatch, {live["id"]})

    def start(profile=""):
        core.tunnel.started.append(profile)
        return "sing-box завершился сам"
    monkeypatch.setattr(core.tunnel, "start", start)

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False and result["error"] == "sing-box завершился сам"
    assert _tunnel({"tunnel": tid})["active"] == active
    assert core.tunnel.uplink is None


def test_автозапуск_при_неудачном_автоподборе_взводит_повторы(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    assert _tunnel({"tunnel": tid})["enabled"] is False
    _prober(core, monkeypatch, set())

    result = core._do_start(retries=service.RECONNECT_TRIES, wake=True, auto=True)

    assert result["ok"] is False
    assert core._retry_left == service.RECONNECT_TRIES
    assert _tunnel({"tunnel": tid})["enabled"] is True
    assert core.tunnel.started == []


def test_плитка_при_неудачном_автоподборе_возвращает_флаги(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    _prober(core, monkeypatch, set())

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result["ok"] is False
    assert _tunnel({"tunnel": tid})["enabled"] is False
    assert core.tunnel.started == []


def _fallback_core(monkeypatch, *links):
    core, reply = _start_core(monkeypatch, *links)
    side = types.SimpleNamespace(tid=reply["tunnel"], title="Основной",
                                 alive=lambda: False)
    monkeypatch.setattr(core, "_failover_main", lambda verify=False: None)
    return core, reply, side


def test_отказ_основного_не_берёт_запасной_после_выключения_авто(monkeypatch):
    core, reply, side = _fallback_core(
        monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    live = subscriptions.list_profiles(sid)[1]
    calls, taken = [], []

    def probe(t, name, **kwargs):
        calls.append(name)
        subscriptions.set_auto(sid, False)
        return {"result": "up", "answer": ""}
    monkeypatch.setattr(core, "_finish_test", probe)
    monkeypatch.setattr(core, "_take_spare",
                        lambda *a, **k: taken.append((a, k)) or {"ok": True})

    assert core._fallback(side) is False
    assert calls == [live["id"]]
    assert taken == []


def test_отказ_основного_не_превышает_лимит_автопроб(monkeypatch):
    links = [_link()] + [
        _link(uuid=f"00000000-0000-4000-8000-00000000000{i}",
              name=f"Node{i}", path=f"/{i}") for i in range(2, 7)]
    core, reply, side = _fallback_core(monkeypatch, *links)
    sid = reply["subscription"]
    subscriptions.set_auto(sid, True)
    monkeypatch.setattr(service, "AUTO_TRIES", 2)
    calls, taken = [], []

    def probe(t, name, **kwargs):
        calls.append(name)
        return {"result": "down", "answer": ""}
    monkeypatch.setattr(core, "_finish_test", probe)
    monkeypatch.setattr(core, "_take_spare",
                        lambda *a, **k: taken.append((a, k)) or {"ok": True})

    assert core._fallback(side) is False
    assert len(calls) == 2
    assert taken == []
    assert "лимит" in core.last_error


def test_отказ_основного_не_берёт_запасной_после_бюджета(monkeypatch):
    core, reply, side = _fallback_core(
        monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    sid = reply["subscription"]
    subscriptions.set_auto(sid, True)
    live = subscriptions.list_profiles(sid)[1]
    clock = {"t": 1000.0}
    monkeypatch.setattr(service, "time",
                        types.SimpleNamespace(monotonic=lambda: clock["t"]))
    calls, taken = [], []

    def probe(t, name, **kwargs):
        calls.append(name)
        clock["t"] += service.AUTO_BUDGET + 1
        return {"result": "up", "answer": ""}
    monkeypatch.setattr(core, "_finish_test", probe)
    monkeypatch.setattr(core, "_take_spare",
                        lambda *a, **k: taken.append((a, k)) or {"ok": True})

    assert core._fallback(side) is False
    assert calls == [live["id"]]
    assert taken == []


def test_запасной_не_берётся_если_авто_выключено_до_записи(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    dead, live = subscriptions.list_profiles(sid)
    marks = core._auto_snapshot(tid)[1]
    subscriptions.set_auto(sid, False)
    applied = []
    monkeypatch.setattr(core, "_apply_locked",
                        lambda **kwargs: applied.append(kwargs))

    result = core._take_spare(dead, live["id"], locked=True, auto=marks)

    assert result["ok"] is False and result.get("cancelled") is True
    assert applied == []
    assert _tunnel({"tunnel": tid})["active"] == dead["id"]


def test_ошибка_применения_резерва_возвращает_прежний_активный(monkeypatch):
    core, reply, side = _fallback_core(
        monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    dead = _tunnel({"tunnel": tid})["active"]
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: {"result": "up", "answer": ""})
    monkeypatch.setattr(core, "_start_locked",
                        lambda **k: {"ok": False, "error": "не поднялся"})

    assert core._fallback(side) is False
    assert _tunnel({"tunnel": tid})["active"] == dead
    assert core.last_error == "не поднялся"


def test_неподтверждённый_резерв_возвращает_прежний_активный(monkeypatch):
    core, reply, side = _fallback_core(
        monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    dead = _tunnel({"tunnel": tid})["active"]
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: {"result": "up", "answer": ""})
    monkeypatch.setattr(core, "_spare_works", lambda tid: False)

    assert core._fallback(side) is False
    assert _tunnel({"tunnel": tid})["active"] == dead


def test_отмена_резерва_не_затирает_ручной_выбор(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    dead, live = subscriptions.list_profiles(sid)
    marks = core._auto_snapshot(tid)[1]
    core.tunnel.uplink = ("iface", "gw")
    monkeypatch.setattr(core, "_apply_locked",
                        lambda **k: {"ok": True, "applied": "sides"})

    def works(tid_):
        data = tunnels.load()
        tunnels.find(data, tid)["active"] = dead["id"]
        tunnels.save(data)
        core._cmd += 1
        return True
    monkeypatch.setattr(core, "_spare_works", works)

    result = core._take_spare(dead, live["id"], locked=True, auto=marks)

    assert result["ok"] is False and result.get("cancelled") is True
    assert _tunnel({"tunnel": tid})["active"] == dead["id"]


def test_выключение_авто_между_чтениями_не_снимает_guard(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    real_auto = core._auto_profiles
    reads = {"n": 0}

    def auto_profiles(t, data=None):
        reads["n"] += 1
        result = real_auto(t, data)
        if reads["n"] == 1:
            subscriptions.set_auto(sid, False)
        return result
    monkeypatch.setattr(core, "_auto_profiles", auto_profiles)
    taken = []
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: {"result": "up", "answer": ""})
    monkeypatch.setattr(core, "_take_spare",
                        lambda *a, **k: taken.append((a, k)) or {"ok": True})

    result = core._try_spares(tid, core._take_spare, lambda: False)

    assert result["ok"] is False and result.get("cancelled") is True
    assert taken == []


def test_применение_с_авто_не_запускает_автопроход(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("apply проверял профили")))
    monkeypatch.setattr(core, "_spares",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("apply перебирал запасные")))

    result = core._start_locked(failover=False)

    assert result == {"ok": True}
    assert core.tunnel.started == [""]
    assert _tunnel({"tunnel": tid})["active"] == active


def test_ручная_смена_профиля_с_авто_не_запускает_автопроход(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    live = subscriptions.list_profiles(sid)[1]
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("set-active проверял профили")))

    result = core.handle("set-active", {"tunnel": tid, "name": live["id"]}, True)

    assert result == {"ok": True, "tunnel": tid, "active": live["id"]}
    assert _tunnel({"tunnel": tid})["active"] == live["id"]


def test_переключатель_авто_только_записывает_настройку(monkeypatch):
    core, reply = _auto_core(monkeypatch, _link(),
                             _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    active = _tunnel({"tunnel": tid})["active"]
    before = len(tunnels.load()["tunnels"])
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("переключатель проверял профили")))

    result = core.handle("set-subscription-auto", {"id": sid, "on": True}, True)

    assert result == {"ok": True, "auto": True}
    assert subscriptions.list_subscriptions()[0]["auto"] is True
    assert len(tunnels.load()["tunnels"]) == before
    assert _tunnel({"tunnel": tid})["active"] == active


def test_переключатель_авто_выключается_без_проверок(monkeypatch):
    core, reply = _auto_core(monkeypatch, _link(),
                             _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    subscriptions.set_auto(sid, True)
    active = _tunnel({"tunnel": tid})["active"]
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("выключение проверяло профили")))

    result = core.handle("set-subscription-auto", {"id": sid, "on": False}, True)

    assert result == {"ok": True, "auto": False}
    assert subscriptions.list_subscriptions()[0]["auto"] is False
    assert _tunnel({"tunnel": tid})["active"] == active


def test_ручной_режим_подписки_не_проверяет_при_включении(monkeypatch):
    core, reply = _start_core(monkeypatch, _link(),
                              _link(uuid=UUID2, name="Second", path="/b"))
    sid, tid = reply["subscription"], reply["tunnel"]
    active = _tunnel({"tunnel": tid})["active"]
    monkeypatch.setattr(core, "_finish_test",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("ручной режим проверял профили")))

    result = core.handle("start-only", {"tunnel": tid}, True)

    assert result == {"ok": True}
    assert _tunnel({"tunnel": tid})["active"] == active
    assert core.tunnel.started == [""]


def test_ссылка_делает_основной_vless_туннель():
    reply = _core().handle("add-link", {"link": _link()}, True)

    assert reply["ok"] is True and reply["place"] == "to"
    tunnel = _tunnel(reply)
    assert (tunnel["proto"], tunnel["mode"], tunnel["active"]) == (
        "vless", "all", reply["profile"])
    assert tunnel["enabled"] is False
    assert [p["id"] for p in subscriptions.list_profiles()] == [reply["profile"]]


def test_ссылка_vless_без_фоновой_проверки():
    reply = _core().handle("add-link", {"link": _link()}, True)

    assert reply["job"] == "" and reply["checking"] is False


def test_ссылка_не_включает_пустой_туннель():
    tunnels.save({"tunnels": [{"id": "proxy", "name": "Proxy", "mode": "all",
                               "proto": "vless", "enabled": True}]})

    reply = _core().handle("add-link", {"link": _link(), "tunnel": "proxy"}, True)

    assert reply["ok"] is True
    assert _tunnel(reply)["active"] == reply["profile"]
    assert _tunnel(reply)["enabled"] is False


def test_та_же_ссылка_не_плодит_профиль_и_туннель():
    core = _core()

    first = core.handle("add-link", {"link": _link()}, True)
    second = core.handle("add-link", {"link": _link()}, True)

    assert second["profile"] == first["profile"]
    assert second["tunnel"] == first["tunnel"]
    assert len(subscriptions.list_profiles()) == 1
    assert len(tunnels.load()["tunnels"]) == 1


def test_массовая_вставка_создаёт_запасные_и_оставляет_первый_активным():
    core = _core()
    links = "\n".join((_link(), _link(uuid=UUID2, name="Second", path="/b"), _link()))

    preview = core.handle("preview", {"link": links}, True)
    reply = core.handle("add-link", {"link": links}, True)

    assert preview["profiles"] == 2
    assert reply["profiles"] == reply["added"] == 2
    assert _tunnel(reply)["active"] == reply["profile"]
    assert len(subscriptions.list_profiles()) == 2
    assert core._spares(reply["tunnel"])[1] == [subscriptions.list_profiles()[1]["id"]]


def test_удаление_последнего_vless_профиля_может_сохранить_или_убрать_правила():
    core = _core()
    first = core.handle("add-link", {"link": _link()}, True)
    data = tunnels.load()
    data["tunnels"][0]["exclude"] = ["example.test"]
    tunnels.save(data)

    kept = core.handle("remove-profile", {"id": first["profile"],
                                           "tunnel": first["tunnel"],
                                           "drop_tunnel": False}, True)

    assert kept["ok"] is True
    assert _tunnel(first)["exclude"] == ["example.test"]
    second = core.handle("add-link", {"link": _link()}, True)
    dropped = core.handle("remove-profile", {"id": second["profile"],
                                              "tunnel": second["tunnel"],
                                              "drop_tunnel": True}, True)

    assert dropped["ok"] is True
    assert tunnels.load()["tunnels"] == []


def test_удаление_профиля_помечает_затронут_ли_активный_основной():
    core = _core()
    first = core.handle("add-link", {"link": _link()}, True)
    core.handle("add-link", {"link": _link(uuid=UUID2, name="Second",
                                            path="/b")}, True)
    data = tunnels.load()
    data["tunnels"][0]["enabled"] = True
    tunnels.save(data)
    active = _tunnel(first)["active"]
    spare = next(p["id"] for p in subscriptions.list_profiles()
                 if p["id"] != active)

    assert core.handle("remove-profile", {"id": spare}, True)["main"] is False
    assert core.handle("remove-profile", {"id": active}, True)["main"] is True


def test_place_new_заводит_второй_туннель_по_списку():
    core = _core()
    main = core.handle("add-link", {"link": _link()}, True)

    other = core.handle("add-link",
                        {"link": _link(uuid=UUID2, name="Second", path="/b"),
                         "place": "new"}, True)

    assert other["ok"] is True and other["place"] == "new"
    assert other["tunnel"] != main["tunnel"]
    assert _tunnel(main)["mode"] == "all"
    assert _tunnel(other)["mode"] == "list"
    assert _tunnel(other)["active"] == other["profile"]


def test_ссылка_в_wg_туннель_отклоняется():
    tunnels.save({"tunnels": [{"id": "home", "name": "Личный", "mode": "all"}]})

    reply = _core().handle("add-link", {"link": _link(), "tunnel": "home"}, True)

    assert reply["ok"] is False and "не туннель с подписками" in reply["error"]
    assert tunnels.find(tunnels.load(), "home")["active"] == ""


def test_ссылка_берёт_маршрутизацию_действующего_основного():
    tunnels.save({"tunnels": [{
        "id": "home", "name": "Личный", "mode": "all",
        "include": ["corp.example"],
        "exclude": ["bank.example", "192.0.2.0/24"],
        "exclude_apps": ["C:\\Games\\steam.exe"],
    }]})

    reply = _core().handle("add-link", {"link": _link()}, True)

    assert reply["ok"] is True
    data = tunnels.load()
    vless = tunnels.find(data, reply["tunnel"])
    wg = tunnels.find(data, "home")
    assert (vless["mode"], vless["enabled"], wg["mode"]) == ("all", False, "all")
    for key in ("include", "exclude", "exclude_apps"):
        assert vless[key] == wg[key]


def test_вторая_ссылка_не_трогает_основной_и_его_правила():
    tunnels.save({"tunnels": [{"id": "home", "name": "Личный", "mode": "all",
                               "exclude": ["bank.example"]}]})
    core = _core()
    first = core.handle("add-link", {"link": _link()}, True)

    second = core.handle("add-link",
                         {"link": _link(uuid=UUID2, name="Second", path="/b")}, True)

    data = tunnels.load()
    assert second["ok"] is True and second["tunnel"] == first["tunnel"]
    assert len(data["tunnels"]) == 2
    main = tunnels.find(data, second["tunnel"])
    assert (main["mode"], main["enabled"], main["exclude"], main["active"]) == (
        "all", False, ["bank.example"], first["profile"])
    assert tunnels.find(data, "home")["mode"] == "all"


def test_ссылка_идёт_в_основную_группу_а_не_в_туннель_по_списку():
    core = _core()
    main = core.handle("add-link", {"link": _link()}, True)
    listed = core.handle("add-link",
                         {"link": _link(uuid=UUID2, name="Second", path="/b"),
                          "place": "new"}, True)
    assert _tunnel(listed)["mode"] == "list"
    data = tunnels.load()
    data["tunnels"].reverse()
    tunnels.save(data)

    added = core.handle("add-link", {"link": _link(uuid=UUID3, name="Third",
                                                    path="/c")}, True)

    assert added["ok"] and added["tunnel"] == main["tunnel"]
    assert len(tunnels.load()["tunnels"]) == 2
    assert _tunnel(listed)["active"] == listed["profile"]


def test_подписка_делает_первый_профиль_активным(monkeypatch):
    _serve(monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))

    reply = _core().handle("add-subscription",
                           {"url": SUB, "name": "Моя"}, True)

    assert reply["ok"] is True and reply["profiles"] == 2
    tunnel = _tunnel(reply)
    assert tunnel["proto"] == "vless"
    assert tunnel["enabled"] is False
    assert tunnel["active"] == reply["profile"]
    sub = subscriptions.list_subscriptions()[0]
    assert sub["id"] == reply["subscription"]
    assert sub["name"] == "Моя"
    assert sub["allow_private"] is False


def test_новая_подписка_не_переключает_выбранный_профиль(monkeypatch):
    core = _core()
    first = core.handle("add-link", {"link": _link()}, True)
    _serve(monkeypatch, _link(uuid=UUID2, name="Second", path="/b"))

    added = core.handle("add-subscription", {"url": SUB}, True)

    assert added["ok"] is True
    assert added["tunnel"] == first["tunnel"]
    assert _tunnel(first)["active"] == first["profile"]


def test_повторное_добавление_подписки_дополняет_только_новыми_ссылками(monkeypatch):
    _serve(monkeypatch, _link(name="First"), _link(uuid=UUID2, name="Same", path="/b"))
    core = _core()
    first = core.handle("add-subscription", {"url": SUB, "name": "Моя"}, True)
    sid = first["subscription"]
    subscriptions.set_auto(sid, True)
    existing = subscriptions.list_profiles(sid)
    other_link = _reality("a1b2")
    manual = core.handle("add-link", {"link": other_link}, True)
    assert manual["ok"]
    tunnel_data = tunnels.load()

    _serve(monkeypatch, _link(name="Renamed"),
           _link(uuid=UUID3, name="Same", path="/c"), other_link)
    reply = core.handle("add-subscription", {"url": SUB, "name": "Другая"}, True)

    assert reply["ok"] is True
    assert reply["subscription"] == sid
    assert reply["profiles"] == 3
    assert reply["added"] == 1
    profiles = subscriptions.list_profiles(sid)
    assert profiles[:2] == existing
    assert profiles[2]["name"] == "Same"
    kept = subscriptions.find_profile(subscriptions.load(), manual["profile"])
    assert kept["source"] == {"manual": True}
    stored = subscriptions.list_subscriptions()
    assert len(stored) == 1 and stored[0]["name"] == "Моя" and stored[0]["auto"]
    assert tunnels.load() == tunnel_data
    again = core.handle("add-subscription", {"url": SUB}, True)
    assert again["ok"] and again["added"] == 0
    assert subscriptions.list_profiles(sid) == profiles
    assert tunnels.load() == tunnel_data


def test_повторное_добавление_подписки_не_восстанавливает_удалённый_туннель(monkeypatch):
    _serve(monkeypatch, _link())
    core = _core()
    first = core.handle("add-subscription", {"url": SUB}, True)
    data = tunnels.load()
    data["tunnels"] = []
    tunnels.save(data)

    _serve(monkeypatch, _link(uuid=UUID2, name="Second", path="/b"))
    reply = core.handle("add-subscription", {"url": SUB}, True)

    assert reply["ok"] is True
    assert reply["subscription"] == first["subscription"]
    assert reply["profiles"] == 2
    assert reply["added"] == 1
    assert tunnels.load()["tunnels"] == []


def test_повтор_add_после_транспортного_сбоя_дополняет_и_не_меняет_профиль(monkeypatch):
    original = netguard.open_https
    _serve(monkeypatch, _link(name="First"))
    core = _core()
    first = core.handle("add-subscription", {"url": SUB, "name": "Моя"}, True)
    sid = first["subscription"]
    existing = subscriptions.list_profiles(sid)
    tunnel_data = tunnels.load()
    stored = subscriptions.list_subscriptions()
    monkeypatch.setattr(netguard, "open_https", original)
    body = "\n".join((_link(name="First"),
                      _link(uuid=UUID2, name="Second", path="/b")))
    slept = _sleeps(monkeypatch)
    calls = _transport_open(monkeypatch, 2, body)

    again = core.handle("add-subscription", {"url": SUB}, True)

    assert again["ok"] is True and again["added"] == 1
    assert again["subscription"] == sid
    profiles = subscriptions.list_profiles(sid)
    assert profiles[:1] == existing
    assert [p["name"] for p in profiles] == ["First", "Second"]
    assert tunnels.load() == tunnel_data
    assert [(s["name"], s["url"], s["auto"])
            for s in subscriptions.list_subscriptions()] == [
        (s["name"], s["url"], s["auto"]) for s in stored]
    assert calls[0] > 9.0 and len(calls) == 3
    assert slept == [1.0]


def test_повтор_add_при_недоступном_источнике_не_меняет_данные(monkeypatch):
    original = netguard.open_https
    _serve(monkeypatch, _link(name="First"))
    core = _core()
    first = core.handle("add-subscription", {"url": SUB, "name": "Моя"}, True)
    sid = first["subscription"]
    existing = subscriptions.list_profiles(sid)
    tunnel_data = tunnels.load()
    monkeypatch.setattr(netguard, "open_https", original)
    slept = _sleeps(monkeypatch)
    calls = _transport_open(monkeypatch, 10 ** 6, "")

    reply = core.handle("add-subscription", {"url": SUB}, True)

    assert reply["ok"] is False and "недоступен" in reply["error"]
    assert subscriptions.list_profiles(sid) == existing
    assert tunnels.load() == tunnel_data
    assert calls[0] > 9.0 and len(calls) == 10
    assert slept == [1.0, 2.0, 4.0, 8.0]


def test_подписка_только_по_https():
    reply = _core().handle("add-subscription",
                           {"url": "http://sub.example.test/sub"}, True)

    assert reply["ok"] is False and "HTTPS" in reply["error"]


def test_обновление_помечает_пропавший_профиль_и_хранит_его(monkeypatch):
    _serve(monkeypatch, _link(), _link(uuid=UUID2, name="Gone", path="/b"))
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)

    _serve(monkeypatch, _link())
    reply = core.handle("update-subscription",
                        {"id": added["subscription"]}, True)

    assert reply == {"ok": True, "id": added["subscription"], "added": 0,
                     "updated": 1, "missing": 1, "total": 2}
    gone = next(p for p in subscriptions.list_profiles() if p["name"] == "Gone")
    assert gone["missing"] is True
    assert _tunnel(added)["active"] == added["profile"]


def test_обновление_reality_сохраняет_выбранный_профиль(monkeypatch):
    _serve(monkeypatch, _reality("a1b2"))
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)

    _serve(monkeypatch, _reality("c3d4"))
    reply = core.handle("update-subscription",
                        {"id": added["subscription"]}, True)

    assert reply["added"] == 0
    assert reply["missing"] == 0
    profiles = subscriptions.list_profiles()
    assert [p["id"] for p in profiles] == [added["profile"]]
    assert profiles[0]["vless"]["short_id"] == "c3d4"
    assert _tunnel(added)["active"] == added["profile"]


def test_удаление_подписки_снимает_выбор(monkeypatch):
    _serve(monkeypatch, _link())
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)

    reply = core.handle("remove-subscription",
                        {"id": added["subscription"]}, True)

    assert reply == {"ok": True, "id": added["subscription"]}
    assert subscriptions.list_profiles() == []
    assert _tunnel(added)["active"] == ""


def test_удаление_дублей_оставляет_свежую_подписку(monkeypatch):
    _serve(monkeypatch, _link())
    core = _core()
    first = core.handle("add-subscription", {"url": SUB}, True)
    _serve(monkeypatch, _link(uuid=UUID2, name="Other", path="/other"))
    other = core.handle("add-subscription",
                        {"url": "https://sub.example.test/other"}, True)
    fresh = _clone_sub(first["subscription"], "https://sub.example.test/new",
                       "2999-01-01T00:00:00+00:00", UUID4, 0xc0)

    reply = core.handle("dedupe-subscriptions", {}, True)

    assert reply["ok"] is True
    assert reply["removed"] == [first["subscription"]]
    assert reply["kept"] == [fresh]
    assert {s["id"] for s in subscriptions.load()["subscriptions"]} == {
        fresh, other["subscription"]}


def test_удаление_активного_профиля_берёт_первый_оставшийся(monkeypatch):
    _serve(monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)
    rest = next(p["id"] for p in subscriptions.list_profiles()
                if p["id"] != added["profile"])

    assert core.handle("remove-profile", {"id": added["profile"]},
                       True)["ok"] is True
    assert _tunnel(added)["active"] == rest

    assert core.handle("remove-profile", {"id": rest}, True)["ok"] is True
    assert _tunnel(added)["active"] == ""


def test_выбор_другого_профиля_и_отказ_неизвестному(monkeypatch):
    _serve(monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)
    rest = next(p["id"] for p in subscriptions.list_profiles()
                if p["id"] != added["profile"])

    chosen = core.handle("set-active",
                         {"tunnel": added["tunnel"], "name": rest}, True)
    assert chosen["ok"] is True
    assert _tunnel(added)["active"] == rest

    missing = core.handle("set-active",
                          {"tunnel": added["tunnel"],
                           "name": "p_ffffffffffffffff"}, True)
    assert missing["ok"] is False and "нет профиля" in missing["error"]


def test_частные_адреса_подписки_разрешаются_флагом(monkeypatch):
    _serve(monkeypatch, _link())
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)

    reply = core.handle("set-allow-private",
                        {"id": added["subscription"], "on": True}, True)

    assert reply == {"ok": True, "subscription": added["subscription"],
                     "allow_private": True}
    assert subscriptions.list_subscriptions()[0]["allow_private"] is True


def test_список_подписок_отдаёт_профили_и_vless_туннель(monkeypatch):
    _serve(monkeypatch, _link())
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)

    reply = core.handle("list-subscriptions", {}, True)

    assert reply["ok"] is True
    assert [s["id"] for s in reply["subscriptions"]] == [added["subscription"]]
    assert [p["id"] for p in reply["profiles"]] == [added["profile"]]
    assert reply["tunnels"] == [{"id": added["tunnel"],
                                 "name": _tunnel(added)["name"],
                                 "active": added["profile"]}]


def test_экспорт_ссылок_по_профилю_подписке_и_всем(monkeypatch):
    _serve(monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)
    manual = core.handle("add-link", {"link": _link(name="Manual", path="/manual")}, True)

    profile = core.handle("export-links", {"scope": "profile",
                                           "id": added["profile"]}, True)
    group = core.handle("export-links", {"scope": "subscription",
                                         "id": added["subscription"]}, True)
    all_links = core.handle("export-links", {"scope": "all"}, True)

    assert profile == {"ok": True, "text": vless.link(subscriptions.find_profile(
        subscriptions.load(), added["profile"])), "profiles": 1}
    assert group["profiles"] == 2 and "#Second" in group["text"]
    assert all_links["profiles"] == 3 and "#Manual" in all_links["text"]
    assert manual["profile"] in [p["id"] for p in subscriptions.list_profiles()]


def test_экспорт_включает_hysteria2(monkeypatch):
    core = _core()
    added = core.handle("add-link", {
        "link": "hysteria2://secret@hy.example.test:443?security=tls"
                "&sni=hy.example.test&alpn=h3#Hy2"}, True)

    reply = core.handle("export-links", {"scope": "profile",
                                         "id": added["profile"]}, True)

    assert reply["profiles"] == 1
    assert reply["text"].startswith("hysteria2://")
    assert reply["text"].endswith("#Hy2")


def test_статус_показывает_профиль_вместо_файлов(monkeypatch):
    core = _core()
    added = core.handle("add-link", {"link": _link()}, True)
    monkeypatch.setattr(core, "_singbox_version", lambda: "")
    monkeypatch.setattr(core, "_last_results", lambda *_a: {})

    status = core._status()

    tunnel = status["tunnels"][0]
    assert tunnel["proto"] == "vless"
    assert tunnel["confs"] == [added["profile"]]
    assert tunnel["active"] == added["profile"]
    assert tunnel["full"] is True
    assert tunnel["awg"] == {added["profile"]: False}
    assert status["main_v6"] is False


def test_чтение_конфига_vless_отдаёт_каноническую_ссылку():
    core = _core()
    added = core.handle("add-link", {"link": _link()}, True)

    reply = core.handle("read-config",
                        {"tunnel": added["tunnel"], "name": added["profile"]}, True)

    profile = subscriptions.find_profile(subscriptions.load(), added["profile"])
    assert reply == {"ok": True, "text": vless.canonical(profile)}


def test_перенос_и_удаление_conf_для_vless_отклоняются():
    core = _core()
    added = core.handle("add-link", {"link": _link()}, True)

    moved = core.handle("move-config",
                        {"tunnel": added["tunnel"], "name": added["profile"],
                         "to": "new"}, True)
    removed = core.handle("remove-config",
                          {"tunnel": added["tunnel"],
                           "name": added["profile"]}, True)

    assert moved["ok"] is False and "VLESS" in moved["error"]
    assert removed["ok"] is False and "remove-profile" in removed["error"]


def test_wg_конфиг_на_весь_трафик_ожидает_включения_после_vless():
    core = _core()
    vless = core.handle("add-link", {"link": _link()}, True)
    core.handle("set-enabled", {"tunnel": vless["tunnel"], "on": True}, True)
    data = tunnels.load()
    data["tunnels"][0]["exclude"] = ["example.test"]
    tunnels.save(data)

    reply = core._add_config("nl", WG_FULL)

    assert reply["ok"] is True and reply["place"] == "all"
    assert reply["tunnel"] != vless["tunnel"]
    assert _tunnel(reply)["mode"] == "all"
    assert _tunnel(reply)["enabled"] is False
    assert _tunnel(reply)["exclude"] == ["example.test"]
    assert _tunnel(vless)["mode"] == "all"
    assert tunnels.main_on(tunnels.load())["id"] == vless["tunnel"]

    switched = core.handle("set-enabled", {"tunnel": reply["tunnel"], "on": True}, True)

    assert switched["ok"] is True
    assert tunnels.main_on(tunnels.load())["id"] == reply["tunnel"]
    assert _tunnel(vless)["enabled"] is False


def test_общее_включение_не_поднимает_два_основных_туннеля():
    core = _core()
    vless = core.handle("add-link", {"link": _link()}, True)
    wg = core._add_config("nl", WG_FULL)
    core.handle("set-enabled", {"tunnel": vless["tunnel"], "on": False}, True)

    result = core._wake_all()

    assert result == ""
    assert tunnels.main_on(tunnels.load())["id"] == vless["tunnel"]
    assert _tunnel(wg)["enabled"] is False


def test_полный_wg_конфиг_не_становится_запасным_в_частичном_wg_туннеле():
    core = _core()
    core.handle("add-link", {"link": _link()}, True)
    existing = core._add_config("old", WG_PART)

    reply = core._add_config("new", WG_FULL)

    assert reply["ok"] is True and reply["tunnel"] != existing["tunnel"]
    assert _tunnel(reply)["active"] == "new"
    assert _tunnel(reply)["mode"] == "all"
    assert _tunnel(reply)["enabled"] is False
    assert _tunnel(existing)["mode"] == "list"


def test_частичный_wg_конфиг_идёт_своим_туннелем_при_основном_vless():
    core = _core()
    main = core.handle("add-link", {"link": _link()}, True)

    reply = core._add_config("yt", WG_PART)

    assert reply["ok"] is True and reply["place"] == "list"
    assert reply["tunnel"] != main["tunnel"]
    assert _tunnel(reply)["proto"] == "wg"
    assert _tunnel(reply)["mode"] == "list"


def test_перенос_wg_конфига_в_основной_при_основном_vless_берёт_свой_протокол():
    core = _core()
    vless = core.handle("add-link", {"link": _link()}, True)
    core.handle("set-enabled", {"tunnel": vless["tunnel"], "on": True}, True)
    data = tunnels.load()
    tunnels.find(data, vless["tunnel"])["exclude"] = ["example.test"]
    tunnels.save(data)
    wg = core._add_config("nl", WG_FULL)
    listed = core._move_config(wg["tunnel"], "nl", "new", drop_tunnel=True)
    assert _tunnel(listed)["mode"] == "list"

    moved = core.handle("move-config",
                        {"tunnel": listed["tunnel"], "name": "nl",
                         "to": "all", "drop_tunnel": True}, True)

    assert moved["ok"] is True
    assert moved["tunnel"] != vless["tunnel"]
    main = _tunnel(moved)
    assert (main["proto"], main["mode"], main["enabled"]) == ("wg", "all", False)
    assert main["exclude"] == ["example.test"]
    assert tunnels.list_confs(moved["tunnel"], "wg") == ["nl"]
    assert tunnels.list_confs(vless["tunnel"], "wg") == []
    assert tunnels.find(tunnels.load(), listed["tunnel"]) is None
    assert tunnels.main_on(tunnels.load())["id"] == vless["tunnel"]


def test_vless_при_основном_wg_становится_своим_основным_с_правилами():
    core = _core()
    wg = core._add_config("nl", WG_FULL)
    core.handle("set-enabled", {"tunnel": wg["tunnel"], "on": True}, True)
    data = tunnels.load()
    tunnels.find(data, wg["tunnel"])["include"] = ["corp.test"]
    tunnels.find(data, wg["tunnel"])["exclude"] = ["example.test"]
    tunnels.save(data)

    added = core.handle("add-link", {"link": _link()}, True)

    assert added["tunnel"] != wg["tunnel"]
    vless = _tunnel(added)
    assert (vless["proto"], vless["mode"], vless["enabled"]) == (
        "vless", "all", False)
    assert (vless["include"], vless["exclude"]) == (
        ["corp.test"], ["example.test"])
    assert _tunnel(wg)["enabled"] is True
    assert tunnels.main_on(tunnels.load())["id"] == wg["tunnel"]
    assert tunnels.list_confs(added["tunnel"], "vless") == [added["profile"]]


def test_предпросмотр_ссылки_ничего_не_пишет():
    core = _core()

    reply = core.handle("preview", {"link": _link(name="Проверка")}, True)

    assert reply == {"ok": True, "type": "link", "name": "Проверка",
                     "host": HOST, "port": 443, "profiles": 1,
                     "invalid": 0, "duplicates": 0}
    assert UUID1 not in str(reply)
    assert subscriptions.load() == subscriptions.empty()
    assert tunnels.load()["tunnels"] == []


def test_предпросмотр_подписки_ничего_не_пишет(monkeypatch):
    _serve(monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))
    core = _core()

    reply = core.handle("preview", {"url": SUB, "name": "Моя"}, True)

    assert reply == {"ok": True, "type": "uri-list", "name": "Моя",
                     "host": "sub.example.test", "port": 443, "profiles": 2}
    assert UUID1 not in str(reply) and UUID2 not in str(reply)
    assert subscriptions.load() == subscriptions.empty()
    assert tunnels.load()["tunnels"] == []


def test_предпросмотр_без_аргументов_объясняет():
    reply = _core().handle("preview", {}, True)

    assert reply["ok"] is False and "ссылка" in reply["error"]


def test_vless_в_только_выбранные_остаётся_переключаемым():
    """Пустой список у VLESS — тоже легальное состояние: профиль не уходит
    запасным, вкладки листа остаются."""
    core = _core()
    main = core.handle("add-link", {"link": _link()}, True)
    tid = main["tunnel"]

    r = core._set_tunnel(tid, {"mode": "list", "include": "", "include_apps": ""})

    assert r["ok"] and r["mode"] == "list" and "moved" not in r
    vless = _tunnel({"tunnel": tid})
    assert vless["mode"] == "list" and vless["pinned"] is True
    work = next(x for x in core._get_tunnels()["tunnels"] if x["id"] == tid)
    assert work["full"] is True and work["switchable"] is True

    back = core._set_tunnel(tid, {"mode": "all"})
    assert back["ok"] and back["mode"] == "all"
    assert "pinned" not in _tunnel({"tunnel": tid})
