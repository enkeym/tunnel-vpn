import io
import json
import os
import threading
import zipfile
from urllib.parse import quote, urlencode

import pytest

from conftest import NoTimer, SyncThread
from tunnelvpn import exports, ipc, paths, proxy, subscriptions, tunnels, window
from tunnelvpn.service import Core

WG = ("[Interface]\nPrivateKey = wg-private\nAddress = 10.8.0.2/32\n"
      "# пометка человека\n\n[Peer]\nPublicKey = wg-public\n"
      "Endpoint = 203.0.113.9:51820\nAllowedIPs = 0.0.0.0/0\n")
UUID1 = "00000000-0000-4000-8000-000000000001"
UUID2 = "00000000-0000-4000-8000-000000000002"
HOST = "vpn.example.test"


def _link(uuid=UUID1, name="Node"):
    query = {"type": "ws", "security": "tls", "encryption": "none",
             "path": "/ws", "host": HOST, "sni": HOST, "fp": "chrome"}
    return f"vless://{uuid}@{HOST}:443?{urlencode(query)}#{quote(name)}"


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    conf = tmp_path / "conf"
    monkeypatch.setattr(paths, "CONF", str(conf))
    monkeypatch.setattr(paths, "TUNNELS_JSON", str(conf / "tunnels.json"))
    monkeypatch.setattr(paths, "CONF_TUNNELS", str(conf / "tunnels"))
    monkeypatch.setattr(paths, "SUBSCRIPTIONS_JSON", str(conf / "subscriptions.json"))


def _tunnel(tid="home", name="Личный", **kw):
    tunnel = {"id": tid, "name": name, "mode": "list", "proto": "wg",
              "active": "", "enabled": True}
    tunnel.update(kw)
    return tunnel


def _save(*items):
    tunnels.save({"tunnels": list(items)})


def _profiles(*links):
    records, _ = subscriptions.add_profiles("\n".join(links))
    return records


def _url(record):
    return proxy.link(record)


def _conf(tid, name, text=WG):
    path = tunnels.conf_path(tid, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def test_один_конфиг_wg_отдаёт_исходный_текст_и_зовёт_его_по_пути():
    _save(_tunnel())
    _conf("home", "office")

    entries, lines, manifest = exports.snapshot("config", "home", "office")

    assert [e["path"] for e in entries] == ["configs/home/office.conf"]
    assert "# пометка человека" in entries[0]["text"]
    assert entries[0]["text"] == WG
    assert lines == []
    group = manifest["tunnels"][0]
    assert group["active"] == "configs/home/office.conf"
    assert group["configs"] == ["configs/home/office.conf"]
    assert manifest["unattached"] == []


def test_конфиг_пропал_или_не_читается_ошибка_до_записи():
    _save(_tunnel())
    path = _conf("home", "office")
    os.remove(path)
    with pytest.raises(ValueError, match="пропал"):
        exports.conf_text("home", "office")

    os.makedirs(path)
    assert "office" in tunnels.list_confs("home")
    with pytest.raises(ValueError, match="пропал"):
        exports.snapshot("config", "home", "office")


def test_конфига_нет_в_этой_группе_ошибка():
    _save(_tunnel("home"), _tunnel("work", "Работа"))
    _conf("work", "office")

    with pytest.raises(ValueError, match="нет конфига"):
        exports.snapshot("config", "home", "office")


def test_одному_конфигу_нужны_и_группа_и_имя():
    _save(_tunnel())
    _conf("home", "office")

    with pytest.raises(ValueError, match="нужны туннель и имя"):
        exports.snapshot("config", "home", "")


def test_чужое_имя_конфига_не_становится_путём_вне_папки():
    _save(_tunnel())

    for bad in ("../../windows/win.ini", "office.conf", "con"):
        with pytest.raises(ValueError):
            exports.snapshot("config", "home", bad)


def test_профиль_группы_даёт_одну_ссылку_и_тот_же_адрес_в_манифесте():
    profile = _profiles(_link())[0]
    _save(_tunnel("proxy", "Прокси", proto="vless", active=profile["id"]))

    entries, lines, manifest = exports.snapshot("config", "proxy", profile["id"])

    assert entries == []
    assert lines == [_url(profile)]
    group = manifest["tunnels"][0]
    assert group["active"] == "L1"
    assert group["profiles"] == [{"name": "Node", "link": "L1"}]
    assert group["configs"] == []


def test_у_wg_ссылки_нет_и_это_объясняется():
    _save(_tunnel())
    _conf("home", "office")

    with pytest.raises(ValueError, match=r"\.conf"):
        exports.links("config", "home", "office")
    with pytest.raises(ValueError, match="архив"):
        exports.links("tunnel", "home")


def test_пустая_область_не_успешный_пустой_экспорт():
    _save(_tunnel())

    with pytest.raises(ValueError, match="нечего экспортировать"):
        exports.snapshot("tunnel", "home")


def test_неизвестной_области_нет():
    with pytest.raises(ValueError, match="неизвестная область"):
        exports.snapshot("subscription", "s_0000000000000000")


def test_нет_такой_группы():
    _save(_tunnel())

    with pytest.raises(ValueError, match="нет туннеля"):
        exports.snapshot("tunnel", "work")


def test_группа_берёт_все_файлы_а_не_только_активный():
    _save(_tunnel(active="office"))
    _conf("home", "office")
    _conf("home", "spare")

    entries, _lines, manifest = exports.snapshot("tunnel", "home")

    assert [e["path"] for e in entries] == ["configs/home/office.conf",
                                            "configs/home/spare.conf"]
    assert manifest["tunnels"][0]["active"] == "configs/home/office.conf"


def test_всё_складывает_группы_и_неприкреплённые_профили():
    free = _profiles(_link(UUID2, "Один"), _link(UUID1, "Два"))
    _save(_tunnel(active="office"))
    _conf("home", "office")

    entries, lines, manifest = exports.snapshot("all")

    assert [e["path"] for e in entries] == ["configs/home/office.conf"]
    assert sorted(lines) == sorted(_url(p) for p in free)
    assert manifest["tunnels"][0]["proto"] == "wg"
    assert [u["link"] for u in manifest["unattached"]] == ["L1", "L2"]
    assert [u["name"] for u in manifest["unattached"]] == [p["name"] for p in free]


def test_общий_профиль_лежит_одной_строкой_а_связи_в_манифесте():
    profile = _profiles(_link())[0]
    _save(_tunnel("proxy", "Прокси", proto="vless", active=profile["id"]),
          _tunnel("backup", "Запас", proto="vless", active=profile["id"]))

    entries, lines, manifest = exports.snapshot("all")

    assert entries == []
    assert lines == [_url(profile)]
    assert [g["profiles"] for g in manifest["tunnels"]] == [
        [{"name": "Node", "link": "L1"}]] * 2
    assert manifest["unattached"] == []


def test_выключенная_группа_экспортируется_наравне_с_включённой():
    _save(_tunnel(enabled=False))
    _conf("home", "office")

    entries, _lines, manifest = exports.snapshot("all")

    assert len(entries) == 1
    assert manifest["tunnels"][0]["id"] == "home"


def test_потолок_элементов_просит_меньшую_область_а_не_режет_экспорт(monkeypatch):
    monkeypatch.setattr(ipc, "MAX_EXPORT_ITEMS", 1)
    _save(_tunnel())
    _conf("home", "office")
    _conf("home", "spare")

    with pytest.raises(ValueError, match="сохраните по одной группе"):
        exports.snapshot("all")


def test_снимок_не_трогает_выбор_и_не_ходит_в_сеть(monkeypatch):
    _save(_tunnel(active="office"))
    _conf("home", "office")

    def _no_network(*_a, **_k):
        raise AssertionError("экспорт не ходит в сеть")

    monkeypatch.setattr(ipc, "call", _no_network)

    exports.snapshot("all")

    assert tunnels.load()["tunnels"][0]["active"] == "office"


def _core():
    return Core.__new__(Core)


def test_export_configs_отвечает_записями_без_пути_назначения():
    _save(_tunnel())
    _conf("home", "office")

    reply = _core()._export_configs("config", "home", "office")

    assert reply["ok"] is True
    assert reply["entries"][0]["path"] == "configs/home/office.conf"
    assert reply["links"] == []
    assert reply["manifest"]["version"] == exports.VERSION


def test_export_links_ведёт_конфиг_и_группу_а_профиль_по_старому():
    profile = _profiles(_link())[0]
    _save(_tunnel("proxy", "Прокси", proto="vless", active=profile["id"]))
    core = _core()

    assert core._export_links("config", "", "proxy", profile["id"]) == {
        "ok": True, "text": _url(profile), "profiles": 1}
    assert core._export_links("tunnel", "", "proxy", "")["profiles"] == 1
    by_id = core._export_links("profile", profile["id"])
    assert by_id["ok"] is True and by_id["text"] == _url(profile)
    refused = core._export_links("config", "", "proxy", "office")
    assert refused["ok"] is False and "id профиля" in refused["error"]


def test_пути_архива_только_разрешённой_раскладки():
    ok = ("manifest.json", "links.txt", "configs/home/office.conf",
          "configs/t-2/Сеть_2.conf")
    bad = ("", "manifest.JSON", "../configs/home/office.conf",
           "configs/office.conf", "configs/home/office.txt",
           "configs/home/nul.conf", "configs/../home/office.conf",
           "configs/home/a/b.conf", "state/tunnels.json",
           "configs/CON/office.conf", "configs/home/a\\b.conf")

    assert [p for p in ok if not exports.check_path(p)] == []
    assert [p for p in bad if exports.check_path(p)] == []


def _api(monkeypatch, reply):
    monkeypatch.setattr(ipc, "call", lambda op, **_kw: {"ok": True, "status": {}})
    monkeypatch.setattr(window.threading, "Thread", SyncThread)
    monkeypatch.setattr(window.threading, "Timer", NoTimer)
    api = window.Api({})
    admin, saved, shown = [], [], []

    def admin_call(op, **kw):
        admin.append((op, kw))
        return reply

    def save_export(filename, file_types, data):
        saved.append((filename, file_types, data))
        return filename

    monkeypatch.setattr(api, "_admin_call", admin_call)
    monkeypatch.setattr(api, "_save_export", save_export)
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))
    return api, admin, saved, shown


def test_один_конфиг_пишется_conf_а_группа_архивом(monkeypatch):
    _save(_tunnel())
    _conf("home", "office")
    entries, lines, manifest = exports.snapshot("tunnel", "home")

    api, admin, saved, _shown = _api(monkeypatch, {"ok": True, "entries": entries,
                                                   "links": lines, "manifest": manifest})
    api.send("export_configs", {"scope": "tunnel", "tunnel": "home",
                                "name": "Личный"})
    api.send("export_configs", {"scope": "config", "tunnel": "home",
                                "conf": "office", "name": "office"})

    assert admin[0] == ("export-configs", {"scope": "tunnel", "tunnel": "home",
                                           "name": ""})
    assert admin[1] == ("export-configs", {"scope": "config", "tunnel": "home",
                                           "name": "office"})
    zip_name, zip_types, zip_data = saved[0]
    assert zip_name == "Личный.zip" and ".zip" in "".join(zip_types)
    conf_name, conf_types, conf_data = saved[1]
    assert conf_name == "office.conf" and ".conf" in "".join(conf_types)
    assert conf_data.decode("utf-8") == WG

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == ["manifest.json", "configs/home/office.conf"]
        assert json.loads(zf.read("manifest.json"))["tunnels"][0]["configs"] == [
            "configs/home/office.conf"]
        assert zf.read("configs/home/office.conf").decode("utf-8") == WG


def test_архив_со_ссылками_кладёт_links_txt_и_не_повторяет_их_в_манифесте(monkeypatch):
    profile = _profiles(_link())[0]
    _save(_tunnel("proxy", "Прокси", proto="vless", active=profile["id"]))
    entries, lines, manifest = exports.snapshot("all")

    api, _admin, saved, _shown = _api(monkeypatch, {"ok": True, "entries": entries,
                                                    "links": lines,
                                                    "manifest": manifest})
    api.send("export_configs", {"scope": "all", "name": "TunnelVPN"})

    data = saved[0][2]
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["manifest.json", "links.txt"]
        assert zf.read("links.txt").decode("utf-8") == _url(profile) + "\n"
        manifest_text = zf.read("manifest.json").decode("utf-8")
    assert "vless://" not in manifest_text
    assert json.loads(manifest_text)["tunnels"][0]["profiles"][0]["link"] == "L1"


def test_отказ_службы_не_открывает_диалог_и_не_пишет(monkeypatch):
    api, _admin, saved, shown = _api(monkeypatch, {"ok": False,
                                                   "error": "нет туннеля work"})
    api.send("export_configs", {"scope": "tunnel", "tunnel": "work"})

    assert saved == []
    assert shown and shown[0][0] == "failed" and "нет туннеля" in shown[0][1]


def test_архив_не_пишет_то_чего_нет_в_раскладке(monkeypatch):
    api, _admin, _saved, _shown = _api(monkeypatch, {"ok": True})

    with pytest.raises(ValueError, match="недопустимый путь"):
        api._export_zip([exports.entry("../windows/x.conf", "x")], [], {})
    with pytest.raises(ValueError, match="недопустимый путь"):
        api._export_zip([exports.entry("configs/home/office.txt", "x")], [], {})


def test_ссылки_пишутся_в_txt(monkeypatch):
    profile = _profiles(_link())[0]
    api, admin, saved, _shown = _api(monkeypatch, {"ok": True, "text": _link(),
                                                   "profiles": 1})
    api.send("export_links", {"scope": "profile", "id": profile["id"],
                              "name": "Node"})

    assert admin == [("export-links", {"scope": "profile", "id": profile["id"],
                                       "tunnel": "", "name": ""})]
    assert saved[0][0] == "Node.txt"
    assert saved[0][2].decode("utf-8") == _link() + "\n"


def test_экспорт_конфигов_требует_прав_администратора():
    assert ipc.requires_admin("export-configs") is True
    assert ipc.requires_admin("export-links") is True
    assert "export-configs" not in ipc.READ_OPS


def test_экспортированная_ссылка_разбирается_обратно_в_тот_же_профиль():
    profile = _profiles(_link())[0]
    _save(_tunnel("proxy", "Прокси", proto="vless", active=profile["id"]))

    again = proxy.parse_link(exports.links("tunnel", "proxy")[0])

    assert again["proto"] == profile["proto"]
    assert again["name"] == profile["name"]
    assert proxy.fingerprint(again) == profile["fingerprint"]


def test_снимок_и_правка_группы_не_берут_замки_в_разном_порядке():
    _save(_tunnel())
    _conf("home", "office")
    errors = []

    def edit():
        try:
            for _ in range(30):
                _conf("home", "spare")
                tunnels.save({"tunnels": [_tunnel(active="spare")]})
                exports.snapshot("all")
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=edit)
    thread.start()
    for _ in range(30):
        exports.snapshot("all")
    thread.join(30)

    assert not thread.is_alive(), "снимок и правка держат замки вразнобой"
    assert errors == []


def _writer(monkeypatch):
    monkeypatch.setattr(ipc, "call", lambda op, **_kw: {"ok": True, "status": {}})
    monkeypatch.setattr(window.threading, "Thread", SyncThread)
    monkeypatch.setattr(window.threading, "Timer", NoTimer)
    api = window.Api({})
    shown = []
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))
    return api, shown


def _dialog(path=None):
    class Win:
        def create_file_dialog(self, _kind, **_kw):
            return (str(path),) if path else None
    return Win()


def test_отменённый_диалог_не_пишет_и_не_обещает_успех(monkeypatch, tmp_path):
    api, shown = _writer(monkeypatch)
    api.holder["window"] = _dialog()

    assert api._save_export("links.txt", window._LINK_TYPES, b"x") is None
    assert shown == []
    assert list(tmp_path.iterdir()) == []


def test_записанный_файл_называется_и_лежит_где_выбрал_человек(monkeypatch, tmp_path):
    out = tmp_path / "Все ссылки.txt"
    foreign = tmp_path / "Все ссылки.txt.tmp"
    foreign.write_bytes(b"foreign file")
    api, shown = _writer(monkeypatch)
    api.holder["window"] = _dialog(out)

    assert api._save_export("links.txt", window._LINK_TYPES, "vless://x\n".encode()) == str(out)

    assert out.read_text(encoding="utf-8") == "vless://x\n"
    assert foreign.read_bytes() == b"foreign file"
    assert shown == [("exported", "Все ссылки.txt")]


def test_отказ_замены_не_оставляет_временного_файла_и_зовёт_failed(monkeypatch,
                                                                  tmp_path):
    out = tmp_path / "office.conf"
    foreign = tmp_path / "office.conf.tmp"
    foreign.write_bytes(b"foreign file")

    def refuse(*_a, **_k):
        raise PermissionError("файл открыт в другом окне")

    api, shown = _writer(monkeypatch)
    api.holder["window"] = _dialog(out)
    monkeypatch.setattr(window.os, "replace", refuse)

    assert api._save_export("office.conf", window._CONF_TYPES, WG.encode()) is None

    assert not out.exists() and foreign.read_bytes() == b"foreign file"
    assert list(tmp_path.iterdir()) == [foreign]
    assert shown and shown[0][0] == "failed" and "другом окне" in shown[0][1]
