"""Трей: одно окно панели и выход, не вешающий значок.

pystray и окно здесь не поднять — подменяем значок, процесс окна и ipc.call
и проверяем только решения трея: запускать ли окно и кто ждёт службу.
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from tunnelvpn import ipc, tray, update


class _Icon:
    def __init__(self):
        self.visible = True
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()


def _no_popen(*_a, **_kw):
    raise AssertionError("второй Popen при живом окне")


def _live(**kw):
    return types.SimpleNamespace(pid=42, poll=lambda: None, **kw)


def _launches(monkeypatch, code=0, once=False):
    """Подменённый Popen: команды запусков; процесс сразу выходит с code.

    once — с code выходит только первый, остальные с 0.
    """
    launched = []

    def popen(cmd, **_kw):
        launched.append(cmd)
        rc = code if not once or len(launched) == 1 else 0
        return types.SimpleNamespace(pid=7, poll=lambda: rc, wait=lambda: rc)
    monkeypatch.setattr(subprocess, "Popen", popen)
    return launched


def _signals(monkeypatch, listened=True, visible=True):
    sent = []
    monkeypatch.setattr(tray.instance, "signal",
                        lambda name, wait=0: sent.append(name) or listened)
    monkeypatch.setattr(tray.instance, "allow_foreground", lambda pid=0: None)
    monkeypatch.setattr(tray, "_wait_visible", lambda pid, timeout: visible)
    return sent


def test_живое_окно_показывается_сигналом_а_не_вторым_процессом(monkeypatch):
    sent = _signals(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", _no_popen)

    t = tray.Tray()
    t._window_proc = _live()
    t._open_window()
    assert sent == [tray.instance.WINDOW_SHOW]


def test_клик_по_туннелю_оставляет_окну_его_карточку_а_левый_клик_убирает(monkeypatch):
    """Файл от непоказавшегося окна не должен увести к карточке обычный показ."""
    sent = _signals(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    t = tray.Tray()
    t._window_proc = _live()

    t._open_window("work")
    with open(tray.paths.FOCUS_FILE, encoding="utf-8") as fh:
        assert fh.read() == "work"
    t._open_window()

    assert not os.path.exists(tray.paths.FOCUS_FILE)
    assert sent == [tray.instance.WINDOW_SHOW] * 2


def test_живой_процесс_без_окна_заменяется(monkeypatch):
    """Зависший WebView2 не должен навсегда запереть панель."""
    _signals(monkeypatch, visible=False)
    launched = _launches(monkeypatch)
    terminated = []

    t = tray.Tray()
    t._window_proc = _live(terminate=lambda: terminated.append(True))
    t._open_window()
    assert terminated and len(launched) == 1
    assert launched[0][-3:] == ["--resident", "--tray-pid", str(os.getpid())]


def test_без_окна_запускается_показываемое_окно_трея(monkeypatch):
    launched = _launches(monkeypatch)

    tray.Tray()._open_window()
    assert launched[0][-4:] == ["window", "--resident", "--tray-pid", str(os.getpid())]


def test_прогрев_запускает_спрятанное_окно_один_раз(monkeypatch):
    launched = _launches(monkeypatch)

    t = tray.Tray()
    t._warm()
    assert launched[0][-5:-2] == ["window", "--resident", "--hidden"]
    assert launched[0][-2:] == ["--tray-pid", str(os.getpid())]
    t._window_proc = _live()
    t._warm()
    assert len(launched) == 1


def test_после_выхода_окно_не_запускается(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)

    t = tray.Tray()
    t.stop_event.set()
    t._open_window()
    t._warm()


def _settle(launched, want):
    """Ждёт, пока сторожа окон перестанут запускать новые."""
    deadline = time.monotonic() + 5
    while len(launched) < want and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)                         # лишний запуск успел бы случиться
    return len(launched)


def test_окно_без_webview2_перезапускается_один_раз(monkeypatch):
    launched = _launches(monkeypatch, code=tray.window.READY_LOST)

    tray.Tray()._open_window()
    # Первый запуск и единственный повтор; повтор, вышедший так же, — всё.
    assert _settle(launched, 2) == 2


def test_прогретое_окно_без_webview2_не_перезапускается(monkeypatch):
    launched = _launches(monkeypatch, code=tray.window.READY_LOST)

    tray.Tray()._warm()
    assert _settle(launched, 1) == 1


def test_прогретое_окно_с_умершим_webview2_прогревается_снова(monkeypatch):
    monkeypatch.setattr(tray, "WEBVIEW_DIED_GAP", 0)
    launched = _launches(monkeypatch, code=tray.window.WEBVIEW_DIED, once=True)

    tray.Tray()._warm()
    assert _settle(launched, 2) == 2
    assert all("--hidden" in cmd for cmd in launched)


def test_показанное_окно_с_умершим_webview2_открывается_снова(monkeypatch):
    monkeypatch.setattr(tray, "WEBVIEW_DIED_GAP", 0)
    launched = _launches(monkeypatch, code=tray.window.WEBVIEW_DIED_SHOWN, once=True)

    tray.Tray()._open_window()
    assert _settle(launched, 2) == 2
    assert not any("--hidden" in cmd for cmd in launched)


def test_окно_после_смены_масштаба_пересоздаётся_каким_было(monkeypatch):
    """Без паузы WEBVIEW_DIED_GAP: окно выходит только на смену экрана."""
    launched = _launches(monkeypatch, code=tray.window.DPI_CHANGED_SHOWN, once=True)

    tray.Tray()._open_window()
    assert _settle(launched, 2) == 2
    assert not any("--hidden" in cmd for cmd in launched)


def test_прогретое_окно_после_смены_масштаба_прогревается_снова(monkeypatch):
    launched = _launches(monkeypatch, code=tray.window.DPI_CHANGED, once=True)

    tray.Tray()._warm()
    assert _settle(launched, 2) == 2
    assert all("--hidden" in cmd for cmd in launched)


def test_окно_умершее_сразу_после_старта_не_перезапускается(monkeypatch):
    """WebView2, падающий на каждом старте, не крутит перезапуски."""
    launched = _launches(monkeypatch, code=tray.window.WEBVIEW_DIED)

    tray.Tray()._warm()
    assert _settle(launched, 1) == 1


def _admin(monkeypatch, admin=True):
    monkeypatch.setattr(tray.window, "_is_admin", lambda: admin)


def test_второй_запуск_ярлыка_открывает_окно_первого(monkeypatch):
    _admin(monkeypatch)
    monkeypatch.setattr(tray.instance, "claim", lambda: False)
    sent = _signals(monkeypatch)

    assert tray.already_running() is True
    assert sent == [tray.instance.TRAY_OPEN]


def test_второй_запуск_при_входе_окно_не_открывает(monkeypatch):
    _admin(monkeypatch)
    monkeypatch.setattr(tray.instance, "claim", lambda: False)
    sent = _signals(monkeypatch)

    assert tray.already_running(background=True) is True
    assert sent == []


def test_окно_ищется_и_у_дочернего_процесса():
    """onefile-exe портативной версии: окно у Python-ребёнка загрузчика."""
    import os
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        assert tray._child_pids(os.getpid()) >= {os.getpid(), child.pid}
    finally:
        child.kill()
        child.wait()


def _no_claim():
    raise AssertionError("процесс без прав занял мьютекс трея")


def _no_tray(*_a, **_kw):
    raise AssertionError("трей поднят без прав")


def test_ярлык_без_прав_при_живом_трее_открывает_окно_без_uac(monkeypatch):
    _admin(monkeypatch, admin=False)
    monkeypatch.setattr(tray.instance, "claim", _no_claim)
    monkeypatch.setattr(tray.instance, "exists", lambda: True)
    sent = _signals(monkeypatch)
    elevated = []
    monkeypatch.setattr(tray, "_relaunch_elevated", lambda: elevated.append(True))
    monkeypatch.setattr(tray, "Tray", _no_tray)

    tray.run()
    assert sent == [tray.instance.TRAY_OPEN]
    assert elevated == []


def _no_tray_yet(monkeypatch, task, listened=True):
    """Ярлык без прав, трея нет; task — запустилась ли задача автозапуска."""
    _admin(monkeypatch, admin=False)
    monkeypatch.setattr(tray.instance, "claim", _no_claim)
    monkeypatch.setattr(tray.instance, "exists", lambda: False)
    monkeypatch.setattr(tray, "_run_task", lambda: task)
    sent = _signals(monkeypatch, listened=listened)
    elevated = []
    monkeypatch.setattr(tray, "_relaunch_elevated", lambda: elevated.append(True))
    monkeypatch.setattr(tray, "Tray", _no_tray)
    return sent, elevated


def test_ярлык_без_прав_без_трея_и_задачи_перезапускается_с_правами(monkeypatch):
    sent, elevated = _no_tray_yet(monkeypatch, task=False)

    tray.run()
    assert elevated == [True]
    assert sent == []


def test_ярлык_без_трея_поднимает_его_задачей_без_uac(monkeypatch):
    sent, elevated = _no_tray_yet(monkeypatch, task=True)
    # Трей задачи ответил — ярлык поднимает туннель сам; без подмены это
    # живая служба на машине.
    calls = []
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append(op) or {"status": {}})

    tray.run()
    assert sent == [tray.instance.TRAY_OPEN]
    assert elevated == []
    assert calls == ["status", "start"]


def test_трей_из_задачи_не_ответил_тогда_uac(monkeypatch):
    sent, elevated = _no_tray_yet(monkeypatch, task=True, listened=False)

    tray.run()
    assert sent == [tray.instance.TRAY_OPEN]
    assert elevated == [True]


def test_вход_без_прав_поднимает_трей_задачей_без_окна(monkeypatch):
    sent, elevated = _no_tray_yet(monkeypatch, task=True)

    tray.run(background=True)
    assert sent == []
    assert elevated == []


def test_задача_запускается_schtasks_по_имени(monkeypatch):
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=1)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert tray._run_task() is False
    assert calls[0][0].lower().endswith("schtasks.exe")
    assert calls[0][1:] == ["/Run", "/TN", tray.TRAY_TASK]


def test_первый_запуск_и_сбой_мьютекса_запускают_трей(monkeypatch):
    _admin(monkeypatch)
    monkeypatch.setattr(tray.instance, "claim", lambda: True)
    assert tray.already_running() is False

    def broken():
        raise OSError("сбой")
    monkeypatch.setattr(tray.instance, "claim", broken)
    assert tray.already_running() is False


def test_выход_не_ждёт_службу_в_потоке_значка(monkeypatch):
    """stop идёт до сорока секунд — поток значка должен освободиться сразу."""
    release = threading.Event()
    calls = []

    def call(op, **_kw):
        calls.append(op)
        release.wait(5)
        return {"ok": True}
    monkeypatch.setattr(ipc, "call", call)

    t = tray.Tray()
    t.icon = _Icon()
    t.on_quit()

    assert not t.icon.visible
    assert not t.icon.stopped.is_set()
    release.set()
    assert t.icon.stopped.wait(5)
    assert calls == ["stop"]


def test_выход_закрывает_значок_и_окно_даже_при_сбое_вызова(monkeypatch):
    def broken(*_a, **_kw):
        raise RuntimeError("сбой")
    monkeypatch.setattr(ipc, "call", broken)
    # Сбой уходит из рабочего потока уже после finally. Ловим его здесь, иначе
    # pytest припишет его тому тесту, который будет идти в этот момент.
    escaped = []
    thread_done = threading.Event()

    def excepthook(args):
        escaped.append(args.exc_type)
        thread_done.set()
    monkeypatch.setattr(threading, "excepthook", excepthook)

    terminated = threading.Event()
    t = tray.Tray()
    t.icon = _Icon()
    t._window_proc = types.SimpleNamespace(poll=lambda: None,
                                           terminate=terminated.set)
    t.on_quit()
    assert t.icon.stopped.wait(5)
    assert terminated.is_set()
    assert thread_done.wait(5)
    assert escaped == [RuntimeError]


def _wait_state(t, state):
    deadline = time.monotonic() + 5
    while t.state != state and time.monotonic() < deadline:
        time.sleep(0.01)
    return t.state


def test_пока_идёт_включение_значок_операции_а_после_ошибки_красный(monkeypatch):
    release = threading.Event()

    def call(op, **_kw):
        if op == "start":
            release.wait(5)
            return {"ok": False, "error": "нет конфига"}
        return {"ok": True, "status": {"last_error": "нет конфига"}}
    monkeypatch.setattr(ipc, "call", call)
    monkeypatch.setattr(tray.Tray, "_notify", lambda _self, _text: None)
    t = tray.Tray()

    t.on_toggle()
    assert t.state == "busy"
    # Опрос посреди команды не сбрасывает «занято», хотя служба ещё не занята.
    t._poll_once()
    assert t.state == "busy"

    release.set()
    assert _wait_state(t, "error") == "error"


def test_после_включения_значок_зелёный_без_ожидания_опроса(monkeypatch):
    started = threading.Event()

    def call(op, **_kw):
        if op == "start":
            started.set()
            return {"ok": True}
        return {"ok": True, "status": {"up": started.is_set()}}
    monkeypatch.setattr(ipc, "call", call)
    t = tray.Tray()

    t.on_toggle()

    assert _wait_state(t, "up") == "up"


def test_опрос_начинается_только_после_показа_значка(monkeypatch):
    """Цвет, сменённый до visible=True, pystray терял: значок серел при up."""
    seen = []
    t = tray.Tray()
    icon = _Icon()
    icon.visible = False
    t.icon = icon
    monkeypatch.setattr(t, "poll", lambda: seen.append(icon.visible))
    monkeypatch.setattr(t, "_watch_updates", lambda: None)

    t._setup(icon)

    for _ in range(100):
        if seen:
            break
        time.sleep(0.01)
    assert seen == [True]


def test_левый_клик_по_значку_не_включает_vpn():
    menu = tray.Tray()._menu()

    assert not any(item.default for item in menu.items)


class _Uxtheme:
    """uxtheme по ordinal: записывает, что и с чем позвали."""

    def __init__(self):
        self.calls = []

    def __getitem__(self, ordinal):
        return lambda *args: self.calls.append((ordinal, args))


def _fake_windows(monkeypatch, build):
    uxtheme = _Uxtheme()
    monkeypatch.setattr(ctypes, "windll",
                        types.SimpleNamespace(uxtheme=uxtheme))
    monkeypatch.setattr(sys, "getwindowsversion",
                        lambda: types.SimpleNamespace(build=build))
    return uxtheme.calls


def test_тёмное_меню_включается_с_windows_1903(monkeypatch):
    calls = _fake_windows(monkeypatch, 18362)

    tray._dark_menus()

    assert calls == [(135, (2,)), (136, ())]


def test_на_сборке_до_1903_ordinal_135_не_зовётся(monkeypatch):
    calls = _fake_windows(monkeypatch, 17763)

    tray._dark_menus()

    assert calls == []


def test_без_ordinal_в_uxtheme_меню_остаётся_светлым_без_ошибки(monkeypatch):
    class Missing:
        def __getitem__(self, ordinal):
            raise AttributeError(f"function ordinal {ordinal} not found")
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(uxtheme=Missing()))
    monkeypatch.setattr(sys, "getwindowsversion",
                        lambda: types.SimpleNamespace(build=26300))

    tray._dark_menus()


def test_кружки_без_vpn_красный_только_не_работавший_в_прошлый_раз():
    """Зелёный «раньше работал» читался как «работает сейчас»: 9 октября
    при упавшем автоподключении оба кружка горели зелёными, а корп был мёртв."""
    st = {"up": False, "tunnels": [{"id": "work", "last": "error"},
                                    {"id": "home", "last": "up"}]}

    assert tray._conf_colors(st, frozenset()) == {"work": "error", "home": "off"}


def test_кружки_без_vpn_и_без_итога_серые():
    assert tray._conf_colors({"up": False}, frozenset()) == {}
    st = {"up": False, "tunnels": [{"id": "work", "last": ""}, {"id": "home"}]}
    assert tray._conf_colors(st, frozenset()) == {"work": "off", "home": "off"}


_MAIN = {"id": "home", "mode": "all"}
_SIDE = {"id": "work", "mode": "list"}


@pytest.mark.parametrize("st, state", [
    ({}, "off"),
    ({"last_error": "нет конфига"}, "error"),
    ({"busy": "включаю", "last_error": "старая"}, "busy"),
    ({"up": True, "busy": "перезапускаю «Работа»"}, "busy"),
    ({"up": True, "tunnels": [_MAIN, _SIDE], "exit_state": "tunnel"}, "up"),
    ({"up": True, "tunnels": [_MAIN], "out": "direct"}, "fallback"),
    ({"up": True, "tunnels": [_MAIN], "exit_state": "direct"}, "fallback"),
    # Kill-switch: out сторож переключает раньше, чем перемерится exit_state.
    ({"up": True, "tunnels": [_MAIN], "out": "block", "exit_state": "direct"}, "blocked"),
    ({"up": True, "tunnels": [_MAIN], "exit_state": "blocked"}, "blocked"),
    ({"up": True, "tunnels": [_MAIN], "out": "direct", "exit_state": "blocked"}, "fallback"),
    ({"up": True, "tunnels": [_MAIN], "exit_state": "leak"}, "leak"),
    ({"up": True, "tunnels": [_MAIN], "out": "direct", "v6_leak": "2a00::1"}, "leak"),
    # Без основного напрямую идёт всё остальное — так задумано.
    ({"up": True, "tunnels": [_SIDE], "exit_state": "direct"}, "up"),
    # Выключенный основной — то же: он основной, но не везёт ничего.
    ({"up": True, "tunnels": [{**_MAIN, "enabled": False}, _SIDE], "out": "direct",
      "exit_state": "direct"}, "up"),
])
def test_состояние_значка_по_статусу(st, state):
    assert tray._tray_state(st) == state


def test_без_основного_прямой_выход_не_запасной():
    t = tray.Tray()
    t.status = {"up": True, "tunnels": [_SIDE], "out": "direct", "exit_ip": "1.2.3.4"}

    assert t._title() == "TunnelVPN — работает · всё остальное напрямую"


def test_запасной_выход_меняет_значок_при_опросе(monkeypatch):
    monkeypatch.setattr(ipc, "call", lambda op, **_kw: {"status": {
        "up": True, "tunnels": [_MAIN], "out": "direct"}})
    t = tray.Tray()

    t._poll_once()

    assert t.state == "fallback"


@pytest.mark.parametrize("size", [16, 20, 24])
def test_значок_трея_грузится_в_своём_размере(size):
    """20 точек при 125 % не переделывается ни в 16, ни в 32."""
    import win32gui

    from tunnelvpn import icon

    hicon = tray._load_icon(icon.tray("up", size))
    try:
        _is_icon, _x, _y, mask, color = win32gui.GetIconInfo(hicon)
        assert win32gui.GetObject(color).bmWidth == size
        win32gui.DeleteObject(mask)
        win32gui.DeleteObject(color)
    finally:
        win32gui.DestroyIcon(hicon)


# ------------------------------------------------------ обновление из окна

@pytest.fixture
def mark():
    """Метка обновления из окна без кода: setup ещё работает."""
    update._write(update.MARK_FILE, json.dumps(
        {"version": "0.3.8", "from": "0.3.7", "at": time.time()}))
    yield
    update.clear()


def _no_service(_op, **_kw):
    raise ipc.NotRunning("служба не отвечает")


def test_пока_ставится_обновление_значок_занят_а_не_красный(monkeypatch, mark):
    """Службу остановил установщик: это ход обновления, а не «не отвечает»."""
    monkeypatch.setattr(ipc, "call", _no_service)
    t = tray.Tray()

    t._poll_once()
    assert (t.state, t._title()) == ("busy", "Обновляю TunnelVPN…")

    update._write(update.CODE_FILE, "0")
    t._poll_once()
    assert (t.state, t._title()) == ("error", "TunnelVPN — служба не отвечает")


_SVC_UP = {"installed": True, "running": True, "start": "auto"}


def _healable(monkeypatch, svc=_SVC_UP, is_portable=False):
    """Служба молчит; перезапуск ждёт release. Возвращает (вызовы, журнал, release)."""
    monkeypatch.setattr(ipc, "call", _no_service)
    monkeypatch.setattr(tray.portable, "is_portable", lambda: is_portable)
    monkeypatch.setattr(tray.settings, "service_state", lambda: svc)
    calls, logged, release = [], [], threading.Event()

    def restart():
        calls.append(1)
        release.wait(5)
        return "служба перезапущена"
    monkeypatch.setattr(tray.settings, "restart_service", restart)
    monkeypatch.setattr(tray, "_log", logged.append)
    return calls, logged, release


def _wait(cond):
    deadline = time.monotonic() + 5
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_молчащую_службу_трей_перезапускает_сам_и_один_раз(monkeypatch):
    """Служба жива, а канал молчит: раньше окно до перезагрузки звало
    перезагрузить компьютер. Теперь трей перезапускает её сам."""
    calls, logged, release = _healable(monkeypatch)
    t = tray.Tray()

    t._poll_once()
    assert (calls, t.state) == ([], "error")           # один опрос — ещё не повод

    t._silent_since -= tray.HEAL_AFTER
    t._poll_once()
    _wait(lambda: calls)
    t._poll_once()                                      # пока идёт — второго нет
    assert (t.state, t._title()) == ("busy", "TunnelVPN — служба не отвечает, перезапускаю её…")

    release.set()
    _wait(lambda: not t._healing)
    assert calls == [1]
    assert logged[-1] == "→ служба перезапущена"

    t._poll_once()
    t._silent_since -= tray.HEAL_AFTER
    t._poll_once()                                      # снова молчит — ждём HEAL_EVERY
    assert (calls, t.state) == ([1], "error")


@pytest.mark.parametrize("svc, is_portable", [
    (_SVC_UP, True),
    ({"installed": True, "running": False, "start": "disabled"}, False),
    ({"installed": False, "running": False, "start": ""}, False),
    (None, False),
], ids=["портативная", "отключена руками", "не установлена", "не узнать"])
def test_службу_которую_не_поднять_трей_не_трогает(monkeypatch, svc, is_portable):
    calls, _logged, _release = _healable(monkeypatch, svc, is_portable)
    t = tray.Tray()

    t._poll_once()
    t._silent_since -= tray.HEAL_AFTER
    t._poll_once()

    assert (calls, t._healing, t.state) == ([], False, "error")


def test_пока_ставится_обновление_включить_и_перезапустить_недоступны(monkeypatch, mark):
    monkeypatch.setattr(ipc, "call", lambda _op, **_kw: {"ok": True, "status": {"up": True}})
    t = tray.Tray()
    menu = t._menu()

    t._poll_once()

    assert [item.text for item in menu if not item.enabled] == ["Выключить", "Перезапустить"]


def _offered(monkeypatch):
    """Трей с найденной 0.3.8 и его меню после опроса и перестройки."""
    monkeypatch.setattr(ipc, "call", lambda _op, **_kw: {"ok": True, "status": {}})
    t = tray.Tray()
    menu = t._menu()
    t.offer = "0.3.8"
    t._poll_once()
    t.icon = types.SimpleNamespace(update_menu=lambda: None)
    t._sync_menu()
    return t, [item.text for item in menu]


def test_найденное_обновление_первый_пункт_меню(monkeypatch):
    _t, texts = _offered(monkeypatch)

    assert texts[0] == "Доступно обновление 0.3.8"


def test_пока_обновление_ставится_пункта_о_нём_нет(monkeypatch, mark):
    _t, texts = _offered(monkeypatch)

    assert not any(text.startswith("Доступно обновление") for text in texts)


def test_пункт_обновления_открывает_окно_на_о_программе(monkeypatch):
    t = tray.Tray()
    opened = []
    monkeypatch.setattr(t, "on_window", lambda focus=None: opened.append(focus))

    t.on_about()

    assert opened == [tray.window.FOCUS_ABOUT]


def test_проверка_ставит_и_снимает_пункт_а_сбой_его_не_трогает(monkeypatch):
    answers = [{"state": "update", "latest": "0.3.8"}, {"state": "error"}, OSError("сеть"),
               {"state": "latest", "latest": "0.3.8"}, {"state": "error"}]
    t = tray.Tray()
    seen = []

    def check(_current):
        seen.append(t.offer)
        if len(seen) == len(answers):
            t.stop_event.set()
        answer = answers[len(seen) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(tray.settings, "check_update", check)
    monkeypatch.setattr(tray, "UPDATE_FIRST", 0)
    monkeypatch.setattr(tray, "UPDATE_EVERY", 0)

    t._watch_updates()

    assert seen == [None, "0.3.8", "0.3.8", "0.3.8", None]


def _start(monkeypatch, background):
    """_start_window: какое окно открылось сразу и какое прогрето."""
    opened, warmed = [], []
    monkeypatch.setattr(tray.instance, "listen", lambda *_a, **_kw: None)
    t = tray.Tray()
    monkeypatch.setattr(t, "on_window", lambda focus=None: opened.append(focus))
    monkeypatch.setattr(t, "_warm", lambda: warmed.append(True))

    t._start_window(background)
    return t, opened, warmed


def test_при_входе_окно_сразу_прогревается_спрятанным(monkeypatch):
    _t, opened, warmed = _start(monkeypatch, background=True)

    assert opened == [] and warmed == [True]


def test_после_обновления_трей_сразу_открывает_окно_с_итогом(monkeypatch, mark):
    _t, opened, warmed = _start(monkeypatch, background=True)

    assert opened == [None] and warmed == []
