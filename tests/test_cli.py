"""Командная строка: что уходит в канал и что видит человек.

Служба здесь — подменённый ipc.call: CLI сам систему не трогает, его дело —
собрать команду канала и показать ответ.
"""

import pytest

from tunnelvpn import cli

STATUS = {"tunnels": [
    {"id": "work", "name": "Работа", "mode": "list", "active": "corp",
     "confs": ["corp"]},
    {"id": "home", "name": "Личный", "mode": "all", "active": "nl-2",
     "confs": ["nl-1", "nl-2"]},
    {"id": "t3", "name": "Пустой", "mode": "list", "active": "", "confs": []},
]}


@pytest.fixture
def sent(monkeypatch):
    """Команды, ушедшие в канал; ответ — из replies по имени команды."""
    class Calls(list):
        pass

    calls = Calls()
    calls.replies = {"status": {"ok": True, "status": STATUS}}

    def call(op, **payload):
        calls.append((op, payload))
        return calls.replies.get(op, {"ok": True})

    monkeypatch.setattr(cli.ipc, "call", call)
    return calls


def test_use_выбирает_конфиг_туннеля(sent, capsys):
    sent.replies["set-active"] = {"ok": True, "tunnel": "home", "active": "nl-1"}

    assert cli.main(["use", "Личный", "nl-1"]) == 0

    assert sent[0] == ("set-active", {"tunnel": "Личный", "name": "nl-1"})
    assert "home: nl-1" in capsys.readouterr().out


def test_use_отказ_службы_код_ошибки_и_не_применяет(sent, capsys):
    sent.replies["set-active"] = {"ok": False, "error": "нет туннеля 'x'"}

    assert cli.main(["use", "x", "nl-1"]) == 1

    assert "нет туннеля 'x'" in capsys.readouterr().out
    assert [op for op, _ in sent] == ["set-active"]


def test_use_без_конфига_не_зовёт_службу(sent):
    assert cli.main(["use", "home"]) == 1
    assert sent == []


@pytest.mark.parametrize("cmd, on, shown", [
    ("disable", False, "work: выключен"),
    ("enable", True, "work: включён"),
])
def test_enable_disable_флаг_туннеля_и_сразу_применяет(sent, capsys, cmd, on, shown):
    sent.replies["set-enabled"] = {"ok": True, "tunnel": "work", "enabled": on}

    assert cli.main([cmd, "Работа"]) == 0

    assert sent == [("set-enabled", {"tunnel": "Работа", "on": on}), ("apply", {})]
    assert shown in capsys.readouterr().out


def test_disable_основного_тот_же_set_enabled_и_применяет(sent, capsys):
    sent.replies["set-enabled"] = {"ok": True, "tunnel": "home", "enabled": False}

    assert cli.main(["disable", "Личный"]) == 0

    assert sent == [("set-enabled", {"tunnel": "Личный", "on": False}), ("apply", {})]
    assert "home: выключен" in capsys.readouterr().out


def test_отказ_set_enabled_код_ошибки_и_не_применяет(sent, capsys):
    sent.replies["set-enabled"] = {"ok": False, "error": "нет туннеля 'x'"}

    assert cli.main(["disable", "x"]) == 1

    assert "не вышло: нет туннеля 'x'" in capsys.readouterr().out
    assert [op for op, _ in sent] == ["set-enabled"]


def _status_text(**st):
    return "\n".join(cli.status_lines({"up": True, "exit_state": "direct", "out": "direct",
                                       **st}))


def test_status_выключенный_основной_напрямую_не_запасной():
    """Без работающего основного selector и так смотрит в direct."""
    home = {**STATUS["tunnels"][1], "enabled": False}

    text = _status_text(tunnels=[STATUS["tunnels"][0], home])

    assert "профиль  : основной выключен" in text
    assert "напрямую: основной туннель выключен" in text
    assert "запасной" not in text


def test_status_без_основного_напрямую_не_запасной():
    text = _status_text(tunnels=[STATUS["tunnels"][0]])

    assert "профиль  : основного нет" in text
    assert "напрямую: основного туннеля нет" in text
    assert "запасной" not in text


def test_status_работающий_основной_на_direct_запасной():
    text = _status_text(tunnels=STATUS["tunnels"], profile="nl-2")

    assert "профиль  : nl-2" in text
    assert "запасной, напрямую: личный не работает" in text


def test_status_kill_switch_закрыл_выход():
    text = _status_text(tunnels=STATUS["tunnels"], out="block", exit_state="blocked",
                        exit_ip="", kill_switch=True)

    assert "закрыт kill-switch" in text
    assert "kill-switch: да" in text
    assert "запасной" not in text


def test_disable_без_туннеля_не_зовёт_службу(sent):
    assert cli.main(["disable"]) == 1
    assert sent == []


def test_выключенный_туннель_помечен_в_status_и_list(sent, capsys):
    off = {**STATUS["tunnels"][0], "enabled": False, "check": "error"}
    sent.replies["status"] = {"ok": True, "status": {"up": True, "tunnels": [off]}}

    cli.main(["status"])
    cli.main(["list"])

    out = capsys.readouterr().out
    assert "Работа [work], по списку — corp; выключен" in out
    assert "молчит" not in out
    assert "Работа [work], по списку, выключен:" in out


def test_profile_остаётся_алиасом(sent):
    assert cli.main(["profile", "nl-1"]) == 0
    assert sent[0] == ("set-profile", {"profile": "nl-1"})


@pytest.mark.parametrize("argv, first", [
    (["use", "home", "nl-1"], "set-active"),
    (["profile", "nl-1"], "set-profile"),
])
def test_выбор_конфига_сразу_применяется(sent, argv, first):
    assert cli.main(argv) == 0
    assert [op for op, _ in sent] == [first, "apply"]


@pytest.mark.parametrize("reply, shown", [
    ({"ok": True, "applied": "off"}, "применится при включении"),
    ({"ok": True, "applied": "off", "empty": True}, "конфигов не осталось — VPN выключен"),
    ({"ok": True, "applied": "sides", "restarted": ["home"]}, "перезапущен процесс: home"),
    ({"ok": True, "applied": "sides", "restarted": []}, "перезапускать нечего"),
    ({"ok": True, "applied": "full"}, "VPN перезапущен"),
])
def test_use_показывает_как_применилось(sent, capsys, reply, shown):
    sent.replies["apply"] = reply

    assert cli.main(["use", "home", "nl-1"]) == 0

    assert shown in capsys.readouterr().out


def test_ошибка_применения_код_ошибки(sent, capsys):
    sent.replies["apply"] = {"ok": False, "applied": "sides", "restarted": ["home"],
                             "error": "процесс «Личный» не поднялся"}

    assert cli.main(["profile", "nl-1"]) == 1
    assert "не вышло: процесс «Личный» не поднялся" in capsys.readouterr().out


def test_list_по_туннелям_с_активным(sent, capsys):
    assert cli.main(["list"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out[out.index("Личный [home], весь остальной трафик:") + 1:][:2] == [
        "    nl-1", "  * nl-2"]
    assert "Пустой [t3], по списку:" in out
    assert "    (нет конфигов)" in out


def test_статус_показывает_туннели(sent, capsys):
    assert cli.main(["status"]) == 0
    assert "Работа [work], по списку — corp" in capsys.readouterr().out


@pytest.mark.parametrize("err, code", [(0, 0), (1072, 1), (-1, 1)])
def test_service_отдаёт_ошибку_наружу(monkeypatch, err, code):
    """По коду выхода установщик видит, что служба не встала (InstallService)."""
    from tunnelvpn import service
    monkeypatch.setattr(service, "handle_command_line", lambda: err)
    monkeypatch.setattr(cli.sys, "argv", ["tunnelvpn.exe"])

    assert cli.main(["service", "install"]) == code
