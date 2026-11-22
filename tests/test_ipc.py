"""Именованный канал: ответ доходит до трея целиком.

Сам канал здесь не поднять, поэтому pywin32 подменяем в sys.modules —
модуль импортирует его внутри функций, и подмена срабатывает при вызове.
Проверяем не WinAPI, а то, как мы им пользуемся: порядок вызовов и склейку.
"""

import json
import sys
import threading
import types

import pytest

from tunnelvpn import ipc


class _WinError(Exception):
    def __init__(self, winerror, strerror=""):
        super().__init__(winerror, strerror)
        self.winerror = winerror
        self.strerror = strerror


@pytest.fixture
def win(monkeypatch):
    """Поддельные pywintypes/win32file/win32pipe; calls — журнал вызовов."""
    calls = []
    reads = []

    win32file = types.SimpleNamespace(
        GENERIC_READ=1, GENERIC_WRITE=2, OPEN_EXISTING=3,
        CreateFile=lambda *a: calls.append("CreateFile") or "h",
        WriteFile=lambda h, data: calls.append("WriteFile"),
        ReadFile=lambda h, n: _next_read(reads, n),
        CloseHandle=lambda h: calls.append("CloseHandle"),
        FlushFileBuffers=lambda h: calls.append("FlushFileBuffers"),
    )
    win32pipe = types.SimpleNamespace(
        WaitNamedPipe=lambda *a: None,
        PeekNamedPipe=lambda h, n: calls.append("PeekNamedPipe") or _peek(reads),
        DisconnectNamedPipe=lambda h: calls.append("DisconnectNamedPipe"),
    )
    pywintypes = types.SimpleNamespace(error=_WinError)
    for name, mod in (("win32file", win32file), ("win32pipe", win32pipe),
                      ("pywintypes", pywintypes)):
        monkeypatch.setitem(sys.modules, name, mod)
    return types.SimpleNamespace(calls=calls, reads=reads)


def _peek(reads):
    """Как PeekNamedPipe: сколько байт ждёт чтения; обрыв — ошибка сразу.
    Пустой reads — служба приняла соединение и молчит."""
    if not reads:
        return b"", 0, 0
    if isinstance(reads[0], Exception):
        raise reads.pop(0)
    return b"", len(reads[0]), 0


def _next_read(reads, n):
    item = reads.pop(0)
    if isinstance(item, Exception):
        raise item
    assert len(item) <= n
    return 0, item


def test_ответ_склеивается_из_двух_чтений(win):
    raw = json.dumps({"ok": True, "status": {"up": True}}).encode() + b"\n"
    win.reads.extend([raw[:7], raw[7:]])
    assert ipc.call("status") == {"ok": True, "status": {"up": True}}


def test_ответ_больше_буфера_читается_целиком(win):
    """Лог на 400 строк легко больше 64 КБ — раньше он обрезался."""
    lines = ["x" * 300] * 400
    raw = json.dumps({"ok": True, "lines": lines}).encode() + b"\n"
    assert len(raw) > ipc._BUF
    win.reads.extend(raw[i:i + ipc._BUF] for i in range(0, len(raw), ipc._BUF))
    assert ipc.call("log", lines=400)["lines"] == lines


def test_запрос_больше_буфера_читается_целиком(win):
    raw = json.dumps({"op": "add-link", "payload": {"link": "x" * 70000}}).encode() + b"\n"
    win.reads.extend(raw[i:i + ipc._BUF] for i in range(0, len(raw), ipc._BUF))

    assert ipc._read_request("h") == raw


def test_слишком_большой_запрос_отклоняется(win):
    win.reads.extend([b"x" * ipc._BUF] * (ipc._MAX_REQUEST // ipc._BUF + 1))

    with pytest.raises(ValueError, match="запрос слишком большой"):
        ipc._read_request("h")


def test_закрытый_канал_после_ответа_не_ошибка(win):
    win.reads.extend([b'{"ok": true}', _WinError(109)])
    assert ipc.call("status") == {"ok": True}


def test_закрытый_канал_без_ответа_значит_служба_не_отвечает(win):
    win.reads.append(_WinError(109, "broken pipe"))
    with pytest.raises(ipc.NotRunning):
        ipc.call("status")


def test_молчащая_служба_на_status_не_отвечает_после_таймаута(win, monkeypatch):
    """Служба приняла соединение и зависла: раньше трей и окно ждали вечно,
    и ни самолечение, ни кнопка «Перезапустить службу» не срабатывали."""
    monkeypatch.setattr(ipc, "STATUS_TIMEOUT", 0.1)
    monkeypatch.setattr(ipc, "_PEEK_EVERY", 0.01)

    with pytest.raises(ipc.NotRunning, match="нет ответа"):
        ipc.call("status")

    assert win.calls[-1] == "CloseHandle"


def test_долгие_команды_ждут_ответа_не_обрываясь(win):
    """start честно работает до тридцати секунд — его не обрываем."""
    win.reads.append(b'{"ok": true}\n')

    assert ipc.call("start") == {"ok": True}


@pytest.mark.parametrize("wait_error", [None, 121])
@pytest.mark.parametrize("available_after", [0.6, None])
def test_занятый_канал_ждёт_подключения_но_не_бесконечно(
        win, monkeypatch, wait_error, available_after):
    clock = [0.0]
    win.reads.append(b'{"ok": true}\n')
    monkeypatch.setattr(ipc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ipc.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))

    def create(*_args):
        if available_after is None or clock[0] < available_after:
            raise _WinError(231)
        return "h"

    def wait(*_args):
        if wait_error:
            clock[0] += 0.2
            raise _WinError(wait_error)

    monkeypatch.setattr(sys.modules["win32file"], "CreateFile", create)
    monkeypatch.setattr(sys.modules["win32pipe"], "WaitNamedPipe", wait)

    if available_after is None:
        with pytest.raises(ipc.NotRunning, match="канал занят"):
            ipc.call("check")
        assert clock[0] == pytest.approx(4.0)
        assert win.calls == []
    else:
        assert ipc.call("check") == {"ok": True}
        assert available_after <= clock[0] <= 4.0
        assert win.calls[-1] == "CloseHandle"


@pytest.mark.parametrize("failed_operation", ["CreateFile", "WaitNamedPipe"])
def test_ошибка_доступа_к_каналу_не_повторяется(win, monkeypatch, failed_operation):
    attempts = []

    def create(*_args):
        attempts.append("create")
        raise _WinError(5 if failed_operation == "CreateFile" else 231)

    def wait(*_args):
        raise _WinError(5)

    monkeypatch.setattr(sys.modules["win32file"], "CreateFile", create)
    monkeypatch.setattr(sys.modules["win32pipe"], "WaitNamedPipe", wait)

    with pytest.raises(ipc.NotRunning) as error:
        ipc.call("check")

    assert error.value.__cause__.winerror == 5
    assert attempts == ["create"]
    assert win.calls == []


def test_молчащая_служба_на_start_обрывается_по_op_таймауту(win, monkeypatch):
    monkeypatch.setattr(ipc, "OP_TIMEOUT", 0.1)
    monkeypatch.setattr(ipc, "_PEEK_EVERY", 0.01)

    with pytest.raises(ipc.NotRunning, match="нет ответа"):
        ipc.call("start")

    assert win.calls[-1] == "CloseHandle"


def test_сервер_сбрасывает_буфер_до_отключения(win, monkeypatch):
    """Иначе DisconnectNamedPipe выбросил бы ещё не прочитанный ответ."""
    server = ipc.Server(handler=None, log=lambda _m: None)
    monkeypatch.setattr(server, "_exchange", lambda pipe: None)
    server._serve_one("pipe")
    assert win.calls == ["FlushFileBuffers", "DisconnectNamedPipe",
                         "CloseHandle"]


def test_сбой_не_от_windows_не_роняет_цикл_канала(win, monkeypatch):
    """Раньше всё, кроме pywintypes.error, молча убивало поток канала: служба
    жила, а окно до перезагрузки писало «Служба не отвечает»."""
    logged, served = [], threading.Event()
    server = ipc.Server(handler=None, log=logged.append)
    monkeypatch.setattr(server, "_security", lambda: None)
    monkeypatch.setattr(server.stop_event, "wait", lambda _t: None)
    monkeypatch.setattr(server, "_serve_one", lambda pipe: served.set())
    pipes = [RuntimeError("сбой"), "p2"]

    def create(*_a):
        if not pipes:
            server.stop_event.set()
            raise _WinError(232)
        item = pipes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    for name, value in (("PIPE_ACCESS_DUPLEX", 3), ("PIPE_TYPE_BYTE", 0),
                        ("PIPE_READMODE_BYTE", 0), ("PIPE_WAIT", 0),
                        ("PIPE_UNLIMITED_INSTANCES", 255), ("CreateNamedPipe", create),
                        ("ConnectNamedPipe", lambda pipe, _ov: None)):
        monkeypatch.setattr(sys.modules["win32pipe"], name, value, raising=False)

    server.serve_forever()

    assert served.wait(5)
    assert logged == ["канал: RuntimeError('сбой')"]


def _status(check_seq, checking, home, work):
    return {"check_seq": check_seq, "checking": checking, "route_gen": 1,
            "tunnels": [{"id": "work", "seq": work}, {"id": "home", "seq": home}]}


def _seq(st, tid):
    return next(t["seq"] for t in st["tunnels"] if t["id"] == tid)


def _fake_service(monkeypatch, st, drop_work=False):
    """Служба без канала: check ставит основной готовым и ждёт release,
    а рабочий отмечает только после него (drop_work — рабочий удалили)."""
    release = threading.Event()

    def call(op, **_payload):
        if op == "check":
            seq = st["check_seq"] if st["checking"] else st["check_seq"] + 1
            st.update(_status(seq, True, home=seq, work=_seq(st, "work")))
            if drop_work:
                st["tunnels"] = st["tunnels"][1:]
            release.wait(2)
            st.update(checking=False)
            for t in st["tunnels"]:
                t["seq"] = seq
        return {"ok": True, "status": {**st, "tunnels": [dict(t) for t in st["tunnels"]]}}

    monkeypatch.setattr(ipc, "call", call)
    return release


@pytest.mark.parametrize("st", [
    _status(3, False, home=3, work=3),
    # Проверка уже шла — check её дождётся, её ответ тоже свежий.
    _status(3, True, home=2, work=2),
])
def test_check_by_side_отдаёт_основной_не_дожидаясь_рабочего(monkeypatch, st):
    need = st["check_seq"] + (0 if st["checking"] else 1)
    release = _fake_service(monkeypatch, st)
    seen = []

    def on_side(got, pending):
        seen.append((_seq(got, "home"), pending))
        release.set()

    reply = ipc.check_by_side(on_side, poll=0.01)

    assert seen[0] == (need, frozenset({"work"}))
    assert _seq(reply["status"], "work") == need
    assert reply["fresh"] is True


def test_check_by_side_прошлый_итог_не_считает_свежим(monkeypatch):
    st = _status(3, False, home=3, work=3)
    release = _fake_service(monkeypatch, st)
    seen = []
    threading.Timer(0.2, release.set).start()

    ipc.check_by_side(lambda got, pending: seen.append(_seq(got, "home")),
                      poll=0.01)

    assert all(seq == 4 for seq in seen)


def test_check_by_side_статус_и_on_side_под_замком(monkeypatch):
    """Опрос окна не вклинится между статусом и on_side со своим статусом."""
    st = _status(3, False, home=3, work=3)
    release = _fake_service(monkeypatch, st)
    lock = threading.Lock()
    fake = ipc.call
    held = []

    def call(op, **payload):
        if op == "status":
            held.append(lock.locked())
        return fake(op, **payload)
    monkeypatch.setattr(ipc, "call", call)

    def on_side(_got, _pending):
        held.append(lock.locked())
        release.set()

    ipc.check_by_side(on_side, poll=0.01, lock=lock)

    # Первый статус — до запуска check: рендера по нему нет, замок не нужен.
    assert held[1:] and all(held[1:])
    assert not lock.locked()


def _exchange(monkeypatch, *, admin, console, op="get-tunnels"):
    """Прогон Server._exchange с подменёнными правами клиента."""
    server = ipc.Server(
        lambda seen, _payload, trusted: {"ok": True, "op": seen, "trusted": trusted},
        lambda _msg: None)
    monkeypatch.setitem(sys.modules, "win32security", types.SimpleNamespace(
        ImpersonateNamedPipeClient=lambda _pipe: None, RevertToSelf=lambda: None))
    monkeypatch.setattr(ipc, "_read_request",
                        lambda _pipe: json.dumps({"op": op, "payload": {}}).encode())
    monkeypatch.setattr(ipc, "_client_is_admin", lambda: admin)
    monkeypatch.setattr(ipc, "_client_is_console", lambda: console)
    replied = []
    monkeypatch.setattr(server, "_reply", lambda _pipe, obj: replied.append(obj))
    server._exchange("pipe")
    return replied


def test_команда_с_правами_открыта_консольному_пользователю(monkeypatch):
    assert _exchange(monkeypatch, admin=False, console=True) == [
        {"ok": True, "op": "get-tunnels", "trusted": True}]


def test_команда_с_правами_открыта_администратору(monkeypatch):
    assert _exchange(monkeypatch, admin=True, console=False) == [
        {"ok": True, "op": "get-tunnels", "trusted": True}]


def test_команда_с_правами_закрыта_чужой_сессии(monkeypatch):
    reply = _exchange(monkeypatch, admin=False, console=False)

    assert reply[0]["ok"] is False and reply[0]["needs_admin"] is True


def test_check_by_side_удалённый_туннель_не_ждёт(monkeypatch):
    st = _status(3, False, home=3, work=3)
    release = _fake_service(monkeypatch, st, drop_work=True)
    seen = []

    def on_side(got, pending):
        seen.append(pending)
        release.set()

    ipc.check_by_side(on_side, poll=0.01)

    assert seen[0] == frozenset()


def test_check_by_side_занятая_служба_не_отдаёт_свежий_итог(monkeypatch):
    st = _status(3, True, home=4, work=4)

    def call(_op, **_payload):
        return {"ok": True, "status": {**st, "busy": "перезапускаю"}}

    monkeypatch.setattr(ipc, "call", call)

    reply = ipc.check_by_side(lambda _st, _pending: None, poll=0.01)

    assert reply["fresh"] is False


def test_check_by_side_прошлый_seq_не_свежий(monkeypatch):
    st = _status(3, False, home=3, work=3)

    def call(_op, **_payload):
        return {"ok": True, "status": st}

    monkeypatch.setattr(ipc, "call", call)

    reply = ipc.check_by_side(lambda _st, _pending: None, poll=0.01)

    assert reply["fresh"] is False


def test_check_by_side_без_туннелей_не_свежий(monkeypatch):
    st = {**_status(3, False, home=3, work=3), "tunnels": []}

    def call(_op, **_payload):
        return {"ok": True, "status": st}

    monkeypatch.setattr(ipc, "call", call)

    reply = ipc.check_by_side(lambda _st, _pending: None, poll=0.01)

    assert reply["fresh"] is False


def test_check_by_side_смена_маршрута_не_свежий(monkeypatch):
    st = _status(3, False, home=3, work=3)

    def call(op, **_payload):
        if op == "check":
            return {"ok": True, "status": {**st, "check_seq": 4,
                                           "route_gen": 2,
                                           "tunnels": [{"id": "work", "seq": 4},
                                                       {"id": "home", "seq": 4}]}}
        return {"ok": True, "status": st}

    monkeypatch.setattr(ipc, "call", call)

    reply = ipc.check_by_side(lambda _st, _pending: None, poll=0.01)

    assert reply["fresh"] is False


def test_check_by_side_чужое_поколение_не_гасит_ожидание(monkeypatch):
    st = _status(3, False, home=3, work=3)
    changed = {**_status(4, False, home=4, work=4), "route_gen": 2}
    release = threading.Event()
    seen = []
    statuses = []

    def call(op, **_payload):
        if op == "check":
            release.wait(2)
            return {"ok": True, "status": changed}
        statuses.append(op)
        if len(statuses) == 1:
            return {"ok": True, "status": st}
        release.set()
        return {"ok": True, "status": changed}

    monkeypatch.setattr(ipc, "call", call)

    reply = ipc.check_by_side(
        lambda got, pending: seen.append((got.get("route_gen"), pending)),
        poll=0.01)

    assert seen == []
    assert len(statuses) >= 2
    assert reply["fresh"] is False


def test_check_by_side_идущая_проверка_не_свежая(monkeypatch):
    st = _status(4, False, home=4, work=4)

    def call(_op, **_payload):
        return {"ok": True, "status": {**st, "checking": True}}

    monkeypatch.setattr(ipc, "call", call)

    reply = ipc.check_by_side(lambda _st, _pending: None, poll=0.01)

    assert reply["fresh"] is False
