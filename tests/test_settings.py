"""Вкладка «Настройки»: задача трея, обновления, диагностика и ветки моста."""

import base64
import subprocess

from tunnelvpn import ipc, paths, settings, window

TASK = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      {trigger}<UserId>PC\\Пользователь</UserId>
    </LogonTrigger>
  </Triggers>
  <Settings>
    {settings}<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  </Settings>
</Task>
"""


def _xml(trigger="", task_settings="", utf16=True):
    text = TASK.format(trigger=trigger, settings=task_settings)
    return text.encode("utf-16") if utf16 else text.encode("utf-8")


def test_задача_с_включённым_входом():
    assert settings.logon_enabled(_xml()) is True
    assert settings.logon_enabled(_xml(utf16=False)) is True


def test_выключенный_триггер_входа_значит_нет():
    assert settings.logon_enabled(_xml(trigger="<Enabled>false</Enabled>")) is False


def test_выключенная_задача_значит_нет():
    assert settings.logon_enabled(_xml(task_settings="<Enabled>false</Enabled>")) is False


def test_задача_без_триггера_входа_значит_нет():
    assert settings.logon_enabled(b"<Task><Triggers/></Task>") is False


def test_нет_задачи(monkeypatch):
    monkeypatch.setattr(settings, "_run", lambda args, timeout=15:
                        subprocess.CompletedProcess(args, 1, b"", b""))
    assert settings.tray_task() is None


def _script(encoded):
    return base64.b64decode(encoded).decode("utf-16-le")


def test_команда_меняет_только_триггер_у_задачи():
    off = _script(settings.tray_task_command(False, True, r"C:\x\TunnelVPN-Tray.exe"))
    on = _script(settings.tray_task_command(True, True, r"C:\x\TunnelVPN-Tray.exe"))

    assert "$g.Enabled = $false" in off and "Register-ScheduledTask" not in off
    assert "Settings.Enabled" not in off
    assert "$g.Enabled = $true" in on and "$t.Settings.Enabled = $true" in on
    assert f"-TaskName '{settings.TRAY_TASK}'" in on


def test_без_задачи_ставится_как_у_установщика():
    s = _script(settings.tray_task_command(True, False, r"C:\Program Files\O'Brien\TunnelVPN-Tray.exe"))

    assert "Register-ScheduledTask" in s and "-AtLogOn" in s and "-RunLevel Highest" in s
    assert "-Argument '--background'" in s
    # Кавычка в пути не рвёт строку PowerShell.
    assert r"-Execute 'C:\Program Files\O''Brien\TunnelVPN-Tray.exe'" in s


def _releases(*tags, draft=()):
    return lambda: [{"tag_name": t, "draft": t in draft} for t in tags]


def test_доступна_новая_версия_windows():
    r = settings.check_update("0.3.4-beta", _releases("mac-v0.9.0", "v0.3.7-beta", "v0.3.10-beta"))

    assert r == {"state": "update", "latest": "0.3.10-beta",
                 "url": f"{settings.REPO_URL}/releases/tag/v0.3.10-beta", "notes": ""}


def test_что_нового_простым_текстом():
    md = ("## Что нового\r\n\r\n<!-- черновик -->\r\n- **Обновление** из окна\r\n"
          "* ссылка [релиз](https://example.com) и `код`\r\n\r\n\r\n\r\nКонец")
    assert settings.plain_notes(md) == ("Что нового\n\n• Обновление из окна\n"
                                        "• ссылка релиз и код\n\nКонец")
    assert settings.plain_notes(None) == ""
    assert len(settings.plain_notes("а" * 10000)) == settings.NOTES_MAX


def test_последняя_уже_стоит():
    r = settings.check_update("0.3.7-beta", _releases("v0.3.7-beta", "v0.3.6-beta"))
    assert r["state"] == "latest"


def test_черновик_и_чужой_тег_не_считаются():
    r = settings.check_update("0.3.7-beta", _releases("v0.4.0", "v1.0.0;rm", "v0.3.7-beta",
                                                      draft=("v0.4.0",)))
    assert r["state"] == "latest"


def test_нет_связи():
    def fail():
        raise OSError("timed out")

    assert settings.check_update("0.3.7-beta", fail) == {"state": "error"}
    assert settings.check_update("0.3.7-beta", lambda: {"message": "rate limit"}) == {"state": "error"}


def test_ссылки_только_на_свой_репозиторий():
    assert settings.repo_url(settings.REPO_URL)
    assert settings.repo_url(f"{settings.REPO_URL}/releases/tag/v0.3.7-beta")
    assert not settings.repo_url("https://github.com/enkeym/tunnel-vpn.evil.com")
    assert not settings.repo_url("file:///C:/Windows/System32/cmd.exe")
    # Кавычка и запятая разобрались бы explorer.exe как его ключи.
    assert not settings.repo_url(f'{settings.REPO_URL}/" /select,C:\\Windows\\System32\\cmd.exe')
    assert not settings.repo_url(f"{settings.REPO_URL}/releases tag")
    assert not settings.repo_url(None)


def test_диагностика_без_адресов():
    st = {"up": True, "version": "0.3.7-beta", "singbox": "1.14.0", "exit_ip": "185.12.4.9",
          "v6_leak": "2a00::71", "exit_state": "tunnel", "log_level": "info",
          # Ответ проверки основного — тот же адрес выхода (probe._check_result).
          "tunnels": [{"id": "home", "name": "Личный", "mode": "all", "active": "nl-1",
                       "check": "up", "answer": "185.12.4.9"},
                      {"id": "work", "name": "Работа", "mode": "list", "active": "corp",
                       "check": "up", "answer": "10.1.1.5"}]}
    text = settings.diagnostics(st, {"portable": False, "tray_autostart": True,
                                     "service": {"installed": True, "running": True, "start": "auto"}})

    assert "185.12.4.9" not in text and "2a00::71" not in text
    assert "Личный [home]" in text and "отвечает (адрес скрыт)" in text
    assert "отвечает 10.1.1.5" in text and "sing-box : 1.14.0" in text
    assert "работает, запуск auto" in text and "трей при входе: да" in text


def test_диагностика_без_службы():
    text = settings.diagnostics({}, {"portable": True, "tray_autostart": None, "service": None})
    assert "служба не отвечает" in text and "(портативная)" in text and "трей при входе" not in text


# ---------------------------------------------------------------- мост

def _api(monkeypatch):
    api = window.Api({})
    monkeypatch.setattr(api, "js", lambda fn, arg=None: None)
    return api


def test_трей_при_входе_выключается_с_правами(monkeypatch):
    state, runs = iter([True, False]), []
    monkeypatch.setattr(settings, "tray_task", lambda: next(state))
    monkeypatch.setattr(window, "_is_admin", lambda: False)
    monkeypatch.setattr(window, "_run_elevated",
                        lambda exe, args, show=False: runs.append((exe, args)))

    r = _api(monkeypatch).send("set_tray_autostart", False)

    assert r == {"ok": True, "tray_autostart": False}
    exe, args = runs[0]
    assert exe.lower().endswith("powershell.exe")
    assert "$false" in _script(args[args.index("-EncodedCommand") + 1])


def test_отказ_в_uac_возвращает_прежнее_состояние(monkeypatch):
    monkeypatch.setattr(settings, "tray_task", lambda: True)
    monkeypatch.setattr(window, "_is_admin", lambda: False)
    monkeypatch.setattr(window, "_run_elevated", lambda exe, args, show=False: None)

    r = _api(monkeypatch).send("set_tray_autostart", False)

    assert r["ok"] is False and r["tray_autostart"] is True
    assert "запрос прав отменён" in r["error"]


def test_выключать_нечего_без_задачи(monkeypatch):
    monkeypatch.setattr(settings, "tray_task", lambda: None)
    monkeypatch.setattr(window, "_run_elevated", lambda *a, **k: (_ for _ in ()).throw(AssertionError))

    assert _api(monkeypatch).send("set_tray_autostart", False) == {"ok": True, "tray_autostart": False}


def test_портативной_нечего_показывать_в_запуске(monkeypatch):
    monkeypatch.setattr(window.portable, "is_portable", lambda: True)
    monkeypatch.setattr(settings, "tray_task", lambda: (_ for _ in ()).throw(AssertionError))

    r = _api(monkeypatch).send("settings_info")

    assert r["ok"] and r["portable"] and r["service"] is None and r["tray_autostart"] is None
    assert r["repo"] == settings.REPO_URL and r["logs_dir"] == paths.LOGS


def test_установленная_без_задачи_трей_выключен(monkeypatch):
    monkeypatch.setattr(window.portable, "is_portable", lambda: False)
    monkeypatch.setattr(settings, "tray_task", lambda: None)
    monkeypatch.setattr(settings, "service_state",
                        lambda: {"installed": True, "running": True, "start": "auto"})

    r = _api(monkeypatch).send("settings_info")

    assert r["tray_autostart"] is False and r["service"]["start"] == "auto"


def test_чужая_ссылка_не_открывается(monkeypatch):
    opened = []
    monkeypatch.setattr(window.subprocess, "Popen", lambda args: opened.append(args))

    r = _api(monkeypatch).send("open_url", "https://example.com/")

    assert r["ok"] is False and opened == []


def test_ссылка_на_релиз_открывается_через_оболочку(monkeypatch):
    opened = []
    monkeypatch.setattr(window.subprocess, "Popen", lambda args: opened.append(args))
    url = f"{settings.REPO_URL}/releases/tag/v0.3.7-beta"

    assert _api(monkeypatch).send("open_url", url) == {"ok": True}
    assert opened[0][0].lower().endswith("explorer.exe") and opened[0][1] == url


def test_проверка_обновлений_идёт_ответом(monkeypatch):
    monkeypatch.setattr(settings, "check_update",
                        lambda cur: {"state": "latest", "latest": cur, "url": settings.REPO_URL})
    monkeypatch.setattr(paths, "version", lambda: "0.3.7-beta")

    monkeypatch.setattr(window.update, "can_install", lambda: (_ for _ in ()).throw(AssertionError))

    assert _api(monkeypatch).send("check_update") == {
        "ok": True, "state": "latest", "latest": "0.3.7-beta", "url": settings.REPO_URL,
        "install": False, "downtime": window.update.DOWNTIME}


# ------------------------------------------------------- обновление из окна

def _offer(monkeypatch, admin=True, signed=True):
    monkeypatch.setattr(settings, "check_update",
                        lambda cur: {"state": "update", "latest": "0.3.8", "url": settings.REPO_URL})
    monkeypatch.setattr(window, "_is_admin", lambda: admin)
    monkeypatch.setattr(window.update, "can_install", lambda: signed)
    calls = []
    api = window.Api({})
    monkeypatch.setattr(api, "js", lambda fn, arg=None: calls.append((fn, arg)))
    return api, calls, api.send("check_update")


def _download(api):
    assert api.send("update_download") == {"ok": True}
    api._upd_thread.join(5)


def test_без_прав_обновление_только_ссылкой(monkeypatch):
    api, _calls, r = _offer(monkeypatch, admin=False)

    assert r["install"] is False
    assert api.send("update_download")["ok"] is False


def test_неподписанная_сборка_не_качает(monkeypatch):
    api, _calls, r = _offer(monkeypatch, signed=False)

    assert r["install"] is False
    assert api.send("update_download")["ok"] is False


def test_скачанное_и_проверенное_ставится(tmp_path, monkeypatch):
    setup = tmp_path / "TunnelVPN-0.3.8-setup.exe"
    setup.write_bytes(b"MZ")

    def prepare(version, progress, cancel, stage):
        progress(1, 2)
        progress(2, 2)
        stage("verify")
        return str(setup)

    launched = []
    monkeypatch.setattr(window.update, "prepare", prepare)
    monkeypatch.setattr(window.update, "launch", lambda path, version: launched.append((path, version)))
    api, calls, r = _offer(monkeypatch)
    assert r["install"] is True

    _download(api)

    names = [fn for fn, _ in calls]
    assert ("updateProgress", {"got": 2, "total": 2}) in calls
    assert names.index("updateStage") < names.index("updateReady")
    assert calls[-1] == ("updateReady", {"version": "0.3.8"})
    assert api.send("update_install") == {"ok": True}
    assert launched == [(str(setup), "0.3.8")]


def test_файл_не_прошёл_проверку(monkeypatch):
    def prepare(version, progress, cancel, stage):
        raise window.update.UpdateError("подпись установщика: NotSigned", "verify")

    monkeypatch.setattr(window.update, "prepare", prepare)
    monkeypatch.setattr(window.update, "launch", lambda *a: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(window.Api, "_log", lambda self, line: None)
    api, calls, _r = _offer(monkeypatch)

    _download(api)

    assert calls[-1] == ("updateFailed", {"stage": "verify", "text": "подпись установщика: NotSigned"})
    assert api.send("update_install")["ok"] is False


def test_сбой_скачивания_без_исключения_в_тосте(monkeypatch):
    def prepare(version, progress, cancel, stage):
        raise window.update.UpdateError("не скачался x.exe: <urlopen error timed out>", "download")

    logged = []
    monkeypatch.setattr(window.update, "prepare", prepare)
    monkeypatch.setattr(window.Api, "_log", lambda self, line: logged.append(line))
    api, calls, _r = _offer(monkeypatch)

    _download(api)

    assert calls[-1] == ("updateFailed", {"stage": "download", "text": window.DOWNLOAD_FAILED})
    assert "urlopen error timed out" in logged[-1]


def test_установщик_не_запустился_без_исключения_в_тосте(tmp_path, monkeypatch):
    setup = tmp_path / "TunnelVPN-0.3.8-setup.exe"
    setup.write_bytes(b"MZ")

    def launch(path, version):
        raise window.update.UpdateError("установщик не запустился: [WinError 5]", "launch")

    logged = []
    monkeypatch.setattr(window.update, "prepare", lambda version, progress, cancel, stage: str(setup))
    monkeypatch.setattr(window.update, "launch", launch)
    monkeypatch.setattr(window.Api, "_log", lambda self, line: logged.append(line))
    api, _calls, _r = _offer(monkeypatch)
    _download(api)

    r = api.send("update_install")

    assert r["ok"] is False and "WinError" not in r["error"]
    assert "WinError 5" in logged[-1]


def test_отмена_скачивания(monkeypatch):
    def prepare(version, progress, cancel, stage):
        assert cancel.wait(5)
        raise window.update.Cancelled()

    monkeypatch.setattr(window.update, "prepare", prepare)
    api, calls, _r = _offer(monkeypatch)

    assert api.send("update_download") == {"ok": True}
    api.send("update_cancel")
    api._upd_thread.join(5)

    assert calls[-1] == ("updateFailed", {"stage": "cancel", "text": ""})


def test_итог_не_стирается_пока_setup_работает(monkeypatch):
    cleared = []
    monkeypatch.setattr(window.update, "clear", lambda: cleared.append(True))
    monkeypatch.setattr(window.update, "pending", lambda: {"version": "0.3.8", "state": "installing"})

    _api(monkeypatch).send("update_seen")
    assert cleared == []

    monkeypatch.setattr(window.update, "pending", lambda: {"version": "0.3.8", "state": "tunnel"})
    _api(monkeypatch).send("update_seen")
    assert cleared == [True]


def test_диагностика_без_службы_через_мост(monkeypatch):
    def no_pipe(op, **_kw):
        raise ipc.NotRunning("служба не отвечает")

    monkeypatch.setattr(ipc, "call", no_pipe)
    monkeypatch.setattr(window.portable, "is_portable", lambda: True)

    r = _api(monkeypatch).send("diagnostics")

    assert r["ok"] and "служба не отвечает" in r["text"]
