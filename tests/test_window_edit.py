"""Окно: «Редактировать» открывает конфиг в Блокноте с правами.

Имя приходит со страницы, поэтому путь собирается только из имён, которые
служба сама отдала в статусе.
"""

import ntpath
import os

from tunnelvpn import ipc, paths, window

WORK = {"id": "work", "name": "Работа", "mode": "list", "active": "corp",
        "confs": ["corp"]}
HOME = {"id": "home", "name": "Личный", "mode": "all", "active": "nl-1",
        "confs": ["nl-1"]}


class _SyncThread:
    """Поток, который выполняет цель сразу: тесту нечего ждать."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def _setup(monkeypatch, status):
    runs, shown = [], []
    monkeypatch.setattr(window.threading, "Thread", _SyncThread)
    monkeypatch.setattr(ipc, "call", lambda op, **_kw: {"ok": True, "status": status})
    monkeypatch.setattr(window, "_run_elevated",
                        lambda exe, args, show=False: runs.append((exe, args, show)))
    api = window.Api({})
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))
    return api, runs, shown


def test_конфиг_открывается_в_блокноте_с_правами(monkeypatch):
    api, runs, _ = _setup(monkeypatch, {"tunnels": [WORK, HOME]})

    api.send("edit_config", {"tunnel": "home", "name": "nl-1"})

    exe, args, show = runs[0]
    # Путь Windows: на Linux-раннере os.path его не разберёт.
    assert ntpath.isabs(exe) and ntpath.normpath(exe).lower().endswith("system32\\notepad.exe")
    assert args == [os.path.join(paths.CONF_TUNNELS, "home", "nl-1.conf")] and show


def test_конфиг_берётся_из_папки_своего_туннеля(monkeypatch):
    api, runs, _ = _setup(monkeypatch, {"tunnels": [WORK, HOME]})

    api.send("edit_config", {"tunnel": "work", "name": "corp"})

    assert runs[0][1] == [os.path.join(paths.CONF_TUNNELS, "work", "corp.conf")]


def test_без_туннеля_в_статусе_не_открывается(monkeypatch):
    api, runs, shown = _setup(monkeypatch, {"tunnels": [HOME]})

    api.send("edit_config", {"tunnel": "work", "name": "corp"})

    assert runs == []
    assert shown[0][0] == "failed"


def test_имя_не_из_статуса_не_открывается(monkeypatch):
    api, runs, shown = _setup(monkeypatch, {"tunnels": [WORK, HOME]})

    api.send("edit_config", {"tunnel": "home", "name": "..\\..\\Windows\\win"})

    assert runs == []
    assert shown[0][0] == "failed"


def _no_pipe(op, **_kw):
    raise ipc.NotRunning("служба не отвечает")


def test_без_канала_окно_сообщает_стоит_ли_служба(monkeypatch):
    monkeypatch.setattr(ipc, "call", _no_pipe)
    monkeypatch.setattr(window, "_service_installed", lambda: False)
    api, shown = window.Api({}), []
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))

    api.refresh()

    assert shown == [("render", {"up": False, "no_service": True, "daemon": False})]


def test_портативная_без_канала_не_просит_ставить_службу(monkeypatch):
    monkeypatch.setattr(ipc, "call", _no_pipe)
    monkeypatch.setattr(window.portable, "is_portable", lambda: True)
    monkeypatch.setattr(window, "_service_installed", lambda: False)
    api, shown = window.Api({}), []
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))

    api.refresh()

    assert shown == [("render", {"up": False, "no_service": True, "portable": True})]


def _revive(monkeypatch, answers, restart=lambda: "служба перезапущена"):
    """«Перезапустить службу»: answers — ответит ли канал на каждый опрос."""
    left, restarts, logged = list(answers), [], []

    def call(op, **_kw):
        if not (left.pop(0) if left else True):
            raise ipc.NotRunning("служба не отвечает")
        return {"ok": True, "status": {"up": True}}

    def run():
        restarts.append(1)
        return restart()

    monkeypatch.setattr(ipc, "call", call)
    monkeypatch.setattr(window.settings, "restart_service", run)
    monkeypatch.setattr(window.portable, "is_portable", lambda: False)
    monkeypatch.setattr(window, "_service_installed", lambda: True)
    monkeypatch.setattr(window.time, "sleep", lambda _s: None)
    api = window.Api({})
    monkeypatch.setattr(api, "js", lambda fn, arg=None: None)
    monkeypatch.setattr(api, "_log", logged.append)
    return api, restarts, logged


def test_молчащая_служба_перезапускается_из_окна(monkeypatch):
    api, restarts, logged = _revive(monkeypatch, [False, False, True])

    assert api.send("restart_service") == {"ok": True}
    assert restarts == [1]
    assert "перезапуск из окна: служба перезапущена" in logged[0]


def test_ответившую_службу_окно_не_перезапускает(monkeypatch):
    api, restarts, _ = _revive(monkeypatch, [True])

    assert api.send("restart_service") == {"ok": True}
    assert restarts == []


def test_сбой_перезапуска_человеческой_фразой_причина_в_журнал(monkeypatch):
    def fail():
        raise OSError("отказано в доступе")

    api, _, logged = _revive(monkeypatch, [False, False], restart=fail)

    assert api.send("restart_service") == {"ok": False, "error": window.RESTART_FAILED}
    assert "отказано в доступе" in logged[-1]


def test_запущенная_но_молчащая_служба_это_сбой(monkeypatch):
    monkeypatch.setattr(window, "SERVICE_PIPE_WAIT", 0.0)
    api, restarts, logged = _revive(monkeypatch, [False] * 10)

    assert api.send("restart_service") == {"ok": False, "error": window.RESTART_FAILED}
    assert restarts == [1] and "канал молчит" in logged[-1]


def _save_rules(monkeypatch, up, reply=None):
    """Сохранить правила туннеля; вернуть команды службе и вызовы страницы."""
    ops, admin, shown = [], [], []

    def call(op, **_kw):
        ops.append(op)
        if op == "start":
            return {"ok": False, "error": "нет конфига"}
        return {"ok": True, "status": {"up": up}}

    def admin_call(op, **kw):
        admin.append((op, kw))
        return reply or {"ok": True, "include": ["a.ru"], "exclude": [],
                         "rejected": {"include": ["foo_bar"]}}

    monkeypatch.setattr(ipc, "call", call)
    api = window.Api({})
    monkeypatch.setattr(api, "_admin_call", admin_call)
    monkeypatch.setattr(api, "js", lambda fn, arg=None: shown.append((fn, arg)))
    api.send("save_rules", {"tunnel": "work", "include": "a.ru foo_bar"})
    return ops, admin, shown


def test_правила_уходят_текстом_поля_а_ответ_со_списками_на_страницу(monkeypatch):
    _, admin, shown = _save_rules(monkeypatch, up=False)

    # Только присланные поля: exclude не пришёл — служба его не трогает.
    assert admin == [("set-tunnel", {"tunnel": "work", "include": "a.ru foo_bar"})]
    assert ("rulesSaved", {"include": ["a.ru"], "exclude": [], "exclude_apps": [],
                           "rejected": {"include": ["foo_bar"]}, "mode": "",
                           "applied": ""}) in shown


def _save_then_apply(monkeypatch, applied):
    """Команды службе и вызовы страницы — одной лентой, по порядку."""
    events = []

    def call(op, **kw):
        events.append(("op", op))
        return {"ok": True, "applied": applied} if op == "apply" else {"ok": True, "status": {}}

    monkeypatch.setattr(ipc, "call", call)
    api = window.Api({})
    monkeypatch.setattr(api, "_admin_call", lambda op, **kw: {"ok": True, "include": ["a.ru"]})
    monkeypatch.setattr(api, "js", lambda fn, arg=None: events.append((fn, arg)))
    api.send("save_rules", {"tunnel": "work", "include": "a.ru"})
    return events


def test_сохранено_только_после_ответа_apply(monkeypatch):
    """«Сохранено» до apply обещало действие, которого ещё нет."""
    events = _save_then_apply(monkeypatch, "sides")

    saved_at = next(i for i, (fn, _) in enumerate(events) if fn == "rulesSaved")
    assert events.index(("op", "apply")) < saved_at
    saved = next(arg for fn, arg in events if fn == "rulesSaved")
    assert saved["applied"] == "sides"


def test_отложенная_правка_сохранена_до_вопроса_о_переподключении(monkeypatch):
    events = _save_then_apply(monkeypatch, "ask")

    fns = [fn for fn, _ in events if fn != "op"]
    assert fns.index("rulesSaved") < fns.index("askFull")
    assert next(arg for fn, arg in events if fn == "rulesSaved")["applied"] == "ask"


def test_отказ_службы_показан_в_листе_и_ничего_не_применяется(monkeypatch):
    ops, _, shown = _save_rules(monkeypatch, up=True,
                                reply={"ok": False, "error": "«a.ru» уже в «Лаб»"})

    # Строка состояния — под листом: отказ должен стоять в самом листе.
    assert ("rulesFailed", {"error": "«a.ru» уже в «Лаб»", "problems": []}) in shown
    assert "rulesSaved" not in [fn for fn, _ in shown]
    assert "apply" not in ops


def test_проблемы_по_полям_доходят_до_листа_со_строкой_записи(monkeypatch):
    problems = [{"field": "include", "text": "«a.ru» уже в «Лаб»", "entry": "a.ru"},
                {"field": "include", "text": "очистите и «не пускать»"}]
    _, _, shown = _save_rules(monkeypatch, up=True,
                              reply={"ok": False, "error": "x", "problems": problems})

    # По полям, а не одной строкой: лист красит поле и пишет причины под ним;
    # у дубля — строка и токен, чтобы выделить запись в поле.
    assert ("rulesFailed", {"error": "x", "problems": [
        {"field": "include", "text": "«a.ru» уже в «Лаб»", "line": 1, "token": "a.ru"},
        {"field": "include", "text": "очистите и «не пускать»", "line": None, "token": ""},
    ]}) in shown


def test_ушёл_запасным_лист_закрывается_и_применяется(monkeypatch):
    moved = {"tunnel": "home", "name": "nl", "main": "Личный"}
    ops, _, shown = _save_rules(monkeypatch, up=True, reply={"ok": True, "moved": moved})

    # Туннеля листа больше нет: не rulesSaved с его полями, а закрыть и пересобрать.
    assert ("rulesMoved", moved) in shown
    assert "rulesSaved" not in [fn for fn, _ in shown]
    assert "apply" in ops


def test_правка_при_выключенном_vpn_не_включает_его(monkeypatch):
    ops, _, shown = _save_rules(monkeypatch, up=False)

    # Без конфигов «Включить» падало бы с «нет конфига» — его и не зовём.
    assert "start" not in ops and "stop" not in ops
    assert "failed" not in [fn for fn, _ in shown]
    assert "restarting" not in [fn for fn, _ in shown]


def test_правка_при_включённом_vpn_применяет_её_службой_без_выключения(monkeypatch):
    ops, _, shown = _save_rules(monkeypatch, up=True)

    # Что перезапускать — процесс туннеля или всё, — решает служба (apply):
    # окно само VPN не выключает, иначе интернет падал на каждую правку.
    assert "apply" in ops
    assert "start" not in ops and "stop" not in ops
    assert "restarting" not in [fn for fn, _ in shown]


# ------------------------------------------------- validate_rules: на лету

def _validate(monkeypatch, arg, admin=True, reply=None):
    """validate_rules со службой, отвечающей reply; вернуть ответ и команды."""
    calls = []

    def call(op, **kw):
        calls.append((op, kw))
        return reply or {"ok": True, "problems": [], "fate": {}}

    monkeypatch.setattr(ipc, "call", call)
    monkeypatch.setattr(window, "_is_admin", lambda: admin)
    return window.Api({}).send("validate_rules", arg), calls


def test_разбор_полей_без_службы_и_со_строкой_непонятого(monkeypatch):
    r, calls = _validate(monkeypatch, {"tunnel": "work", "include": "a.ru\ngit",
                                       "exclude": "1.2.3.4"})

    # Непонятое — до службы: дубли с ним проверять незачем.
    assert calls == []
    inc = r["fields"]["include"]
    assert inc["count"] == 1 and [(p["line"], p["token"]) for p in inc["problems"]] == [(2, "git")]
    assert r["fields"]["exclude"]["ips"] == 1 and r["max"] == 500


def test_дубли_с_правами_через_check_rules_со_строкой(monkeypatch):
    reply = {"ok": True, "fate": {"to": "main"},
             "problems": [{"field": "include", "text": "«a.ru» уже в «Лаб»", "entry": "a.ru"}]}

    r, calls = _validate(monkeypatch, {"tunnel": "work", "include": "b.ru\n A.RU"},
                         reply=reply)

    assert calls == [("check-rules", {"tunnel": "work", "include": "b.ru\n A.RU"})]
    assert r["fields"]["include"]["problems"] == [
        {"text": "«a.ru» уже в «Лаб»", "line": 2, "token": "A.RU"}]
    assert r["fate"] == {"to": "main"}


def test_программы_разбираются_без_службы_со_строкой_непонятого(monkeypatch):
    r, calls = _validate(monkeypatch, {"tunnel": "home",
                                       "exclude_apps": "C:\\a.exe\n\nsteam.exe"})

    # С другими туннелями программы не сверяются: check-rules не зовётся.
    assert calls == []
    apps = r["fields"]["exclude_apps"]
    assert apps["count"] == 1
    assert [(p["line"], p["token"]) for p in apps["problems"]] == [(3, "steam.exe")]
    assert r["max_apps"] == 100


def test_без_прав_дубли_не_спрашиваются(monkeypatch):
    r, calls = _validate(monkeypatch, {"tunnel": "work", "include": "a.ru"}, admin=False)

    # Иначе UAC на каждую паузу ввода: дубли покажет «Сохранить».
    assert calls == [] and r["ok"] and r["fields"]["include"]["problems"] == []


def test_отказ_check_rules_под_первым_полем(monkeypatch):
    r, _ = _validate(monkeypatch, {"tunnel": "home", "exclude": "a.ru"},
                     reply={"ok": False, "error": "нет туннеля home"})

    assert r["fields"]["exclude"]["problems"] == [
        {"line": None, "token": "", "text": "нет туннеля home"}]


def test_проблема_чужого_поля_встаёт_под_своё(monkeypatch):
    reply = {"ok": True, "fate": {},
             "problems": [{"field": "include", "text": "дубль", "entry": "a.ru"}]}

    r, _ = _validate(monkeypatch, {"tunnel": "home", "exclude": "a.ru"}, reply=reply)

    # У основного в листе одно поле — причина всё равно видна.
    assert r["fields"]["exclude"]["problems"] == [{"text": "дубль", "line": None, "token": ""}]
