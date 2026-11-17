"""Окно: пишущие операции становятся в очередь рабочего потока, поток моста
не ждёт их ответа (блок A2 плана)."""

import threading

from tunnelvpn import ipc, window

WAIT = 5.0
BARRIER = "barrier"


class Recorder:
    """Подмена Api._dispatch: порядок, потоки, сколько операций идёт одновременно."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.order = []
        self.threads = set()
        self.running = 0
        self.max_running = 0
        self.done = 0
        self.lock = threading.Lock()
        self.barrier = None

    def dispatch(self, name, arg):
        if name == BARRIER:
            self.barrier.set()
            return None
        with self.lock:
            self.order.append(name)
            self.threads.add(threading.get_ident())
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        if self.delay:
            threading.Event().wait(self.delay)
        with self.lock:
            self.running -= 1
            self.done += 1
        return {"ok": True}


def _settled(api, rec, expected):
    """Барьерная задача проходит последней: всё поставленное до неё выработано."""
    rec.barrier = threading.Event()
    api._post(BARRIER, None)
    assert rec.barrier.wait(WAIT)
    assert rec.done == expected


def test_пишущая_операция_не_блокирует_поток_моста(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    api = window.Api({})
    ids = set()

    def dispatch(name, arg):
        ids.add(threading.get_ident())
        started.set()
        assert release.wait(WAIT)
        finished.set()
        return {"ok": True}

    monkeypatch.setattr(api, "_dispatch", dispatch)
    bridge = threading.get_ident()

    assert api.send("stop", {}) is None
    assert started.wait(WAIT)
    assert not finished.is_set()
    assert ids and bridge not in ids

    release.set()
    assert finished.wait(WAIT)


def test_второе_нажатие_становится_в_очередь_и_тоже_не_ждёт(monkeypatch):
    first = threading.Event()
    release = threading.Event()
    api = window.Api({})
    rec = Recorder()

    def dispatch(name, arg):
        if name == "start":
            first.set()
            assert release.wait(WAIT)
        return rec.dispatch(name, arg)

    monkeypatch.setattr(api, "_dispatch", dispatch)

    api.send("start", {})
    assert first.wait(WAIT)
    assert api.send("stop", {}) is None
    assert rec.order == []

    release.set()
    _settled(api, rec, 2)
    assert rec.order == ["start", "stop"]


def test_операции_идут_по_одной_в_порядке_очереди(monkeypatch):
    api = window.Api({})
    rec = Recorder(delay=0.02)
    monkeypatch.setattr(api, "_dispatch", rec.dispatch)

    for name in ("use_config", "del_config", "save_rules", "logs"):
        assert api.send(name, {}) is None

    _settled(api, rec, 4)
    assert rec.order == ["use_config", "del_config", "save_rules", "logs"]
    assert rec.max_running == 1


def test_задача_из_работающего_воркера_не_остаётся_висеть(monkeypatch):
    api = window.Api({})
    rec = Recorder()
    extra = threading.Event()

    def dispatch(name, arg):
        if name == "start":
            api._jobs.put(("stop", {}))
            extra.set()
        return rec.dispatch(name, arg)

    monkeypatch.setattr(api, "_dispatch", dispatch)

    api.send("start", {})
    assert extra.wait(WAIT)
    _settled(api, rec, 2)
    assert rec.order == ["start", "stop"]


def test_операции_с_ответом_странице_не_идут_в_очередь():
    for name in ("remove_profile", "set_mode", "restart_service", "add_link",
                 "add_subscription", "update_subscription", "remove_subscription",
                 "conf_details", "validate_rules", "preview"):
        assert name not in window.QUEUED_OPS


def test_читающие_операции_остаются_синхронными(monkeypatch):
    api = window.Api({})
    rec = Recorder()
    monkeypatch.setattr(api, "_dispatch", rec.dispatch)

    assert api.send("status", {}) == {"ok": True}
    assert rec.order == ["status"]
    assert rec.threads == {threading.get_ident()}


def test_ошибка_в_очередной_операции_уходит_странице(monkeypatch):
    api = window.Api({})
    shown = []
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))

    def boom(name, arg):
        if name == BARRIER:
            return rec.dispatch(name, arg)
        raise ipc.NotRunning("служба не отвечает")

    monkeypatch.setattr(api, "_dispatch", boom)
    rec = Recorder()

    api.send("start", {})
    _settled(api, rec, 0)
    assert shown[0][0] == "failed" and "не отвечает" in shown[0][1]
