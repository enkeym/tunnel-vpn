"""VLESS в службе: ссылка и подписка кладут профили в VLESS-туннель.

Одна ссылка заводит профиль и туннель, подписка — профили в том же туннеле.
Обновление не теряет исчезнувшие профили, удаление снимает выбор, а .conf
в такой туннель не кладётся: у него нет файлов, только профили.
"""

import os
import shutil
import types
from urllib.parse import quote, urlencode

import pytest

from tunnelvpn import netguard, paths, probe, service, subscriptions, tunnels, vless
from tunnelvpn.service import Core

UUID1 = "00000000-0000-4000-8000-000000000001"
UUID2 = "00000000-0000-4000-8000-000000000002"
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
                        lambda url, allow_private=False: "\n".join(links))


def _tunnel(reply):
    return tunnels.find(tunnels.load(), reply["tunnel"])


def test_ссылка_делает_основной_vless_туннель():
    reply = _core().handle("add-link", {"link": _link()}, True)

    assert reply["ok"] is True and reply["place"] == "to"
    tunnel = _tunnel(reply)
    assert (tunnel["proto"], tunnel["mode"], tunnel["active"]) == (
        "vless", "all", reply["profile"])
    assert [p["id"] for p in subscriptions.list_profiles()] == [reply["profile"]]


def test_та_же_ссылка_не_плодит_профиль_и_туннель():
    core = _core()

    first = core.handle("add-link", {"link": _link()}, True)
    second = core.handle("add-link", {"link": _link()}, True)

    assert second["profile"] == first["profile"]
    assert second["tunnel"] == first["tunnel"]
    assert len(subscriptions.list_profiles()) == 1
    assert len(tunnels.load()["tunnels"]) == 1


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

    assert reply["ok"] is False and "не VLESS" in reply["error"]
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
    assert [t["id"] for t in data["tunnels"] if t["mode"] == "all"] == [reply["tunnel"]]
    assert (vless["mode"], wg["mode"]) == ("all", "list")
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
    assert (main["mode"], main["exclude"], main["active"]) == (
        "all", ["bank.example"], second["profile"])
    assert tunnels.find(data, "home")["mode"] == "list"


def test_подписка_делает_первый_профиль_активным(monkeypatch):
    _serve(monkeypatch, _link(), _link(uuid=UUID2, name="Second", path="/b"))

    reply = _core().handle("add-subscription",
                           {"url": SUB, "name": "Моя"}, True)

    assert reply["ok"] is True and reply["profiles"] == 2
    tunnel = _tunnel(reply)
    assert tunnel["proto"] == "vless"
    assert tunnel["active"] == reply["profile"]
    sub = subscriptions.list_subscriptions()[0]
    assert sub["id"] == reply["subscription"]
    assert sub["name"] == "Моя"
    assert sub["allow_private"] is False


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


def test_удаление_подписки_снимает_выбор(monkeypatch):
    _serve(monkeypatch, _link())
    core = _core()
    added = core.handle("add-subscription", {"url": SUB}, True)

    reply = core.handle("remove-subscription",
                        {"id": added["subscription"]}, True)

    assert reply == {"ok": True, "id": added["subscription"]}
    assert subscriptions.list_profiles() == []
    assert _tunnel(added)["active"] == ""


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
    assert status["main_v6"] is True


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


def test_wg_конфиг_на_весь_трафик_не_лезет_в_основной_vless():
    core = _core()
    core.handle("add-link", {"link": _link()}, True)

    reply = core.handle("add-config", {"name": "nl", "text": WG_FULL}, True)

    assert reply["ok"] is False and "VLESS" in reply["error"]
    assert len(tunnels.load()["tunnels"]) == 1


def test_частичный_wg_конфиг_идёт_своим_туннелем_при_основном_vless():
    core = _core()
    main = core.handle("add-link", {"link": _link()}, True)

    reply = core._add_config("yt", WG_PART)

    assert reply["ok"] is True and reply["place"] == "list"
    assert reply["tunnel"] != main["tunnel"]
    assert _tunnel(reply)["proto"] == "wg"
    assert _tunnel(reply)["mode"] == "list"


def test_предпросмотр_ссылки_ничего_не_пишет():
    core = _core()

    reply = core.handle("preview", {"link": _link(name="Проверка")}, True)

    assert reply == {"ok": True, "type": "link", "name": "Проверка",
                     "host": HOST, "port": 443, "profiles": 1}
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
