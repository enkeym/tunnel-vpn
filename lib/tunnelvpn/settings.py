"""Вкладка «Настройки»: то, что окно узнаёт о системе само, без службы, —
задача автозапуска трея, служба Windows, обновления, диагностика.

Права здесь не просят: задачу трея меняет window.Api с UAC на одно действие,
как правку конфигов, а этот модуль только собирает команду и читает итог.
"""

import base64
import json
import os
import platform
import re
import subprocess
import urllib.request

from . import paths

# Задача автозапуска трея (tunnelvpn.iss: RegisterTrayTask). Ею же ярлык
# запускает трей без UAC (tray._run_task) — поэтому «не запускать при входе»
# выключает только триггер входа, а не задачу целиком.
TRAY_TASK = "TunnelVPN Tray"
TRAY_EXE = "TunnelVPN-Tray.exe"

REPO_URL = "https://github.com/sotakeiteasy/dual-vpn"
# Не /releases/latest: в том же репозитории релизы macOS (mac-v0.1.x), и
# «последним» мог оказаться он. Версии Windows — теги v<цифра>…, бета
# публикуется обычным релизом (prerelease: false), но черновики пропускаем.
RELEASES_API = "https://api.github.com/repos/sotakeiteasy/dual-vpn/releases?per_page=30"
RELEASE_TAG = re.compile(r"^v(\d+(?:\.\d+)*)(?:-[0-9A-Za-z.]+)?$")
# Ссылка уходит аргументом explorer.exe: кавычки, пробелы и запятые он
# разбирает по-своему (/select,…), поэтому после адреса репозитория — только путь.
REPO_PAGE = re.compile(re.escape(REPO_URL) + r"(?:/[A-Za-z0-9._/-]*)?")
UPDATE_TIMEOUT = 6.0

_START = {2: "auto", 3: "manual", 4: "disabled"}
_NO_SERVICE = 1060          # ERROR_SERVICE_DOES_NOT_EXIST


def system32(*parts):
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", *parts)


def _run(args, timeout=15):
    """Консольная утилита без окна; None — не запустилась или зависла."""
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------- трей при входе

def tray_task():
    """Встаёт ли трей при входе в систему: True/False; None — задачи нет
    (портативная версия, галочку сняли при установке) или её не прочесть."""
    r = _run([system32("schtasks.exe"), "/Query", "/TN", TRAY_TASK, "/XML"])
    if r is None or r.returncode != 0:
        return None
    return logon_enabled(r.stdout)


def logon_enabled(raw):
    """Разбор XML задачи: включён ли триггер входа и сама задача. XML, а не
    таблица /FO: в таблице состояние переведено («Готово», «Отключено»)."""
    text = (raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
            else raw.decode("utf-8", "replace"))

    def off(tag):
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", text, re.S)
        return bool(m and re.search(r"<Enabled>\s*false\s*</Enabled>", m.group(1)))

    if not re.search(r"<LogonTrigger\b", text):
        return False
    return not off("LogonTrigger") and not off("Settings")


def tray_task_command(on, exists, exe):
    """Команда PowerShell (для -EncodedCommand), которая включает или
    выключает трей при входе. Есть задача — меняется только её триггер; нет —
    ставится такая же, как у установщика (RegisterTrayTask в tunnelvpn.iss:
    правишь одну — правь и другую)."""
    name = TRAY_TASK.replace("'", "''")
    if exists:
        flag = "$true" if on else "$false"
        script = (f"$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction Stop; "
                  f"foreach ($g in $t.Triggers) {{ $g.Enabled = {flag} }}; "
                  + ("$t.Settings.Enabled = $true; " if on else "")
                  + "Set-ScheduledTask -InputObject $t | Out-Null")
    else:
        exe = exe.replace("'", "''")
        script = ("$u = [Security.Principal.WindowsIdentity]::GetCurrent().Name; "
                  f"$a = New-ScheduledTaskAction -Execute '{exe}' -Argument '--background'; "
                  "$t = New-ScheduledTaskTrigger -AtLogOn -User $u; "
                  "$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest; "
                  "$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) "
                  "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew; "
                  f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $t "
                  "-Principal $p -Settings $s -Force | Out-Null")
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


# ----------------------------------------------------------------- служба

def service_state():
    """{installed, running, start} службы для строки в «Запуске»: start —
    auto, delayed, manual, disabled. None — узнать нечем."""
    try:
        import pywintypes
        import win32service
    except ImportError:
        return None
    try:
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    except pywintypes.error:
        return None
    try:
        try:
            h = win32service.OpenService(scm, paths.SERVICE_NAME,
                                         win32service.SERVICE_QUERY_CONFIG
                                         | win32service.SERVICE_QUERY_STATUS)
        except pywintypes.error as exc:
            if exc.winerror == _NO_SERVICE:
                return {"installed": False, "running": False, "start": ""}
            return None
        try:
            start = _START.get(win32service.QueryServiceConfig(h)[1], "")
            running = (win32service.QueryServiceStatus(h)[1]
                       == win32service.SERVICE_RUNNING)
            if start == "auto":
                try:
                    if win32service.QueryServiceConfig2(
                            h, win32service.SERVICE_CONFIG_DELAYED_AUTO_START_INFO):
                        start = "delayed"
                except pywintypes.error:
                    pass
        finally:
            win32service.CloseServiceHandle(h)
    finally:
        win32service.CloseServiceHandle(scm)
    return {"installed": True, "running": running, "start": start}


# ------------------------------------------------------------- обновления

def _version_key(text):
    m = re.match(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else ()


def _fetch_releases():
    req = urllib.request.Request(RELEASES_API, headers={
        "User-Agent": f"TunnelVPN/{paths.version()} (+{REPO_URL})",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
        return json.load(resp)


def check_update(current, fetch=_fetch_releases):
    """Сравнивает версию с релизами Windows на GitHub. Ответ:
    {state: latest|update|error, latest, url}. Без скачивания: страницу
    релиза человек открывает сам. Адрес собирается из тега, не из ответа API."""
    try:
        releases = fetch()
    except (OSError, ValueError):
        return {"state": "error"}
    best = None
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict):
            continue
        m = RELEASE_TAG.match(str(r.get("tag_name") or ""))
        if not m or r.get("draft"):
            continue
        key = _version_key(m.group(1))
        if best is None or key > best[0]:
            best = (key, r["tag_name"])
    if best is None:
        return {"state": "error"}
    tag = best[1]
    newer = best[0] > _version_key(current)
    return {"state": "update" if newer else "latest", "latest": tag[1:],
            "url": f"{REPO_URL}/releases/tag/{tag}"}


def repo_url(url):
    """Ссылку наружу окно открывает только на свой репозиторий."""
    return isinstance(url, str) and REPO_PAGE.fullmatch(url) is not None


# ------------------------------------------------------------- диагностика

def diagnostics(st, info):
    """Текст для багрепорта: вывод `tunnelvpn status` и версии. Адреса выхода
    и утечки IPv6 скрыты — отчёт обычно уходит в публичный issue."""
    from . import cli

    if st:
        st = {**st, "exit_ip": "скрыт" if st.get("exit_ip") else "",
              "v6_leak": "адрес скрыт" if st.get("v6_leak") else ""}
        lines = cli.status_lines(st)
    else:
        lines = ["TunnelVPN: служба не отвечает"]
    svc = info.get("service")
    tray = info.get("tray_autostart")
    lines += [
        "",
        f"версия   : {paths.version()}{' (портативная)' if info.get('portable') else ''}",
        f"sing-box : {(st or {}).get('singbox') or '?'}",
        f"Windows  : {platform.platform()}",
        f"журнал   : {(st or {}).get('log_level') or '?'}",
        "служба   : " + ("нет" if not svc or not svc.get("installed") else
                         f"{'работает' if svc.get('running') else 'остановлена'}, "
                         f"запуск {svc.get('start') or '?'}"),
    ]
    if tray is not None:
        lines.append(f"трей при входе: {'да' if tray else 'нет'}")
    return "\n".join(lines)
