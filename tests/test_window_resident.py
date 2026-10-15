"""Окно трея: спрятанное не проверяет туннели, холодное показывается на ready,
флажок автозапуска доходит до службы и всегда отпускается на странице, разбор
аргументов CLI.
"""

import os
import sys
import types

from tunnelvpn import cli, ipc, paths, window


class _SyncThread:
    """Поток, который выполняет цель сразу: тесту нечего ждать."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class _Timer:
    """Запасной таймер, который не срабатывает: _SyncThread вместо Thread
    ломает настоящий threading.Timer."""

    def __init__(self, delay, fn):
        self.daemon = False

    def start(self):
        pass


def _api(monkeypatch, reply=None):
    calls, shown = [], []
    monkeypatch.setattr(window.threading, "Thread", _SyncThread)
    monkeypatch.setattr(window.threading, "Timer", _Timer)
    monkeypatch.setattr(window, "PAINT_WAIT", 0)

    def call(op, **kw):
        calls.append((op, kw))
        if op == "set-autostart":
            return reply if reply is not None else {"ok": True}
        return {"ok": True, "status": {}}
    monkeypatch.setattr(ipc, "call", call)
    monkeypatch.setattr(ipc, "check_by_side",
                        lambda _cb, lock=None: calls.append(("check", {})) or {})
    api = window.Api({})
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append(fn))
    monkeypatch.setattr(api, "jsn", lambda fn, args: shown.append(fn))
    return api, calls, shown


def test_спрятанное_окно_на_ready_не_проверяет_туннели(monkeypatch):
    api, calls, _ = _api(monkeypatch)
    api.hidden = True

    api.send("ready")
    assert api.ready.is_set()
    assert "check" not in [op for op, _ in calls]


def test_показанное_окно_на_ready_проверяет_туннели(monkeypatch):
    api, calls, _ = _api(monkeypatch)

    api.send("ready")
    assert "check" in [op for op, _ in calls]


def test_проверка_окна_отдаёт_странице_ждущие_id_туннелей(monkeypatch):
    """Плитки — по id туннеля: каждый перекрашивается, как только проверен он сам."""
    api, _, _ = _api(monkeypatch)
    sent = []
    monkeypatch.setattr(api, "jsn", lambda fn, args: sent.append((fn, args[1])))
    st = {"tunnels": [{"id": "work", "mode": "list"}, {"id": "lab", "mode": "list"},
                      {"id": "home", "mode": "all"}]}

    def check_by_side(on_side, lock=None):
        for pending in ({"lab", "home"}, {"work"}, set()):
            on_side(st, frozenset(pending))
        return {}
    monkeypatch.setattr(ipc, "check_by_side", check_by_side)

    api._check()

    assert sent == [("checkSides", ["home", "lab"]), ("checkSides", ["work"]),
                    ("checkSides", [])]


def test_проверка_окна_рендерит_итог_свежим_статусом_под_замком(monkeypatch):
    """Статус из ответа check собран раньше статуса опроса: итог — новым
    status под тем же замком, что у опроса, и до checkDone."""
    api, calls, shown = _api(monkeypatch)
    got = {}

    def check_by_side(_on_side, lock=None):
        got["lock"] = lock
        return {"ok": True, "status": {"stale": True}}
    monkeypatch.setattr(ipc, "check_by_side", check_by_side)
    held = []
    monkeypatch.setattr(api, "js",
                        lambda fn, arg=None: held.append((fn, arg, api._render_lock.locked())))

    api._check()

    assert got["lock"] is api._render_lock
    assert calls[-1] == ("status", {})
    assert held == [("checkStart", None, False), ("render", {}, True),
                    ("checkDone", None, False)]


def test_статус_cli_пишет_итог_проверки_у_туннеля(capsys):
    st = {"up": True, "tunnels": [
        {"id": "work", "name": "Работа", "mode": "list", "active": "corp",
         "check": "up", "answer": "10.0.0.8"},
        {"id": "lab", "name": "Лаб", "mode": "list", "check": "none"},
        {"id": "dev", "name": "Дев", "mode": "list", "check": "error", "checking": True},
        {"id": "home", "name": "Личный", "mode": "all", "active": "nl-1", "check": "error"},
        {"id": "own", "name": "Свой", "mode": "list", "check": "rules",
         "answer": "corp.example"}]}

    cli._print_status(st)
    cli._print_status({**st, "up": False})

    lines = [l for l in capsys.readouterr().out.splitlines() if "туннель" in l]
    assert lines[0].endswith("— corp; отвечает 10.0.0.8")
    assert lines[1].endswith("; не с чем проверить: ни DNS в конфиге, ни домена в «пускать»")
    assert lines[2].endswith("; проверяю…")
    assert lines[3].endswith("— nl-1; молчит")
    assert lines[4].endswith("; отвечает, но «corp.example» через него не открывается "
                             "— проверьте туннелирование")
    assert lines[5].endswith("— corp")


class _Window:
    def __init__(self, shown):
        self.shown = shown

    def show(self):
        self.shown.append("show")


def test_холодное_окно_показывается_на_ready_уже_отрисованным(monkeypatch):
    api, calls, shown = _api(monkeypatch)
    api.holder["window"] = _Window(shown)
    api.cold = True

    api.send("ready")
    assert shown.index("render") < shown.index("show")
    assert "check" in [op for op, _ in calls]
    api.reveal()                    # запасной таймер после ready — уже ничего
    assert shown.count("show") == 1


def test_прогретое_окно_на_ready_остаётся_спрятанным(monkeypatch):
    api, _, shown = _api(monkeypatch)
    api.holder["window"] = _Window(shown)
    api.hidden = True

    api.send("ready")
    assert "show" not in shown


def test_открытое_окно_по_сигналу_только_выходит_вперёд(monkeypatch):
    api, calls, shown = _api(monkeypatch)
    api.ready.set()

    window._show(_Window(shown), api)
    assert shown == ["show"] and calls == []


def _focus_on(tid):
    paths.ensure_dirs()
    with open(paths.FOCUS_FILE, "w", encoding="utf-8") as fh:
        fh.write(tid)


def test_открытое_окно_по_сигналу_прокручивает_к_карточке_один_раз(monkeypatch):
    api, _, _ = _api(monkeypatch)
    focused = []
    monkeypatch.setattr(api, "js", lambda fn, arg=None: focused.append(arg) if fn == "focusCard" else None)
    api.ready.set()
    _focus_on("work")

    window._show(_Window([]), api)
    window._show(_Window([]), api)

    assert focused == ["work"]
    assert not os.path.exists(paths.FOCUS_FILE)


def test_карточку_клика_забирает_показанное_окно_после_отрисовки(monkeypatch):
    api, _, shown = _api(monkeypatch)
    _focus_on("work")

    api.send("ready")

    assert shown.index("render") < shown.index("focusCard")


def test_пункт_обновления_в_трее_открывает_о_программе_а_не_карточку(monkeypatch):
    api, _, shown = _api(monkeypatch)
    _focus_on(window.FOCUS_ABOUT)

    api.send("ready")

    assert "showAbout" in shown and "focusCard" not in shown
    assert not os.path.exists(paths.FOCUS_FILE)


def test_прогретое_окно_на_ready_карточку_не_забирает(monkeypatch):
    api, _, shown = _api(monkeypatch)
    api.hidden = True
    _focus_on("work")

    api.send("ready")

    assert "focusCard" not in shown and os.path.exists(paths.FOCUS_FILE)
    os.remove(paths.FOCUS_FILE)


def test_спрятанное_окно_по_сигналу_обновляется(monkeypatch):
    api, calls, shown = _api(monkeypatch)
    api.ready.set()
    api.hidden = True

    window._show(_Window(shown), api)
    assert "render" in shown and "check" in [op for op, _ in calls]


def test_прогретое_окно_по_сигналу_до_ready_ждёт_отрисовки(monkeypatch):
    """Трей только что запущен: сигнал показа обогнал загрузку страницы.
    Форма на виду стояла бы чёрной — показывает её ready, уже отрисованной."""
    api, calls, shown = _api(monkeypatch)
    timers = []
    monkeypatch.setattr(window.threading, "Timer",
                        lambda delay, fn: types.SimpleNamespace(
                            start=lambda: timers.append((delay, fn)), daemon=False))
    win = _Window(shown)
    api.holder["window"] = win
    api.hidden = True

    window._show(win, api)
    assert "show" not in shown
    assert timers == [(window.COLD_SHOW_WAIT, api.reveal)]

    api.send("ready")
    assert shown.index("render") < shown.index("show")
    assert "check" in [op for op, _ in calls]
    timers[0][1]()                  # запасной таймер после ready — уже ничего
    assert shown.count("show") == 1


class _Form:
    Location = (52, 52)
    Width, Height = 1040, 720
    StartPosition = None

    def Invoke(self, action):
        action()


def _parked(shown):
    # Форма не настоящая: Invoke зовёт делегат System.Action сразу.
    import clr  # noqa: F401  — без него нет модуля System
    win = _Window(shown)
    win.native = _Form()
    return win


def test_холодное_окно_показывается_за_краем_и_встаёт_на_место_после_отрисовки(monkeypatch):
    """Скрытый WebView2 кадр не рисует: окно показывают, пока оно за краем\n    экрана, ждут painted от страницы и только потом ставят на место."""
    api, _, shown = _api(monkeypatch)
    win = _parked(shown)
    api.holder["window"] = win
    api.cold, api._home = True, (440, 180)
    win.show = lambda: shown.append(("show", win.native.Location))
    waited = []
    monkeypatch.setattr(api.painted, "wait",
                        lambda t=None: waited.append((t, win.native.Location)))

    api.reveal()
    assert shown == [("show", (52, 52)), "paintAck"]   # (52, 52) — «за краем» в заглушке
    assert waited == [(window.PAINT_WAIT, (52, 52))]
    assert win.native.Location == (440, 180)


def test_прогретое_окно_при_первом_показе_ждёт_кадр_за_краем(monkeypatch):
    """15 октября: первый клик по значку после прогрева давал чёрную рамку —
    WebView2 спрятанного окна кадра ещё не рисовал. Кадр просят после Show."""
    api, _, shown = _api(monkeypatch)
    win = _parked(shown)
    api.holder["window"] = win
    api._home, api.hidden = (440, 180), True
    api.ready.set()
    api.painted.set()                               # кадр спрятанной страницы не в счёт
    win.show = lambda: shown.append(("show", win.native.Location))
    waited = []
    monkeypatch.setattr(api.painted, "wait",
                        lambda t=None: waited.append((api.painted.is_set(), win.native.Location)))

    window._show(win, api)
    assert shown[:2] == [("show", (52, 52)), "paintAck"]
    assert waited == [(False, (52, 52))]
    assert win.native.Location == (440, 180)

    shown.clear()
    api.hidden = True                               # закрыли крестиком и открыли снова
    window._show(win, api)
    assert "paintAck" not in shown and waited == [(False, (52, 52))]


def test_painted_отмечает_первый_кадр_страницы(monkeypatch):
    api, _, _ = _api(monkeypatch)

    api.send("painted")
    assert api.painted.is_set()


def test_место_возвращается_только_при_первом_показе(monkeypatch):
    api, _, shown = _api(monkeypatch)
    win = _parked(shown)
    api._home = (440, 180)
    api.ready.set()

    window._show(win, api)
    win.native.Location = (900, 300)          # пользователь передвинул окно
    api.hidden = True                         # и закрыл крестиком
    window._show(win, api)
    assert win.native.Location == (900, 300)


class _User32:
    """Окно 7 и чужое 9 впереди; SetForegroundWindow удаётся, только когда
    ввод сцеплен с потоком переднего окна (или право уже есть)."""

    def __init__(self, front=9, allowed=False):
        self.front, self.allowed, self.calls = front, allowed, []
        self.attached = False

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name,) + a)

    def IsIconic(self, hwnd):
        return False

    def GetForegroundWindow(self):
        return self.front

    def GetWindowThreadProcessId(self, hwnd, _pid):
        return 900

    def AttachThreadInput(self, own, other, on):
        self.calls.append(("AttachThreadInput", own, other, on))
        self.attached = on
        return True

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        if self.allowed or self.attached:
            self.front = hwnd
            return True
        return False


_kernel = types.SimpleNamespace(GetCurrentThreadId=lambda: 100)


def test_окно_без_права_на_передний_план_всё_равно_выходит_вперёд():
    """11 октября Activate молча не сработал, и прогретое окно открылось под
    редактором: поднимаем по z-порядку и сцепляем ввод с передним окном."""
    u = _User32()

    window._raise(7, u, _kernel)

    names = [c[0] for c in u.calls]
    assert ("SetWindowPos", 7, window.HWND_TOPMOST) == u.calls[0][:3]
    assert ("SetWindowPos", 7, window.HWND_NOTOPMOST) == u.calls[1][:3]
    assert u.front == 7
    assert ("AttachThreadInput", 100, 900, False) == u.calls[-1]
    assert names.count("AttachThreadInput") == 2


def test_окно_с_правом_не_сцепляет_ввод():
    u = _User32(allowed=True)

    window._raise(7, u, _kernel)

    assert u.front == 7
    assert "AttachThreadInput" not in [c[0] for c in u.calls]


def test_окно_уже_впереди_не_трогается():
    u = _User32(front=7)

    window._raise(7, u, _kernel)

    assert u.calls == []


def _forms(monkeypatch):
    """WinForms для _park подменён: рабочий стол 2560×1400, а не экран машины,
    и места — кортежи, с которыми сравнивает тест."""
    monkeypatch.setitem(sys.modules, "System.Drawing",
                        types.SimpleNamespace(Point=lambda x, y: (x, y)))
    area = types.SimpleNamespace(X=0, Y=0, Width=2560, Height=1400)
    monkeypatch.setitem(sys.modules, "System.Windows.Forms", types.SimpleNamespace(
        FormStartPosition=types.SimpleNamespace(Manual="manual"),
        Screen=types.SimpleNamespace(
            PrimaryScreen=types.SimpleNamespace(WorkingArea=area))))


def test_прогретое_окно_по_сигналу_встаёт_в_центр(monkeypatch):
    api, _, shown = _api(monkeypatch)
    win = _parked(shown)
    _forms(monkeypatch)
    api.hidden = True
    api.ready.set()

    window._park(win, api)
    assert win.native.Location == (-32000, -32000)
    window._show(win, api)
    assert win.native.Location == (760, 340)


def test_показ_раньше_before_show_не_оставляет_окно_за_краем(monkeypatch):
    """9 октября двойной клик сразу после входа пришёл раньше before_show:
    _unpark было нечего вернуть, _park увёл форму за край, и окно в панели
    задач есть, а на экране нет. Теперь такой показ ждёт ready страницы, и
    его reveal возвращает форму на место из _park."""
    api, _, shown = _api(monkeypatch)
    win = _parked(shown)
    _forms(monkeypatch)
    api.holder["window"] = win
    api.hidden = True

    window._show(win, api)
    window._park(win, api)
    api.send("ready")

    assert win.native.Location == (760, 340)
    assert shown.count("show") == 1


def test_место_окна_мосту_pywebview_не_видно():
    """Публичные атрибуты js_api pywebview обходит рекурсивно: .NET Point
    давал home.Empty.Empty… до переполнения стека на каждой загрузке."""
    api = window.Api({})

    assert not [n for n in vars(api) if "home" in n and not n.startswith("_")]


def test_включение_сразу_перерисовывает_статус(monkeypatch):
    """Не ждёт следующего опроса: иначе «работает» запаздывает на POLL_EVERY."""
    api, calls, shown = _api(monkeypatch)

    api.send("start")
    ops = [op for op, _ in calls]
    assert ops == ["start", "status"] and "render" in shown


def test_отказ_включения_тоже_перерисовывает_статус(monkeypatch):
    api, calls, shown = _api(monkeypatch)
    monkeypatch.setattr(ipc, "call", lambda op, **kw: calls.append((op, kw)) or
                        ({"ok": False, "error": "нет"} if op == "start" else {"status": {}}))

    api.send("restart")
    assert [op for op, _ in calls] == ["stop", "start", "status"]
    assert "failed" in shown and "render" in shown


def test_флажок_автозапуска_уходит_в_службу(monkeypatch):
    monkeypatch.setattr(window, "_is_admin", lambda: True)
    api, calls, shown = _api(monkeypatch)

    api.send("set_autostart", True)
    assert ("set-autostart", {"on": True}) in calls
    assert "autostartDone" in shown


def test_отказ_службы_тоже_отпускает_флажок(monkeypatch):
    monkeypatch.setattr(window, "_is_admin", lambda: True)
    api, _, shown = _api(monkeypatch, reply={"ok": False, "error": "нет"})

    api.send("set_autostart", False)
    assert "autostartDone" in shown and "failed" in shown


def test_cli_передаёт_режимы_окна_и_трея(monkeypatch):
    seen = {}
    monkeypatch.setattr(window, "open_window",
                        lambda **kw: seen.setdefault("window", kw))
    from tunnelvpn import tray
    monkeypatch.setattr(tray, "run", lambda **kw: seen.setdefault("tray", kw))

    cli.main(["window", "--resident", "--hidden", "--tray-pid", "4321"])
    cli.main(["tray", "--background"])
    assert seen == {"window": {"resident": True, "hidden": True, "tray_pid": 4321},
                    "tray": {"background": True}}


def test_cli_без_pid_трея_окно_низа_чем_не_следит(monkeypatch):
    seen = []
    monkeypatch.setattr(window, "open_window", lambda **kw: seen.append(kw))

    cli.main(["window"])
    cli.main(["window", "--tray-pid"])
    assert [kw["tray_pid"] for kw in seen] == [None, None]


def test_окно_узнаёт_о_выходе_трея():
    """Окно, пережившее трей, оставалось сиротой, и следующий трей открывал второе."""
    import subprocess
    import threading

    from tunnelvpn import instance

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    gone = threading.Event()
    try:
        instance.on_exit(proc.pid, gone.set)
        assert not gone.wait(0.3)
    finally:
        proc.kill()
        proc.wait()
    assert gone.wait(5)

    after = threading.Event()
    instance.on_exit(proc.pid, after.set)
    assert after.wait(5)


def test_код_выхода_на_сбой_webview2_по_виду_и_состоянию_окна():
    api = window.Api({})
    # Страница ещё не готова — тот же «не поднялся», что и по READY_WAIT.
    assert window._failure_exit("RenderProcessExited", api) == window.READY_LOST
    api.ready.set()
    assert window._failure_exit("BrowserProcessExited", api) == window.WEBVIEW_DIED_SHOWN
    api.hidden = True
    assert window._failure_exit("RenderProcessExited", api) == window.WEBVIEW_DIED
    # GPU и утилиты WebView2 поднимает сам, «не отвечает» бывает временным.
    for kind in ("GpuProcessExited", "UtilityProcessExited", "RenderProcessUnresponsive",
                 "FrameRenderProcessExited"):
        assert window._failure_exit(kind, api) is None


class _NetEvent:
    """Событие .NET: `+=` добавляет обработчик."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


def test_умерший_webview2_завершает_окно_кодом_для_трея(monkeypatch):
    """Без выхода окно с мёртвым WebView2 стояло чёрной рамкой."""
    exits = []
    monkeypatch.setattr(window.os, "_exit", exits.append)
    api = window.Api({})
    monkeypatch.setattr(api, "_log", lambda _msg: None)
    api.ready.set()
    control = types.SimpleNamespace(CoreWebView2InitializationCompleted=_NetEvent())
    window._watch_webview(types.SimpleNamespace(native=types.SimpleNamespace(webview=control)), api)

    core = types.SimpleNamespace(ProcessFailed=_NetEvent())
    sender = types.SimpleNamespace(CoreWebView2=core)
    for init in control.CoreWebView2InitializationCompleted.handlers:
        init(sender, types.SimpleNamespace(IsSuccess=True))
    for failed in core.ProcessFailed.handlers:
        failed(core, types.SimpleNamespace(ProcessFailedKind="GpuProcessExited"))
        failed(core, types.SimpleNamespace(ProcessFailedKind="RenderProcessExited"))
    assert exits == [window.WEBVIEW_DIED_SHOWN]
