"""Окно: конфиги и правила по id туннеля — вопрос о месте конфига, удаление
последнего, списки правил из файла."""

import sys
import types

from tunnelvpn import ipc, window

WG = "[Interface]\nAddress = 10.53.0.4/32\n[Peer]\nAllowedIPs = 10.53.0.0/16\n"


def _api(monkeypatch, replies):
    """Окно с подменёнными каналом и UAC: replies — ответы админ-команд по очереди."""
    admin, shown = [], []
    monkeypatch.setattr(ipc, "call", lambda op, **_kw: {"ok": True, "status": {}})
    api = window.Api({})

    def admin_call(op, **kw):
        admin.append((op, kw))
        return replies.pop(0) if replies else {"ok": True}
    monkeypatch.setattr(api, "_admin_call", admin_call)
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))
    return api, admin, shown


def test_вопрос_о_месте_и_замена_без_повторного_диалога(monkeypatch, tmp_path):
    conf = tmp_path / "office-2.conf"
    conf.write_text(WG, encoding="utf-8")
    ask = {"id": "work", "name": "office", "conf": "office"}
    api, admin, shown = _api(monkeypatch, [{"ok": False, "ask": ask, "error": "…"}])
    monkeypatch.setattr(api, "_pick", lambda _types: str(conf))

    api.send("add_config", {})
    api.send("place_config", {"place": "replace", "tunnel": "work"})

    assert ("askPlace", {**ask, "file": "office-2"}) in shown
    assert "failed" not in [fn for fn, _ in shown]
    # Место выбирает служба; ответ человека несёт тот же файл.
    assert admin == [
        ("add-config", {"name": "office-2", "text": WG, "tunnel": "", "place": ""}),
        ("add-config", {"name": "office-2", "text": WG, "tunnel": "work",
                        "place": "replace"})]


def test_брошенный_conf_идёт_службе_как_добавление(monkeypatch, tmp_path):
    conf, txt = tmp_path / "office-2.conf", tmp_path / "notes.txt"
    conf.write_text(WG, encoding="utf-8")
    txt.write_text("x", encoding="utf-8")
    api, admin, shown = _api(monkeypatch, [])
    files = [{"name": "notes.txt", "pywebviewFullPath": str(txt)},
             {"name": "office-2.conf", "pywebviewFullPath": str(conf)}]

    api._drop({"type": "drop", "dataTransfer": {"files": files}})

    # Место выбирает служба — как у «Добавить конфиг…» без туннеля.
    assert admin == [("add-config", {"name": "office-2", "text": WG, "tunnel": "", "place": ""})]
    assert "failed" not in [fn for fn, _ in shown]


def test_брошенный_не_conf_объясняется_и_не_шлётся(monkeypatch, tmp_path):
    api, admin, shown = _api(monkeypatch, [])

    # Без pywebviewFullPath — WebView2 пути не отдал.
    api._drop({"type": "drop", "dataTransfer": {"files": [{"name": "a.conf"}]}})

    assert admin == []
    assert shown == [("failed", "Нужен файл .conf WireGuard или AmneziaWG")]


AWG = ("[Interface]\nPrivateKey = PRIV0secret=\nAddress = 10.8.0.2/32, fd00::2/128\n"
       "DNS = 10.1.1.5\nMTU = 1420\nJc = 4\nS1 = 15\nH1 = 1-5\nHeaderProtectionKey = HPK0secret=\n\n"
       "[Peer]\nPublicKey = PUB0key=\nPresharedKey = PSK0secret=\n"
       "AllowedIPs = 0.0.0.0/0\nEndpoint = vpn.example.net:51820\nPersistentKeepalive = 25\n")


def test_подробности_без_ключей_и_с_mtu_сборки(monkeypatch):
    api, admin, _ = _api(monkeypatch, [{"ok": True, "text": AWG}])

    got = api.send("conf_details", {"tunnel": "home", "name": "nl-1"})

    assert admin == [("read-config", {"tunnel": "home", "name": "nl-1"})]
    assert "secret" not in str(got) and "PUB0key" not in str(got)
    assert got["ok"] and got["awg"] and got["path"].endswith("nl-1.conf")
    assert got["address"] == ["10.8.0.2/32", "fd00::2/128"]
    assert (got["endpoint"], got["dns"], got["allowed"], got["keepalive"]) == (
        "vpn.example.net:51820", ["10.1.1.5"], ["0.0.0.0/0"], "25")
    # AWG с junk: служба соберёт с 1280, а не с 1420 из файла.
    assert (got["mtu"], got["mtu_file"], got["cut"]) == (1280, 1420, True)
    # Имена полей маскировки — как в файле.
    assert got["mask"] == [["Jc", "4"], ["S1", "15"], ["H1", "1-5"]]
    assert got["problem"] == ""


def test_подробности_wg_без_mtu_и_с_ошибкой_сборки(monkeypatch):
    api, _, _ = _api(monkeypatch, [{"ok": True, "text": WG}])

    got = api.send("conf_details", {"tunnel": "work", "name": "office"})

    assert not got["awg"] and got["mask"] == []
    assert (got["mtu"], got["mtu_file"], got["cut"]) == (1280, None, False)
    assert "PrivateKey" in got["problem"]


def test_подробности_отказ_прав_возвращается_ответом(monkeypatch):
    api, _, shown = _api(monkeypatch, [{"ok": False, "error": "отменено"}])

    got = api.send("conf_details", {"tunnel": "home", "name": "nl-1"})

    # Ошибку показывает страница по ответу — без второго тоста через failed.
    assert got == {"ok": False, "error": "отменено"}
    assert shown == []


def test_ответ_на_вопрос_без_вопроса_ничего_не_шлёт(monkeypatch):
    api, admin, _ = _api(monkeypatch, [])

    api.send("place_config", {"place": "new"})

    assert admin == []


def test_удаление_последнего_передаёт_выбор_о_туннеле(monkeypatch):
    api, admin, shown = _api(monkeypatch, [])

    api.send("del_config", {"tunnel": "work", "name": "office", "drop_tunnel": True})

    assert admin == [("remove-config", {"tunnel": "work", "name": "office",
                                        "drop_tunnel": True})]
    assert ("closeSheet", None) in shown


def test_выбор_конфига_без_прав_по_id_туннеля(monkeypatch):
    api, admin, _ = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": True, "status": {}})

    api.send("use_config", {"tunnel": "home", "name": "de-2"})

    # set-active в USER_OPS: клик по запасному не спрашивает UAC.
    assert ("set-active", {"tunnel": "home", "name": "de-2"}) in calls
    assert admin == []


def test_вкл_выкл_туннеля_без_прав_и_сразу_на_живой_vpn(monkeypatch):
    api, admin, shown = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": True, "status": {}})

    api.send("set_enabled", {"tunnel": "work", "on": False})

    # set-enabled в USER_OPS, как stop; флаг на живой VPN переносит apply — с ask:
    # полный перезапуск сам не делается.
    assert calls[:2] == [("set-enabled", {"tunnel": "work", "on": False, "replace": False}),
                         ("apply", {"ask": True})]
    assert admin == []
    assert "failed" not in [fn for fn, _ in shown]


def test_плитка_при_выключенном_vpn_поднимает_только_её(monkeypatch):
    api, admin, shown = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": True, "status": {}})

    api.send("start_only", {"tunnel": "work"})

    # Флаги и start — одна команда службы: между ними никто не вклинится.
    assert calls[0] == ("start-only", {"tunnel": "work"})
    assert admin == []
    assert "failed" not in [fn for fn, _ in shown]


def test_плитка_отказ_службы_виден_и_статус_обновляется(monkeypatch):
    api, _, shown = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append(op) or
                        ({"ok": False, "error": "занято"} if op == "start-only" else
                         {"ok": True, "status": {}}))

    api.send("start_only", {"tunnel": "work"})

    assert ("failed", "start_only: занято") in shown
    assert "status" in calls


def test_конфликт_при_включении_предлагает_замену_и_не_применяет(monkeypatch):
    api, _, shown = _api(monkeypatch, [])
    calls = []
    clash = [{"id": "t1", "name": "Офис"}]
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": False, "clash": clash, "error": "…"})

    api.send("set_enabled", {"tunnel": "t2", "on": True})

    assert [op for op, _ in calls] == ["set-enabled"]
    assert ("askSwap", {"tunnel": "t2", "clash": clash}) in shown
    assert "failed" not in [fn for fn, _ in shown]


def test_замена_уходит_службе_с_replace(monkeypatch):
    api, _, _ = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": True, "status": {}})

    api.send("set_enabled", {"tunnel": "t2", "on": True, "replace": True})

    assert calls[0] == ("set-enabled", {"tunnel": "t2", "on": True, "replace": True})


def test_опоздавшее_нажатие_не_ложится_поверх_нового(monkeypatch):
    api, _, shown = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": True, "status": {}})

    # Мост отдал второе нажатие раньше первого.
    api.send("set_enabled", {"tunnel": "work", "on": True, "seq": 2})
    api.send("set_enabled", {"tunnel": "work", "on": False, "seq": 1})
    # Счёт у каждого туннеля свой.
    api.send("set_enabled", {"tunnel": "home", "on": False, "seq": 1})

    sets = [(kw["tunnel"], kw["on"]) for op, kw in calls if op == "set-enabled"]
    assert sets == [("work", True), ("home", False)]
    assert "failed" not in [fn for fn, _ in shown]


def test_перезагруженная_страница_считает_нажатия_заново(monkeypatch):
    api, _, _ = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": True, "status": {}})
    monkeypatch.setattr(api, "refresh", lambda full=False: None)
    monkeypatch.setattr(api, "reveal", lambda: None)
    api.hidden = True

    api.send("set_enabled", {"tunnel": "work", "on": False, "seq": 5})
    api.send("ready")
    api.send("set_enabled", {"tunnel": "work", "on": True, "seq": 1})

    sets = [kw["on"] for op, kw in calls if op == "set-enabled"]
    assert sets == [False, True]


def test_полный_перезапуск_спрашивает_а_после_да_идёт_без_ask(monkeypatch):
    api, _, shown = _api(monkeypatch, [])
    calls = []

    def call(op, **kw):
        calls.append((op, kw))
        return {"ok": True, "applied": "ask"} if kw.get("ask") else {"ok": True, "status": {}}
    monkeypatch.setattr(ipc, "call", call)

    api.send("use_config", {"tunnel": "home", "name": "de-2"})
    assert ("askFull", None) in shown
    assert "failed" not in [fn for fn, _ in shown]

    api.send("apply_full")
    assert ("apply", {}) in calls


def test_отказ_вкл_выкл_виден_и_не_применяет(monkeypatch):
    api, _, shown = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append(op) or
                        {"ok": False, "error": "«Личный» — основной"})

    api.send("set_enabled", {"tunnel": "home", "on": False})

    assert calls == ["set-enabled"]
    assert ("failed", "set_enabled: «Личный» — основной") in shown


def test_команды_плитки_уходят_администратору_с_id_туннеля(monkeypatch):
    api, admin, shown = _api(monkeypatch, [])

    api.send("move_config", {"tunnel": "home", "name": "nl-1", "to": "new"})
    api.send("set_mode", {"tunnel": "t3", "mode": "all"})
    api.send("del_tunnel", "work")

    assert admin == [
        ("move-config", {"tunnel": "home", "name": "nl-1", "to": "new",
                         "drop_tunnel": False}),
        ("set-tunnel", {"tunnel": "t3", "mode": "all"}),
        ("remove-tunnel", {"tunnel": "work"})]
    assert "failed" not in [fn for fn, _ in shown]


def test_проверка_конфига_без_прав_статус_раньше_отпускания(monkeypatch):
    api, admin, shown = _api(monkeypatch, [])
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        {"ok": True, "status": {}})
    arg = {"tunnel": "home", "name": "de-2"}

    api._test_config(arg)

    # test-config в USER_OPS: ↻ не спрашивает UAC.
    assert calls[0] == ("test-config", arg)
    assert admin == []
    # Иначе testDone перерисовал бы плитку прошлым статусом.
    assert [fn for fn, _ in shown] == ["render", "testDone"]
    assert shown[-1] == ("testDone", arg)


def test_отказ_проверки_виден_и_кнопка_отпускается(monkeypatch):
    api, _, shown = _api(monkeypatch, [])
    monkeypatch.setattr(ipc, "call", lambda op, **_kw: {"ok": True, "status": {}}
                        if op == "status" else {"ok": False, "error": "нет конфига x.conf"})

    api._test_config({"tunnel": "home", "name": "x"})

    assert ("failed", "нет конфига x.conf") in shown
    assert shown[-1] == ("testDone", {"tunnel": "home", "name": "x"})


def test_галочка_журнала_отпускается_и_при_отказе(monkeypatch):
    api, admin, shown = _api(monkeypatch, [{"ok": False, "error": "отменено"}])

    api.send("log_level", True)

    assert admin == [("set-log-level", {"level": "debug"})]
    fns = [fn for fn, _ in shown]
    assert "logLevelDone" in fns and "failed" in fns


def test_лист_правил_получает_только_поля_туннеля(monkeypatch):
    work = {"id": "work", "name": "Работа", "mode": "list", "active": "corp",
            "include": ["corp.example"], "exclude": ["198.51.100.7/32"],
            "exclude_apps": [], "lab": 1}
    api, admin, shown = _api(monkeypatch, [{"ok": True, "tunnels": [work]},
                                           {"ok": True, "tunnels": [work]}])

    api.send("tunnel_rules", "work")
    api.send("tunnel_rules", "gone")

    assert admin == [("get-tunnels", {}), ("get-tunnels", {})]
    assert shown[0] == ("showRules", {k: v for k, v in work.items() if k != "lab"})
    assert shown[1][0] == "failed"


def test_экспорт_правил_читается_обратно_загрузкой(monkeypatch, tmp_path):
    out = tmp_path / "rules.json"
    asked = {}

    class Win:
        def create_file_dialog(self, _kind, **kw):
            asked.update(kw)
            return (str(out),)
    api, _, shown = _api(monkeypatch, [])
    api.holder["window"] = Win()
    # pywebview в CI нет: диалог подменён, от модуля нужен только FileDialog.
    monkeypatch.setitem(sys.modules, "webview",
                        types.SimpleNamespace(FileDialog=types.SimpleNamespace(SAVE=30)))

    api.send("export_rules", {"name": "Работа", "include": ["corp.example"],
                              "exclude": ["198.51.100.7/32"]})

    assert asked["save_filename"] == "Работа.json"
    assert "failed" not in [fn for fn, _ in shown]
    assert window._rules_from_file(out.read_text(encoding="utf-8")) == {
        "include": "corp.example", "exclude": "198.51.100.7/32"}


def test_файл_экспорта_основного_несёт_программы_строками():
    text = '{"include": [], "exclude": ["a.ru"], "exclude_apps": ["C:\\\\A b\\\\a.exe", "D:\\\\b.exe"]}'

    assert window._rules_from_file(text) == {"include": "", "exclude": "a.ru",
                                             "exclude_apps": "C:\\A b\\a.exe\nD:\\b.exe"}


def test_файл_без_программ_не_трогает_их_поле():
    """Экспорт туннеля «по списку» программ не пишет: поле основного не очищается."""
    assert "exclude_apps" not in window._rules_from_file('{"exclude": ["a.ru"]}')


def test_выбранная_программа_уходит_в_лист_путём(monkeypatch):
    api, admin, shown = _api(monkeypatch, [])
    picked = iter(["C:\\Games\\..\\Steam\\steam.exe", None])
    monkeypatch.setattr(api, "_pick", lambda _types: next(picked))

    api.send("pick_app")
    api.send("pick_app")

    # Выбор — правка поля, не запись: служба не зовётся; передумали — тишина.
    assert shown == [("appPicked", "C:\\Steam\\steam.exe")]
    assert admin == []


def test_файл_экспорта_раскладывается_по_полям():
    text = '﻿{"include": ["corp.example", "198.51.100.7/32"], "exclude": []}'

    assert window._rules_from_file(text) == {"include": "corp.example\n198.51.100.7/32",
                                             "exclude": ""}


def test_простой_текст_идёт_в_первое_поле_целиком():
    assert window._rules_from_file("a.ru, 1.2.3.4") == {"text": "a.ru, 1.2.3.4"}
    # JSON, но не наш: ни include, ни exclude — тоже просто текст.
    assert window._rules_from_file('["a.ru"]') == {"text": '["a.ru"]'}
    assert window._rules_from_file('{"exclude": 5}') == {"include": "", "exclude": ""}
