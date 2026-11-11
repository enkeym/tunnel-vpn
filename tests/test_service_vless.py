
import base64
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
                        lambda url, allow_private=False: "\n".join(links))


def _tunnel(reply):
    return tunnels.find(tunnels.load(), reply["tunnel"])


def test_ссылка_делает_основной_vless_туннель():
    reply = _core().handle("add-link", {"link": _link()}, True)

    assert reply["ok"] is True and reply["place"] == "to"
    tunnel = _tunnel(reply)
    assert (tunnel["proto"], tunnel["mode"], tunnel["active"]) == (
        "vless", "all", reply["profile"])
    assert tunnel["enabled"] is False
    assert [p["id"] for p in subscriptions.list_profiles()] == [reply["profile"]]


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
    assert service.Core._spares(reply["tunnel"])[1] == [subscriptions.list_profiles()[1]["id"]]


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
