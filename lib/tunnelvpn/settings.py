"""Вкладка «Настройки»: то, что окно узнаёт о системе само, без службы, —
задача автозапуска трея, служба Windows, обновления, диагностика.

Права здесь не просят: задачу трея меняет window.Api с UAC на одно действие,
как правку конфигов, а этот модуль только собирает команду и читает итог.
"""

import base64
import html
import json
import os
import platform
import re
import ssl
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

from . import paths

# Задача автозапуска трея (tunnelvpn.iss: RegisterTrayTask). Ею же ярлык
# запускает трей без UAC (tray._run_task) — поэтому «не запускать при входе»
# выключает только триггер входа, а не задачу целиком.
TRAY_TASK = "TunnelVPN Tray"
TRAY_EXE = "TunnelVPN-Tray.exe"

REPO_URL = "https://github.com/enkeym/tunnel-vpn"
# Не /releases/latest: «последним» мог оказаться релиз с чужим тегом (в прежнем
# репозитории — macOS, mac-v0.1.x). Версии Windows — теги v<цифра>…, бета
# публикуется обычным релизом (prerelease: false), но черновики пропускаем.
RELEASES_API = "https://api.github.com/repos/enkeym/tunnel-vpn/releases?per_page=30"
# Запасной список, когда API не ответил: без лимита API (60 запросов в час на
# адрес — у общего выхода VPN или оператора его выбирают чужие запросы), без
# черновиков, последние 10 релизов.
RELEASES_FEED = f"{REPO_URL}/releases.atom"
FEED_MAX = 2 ** 20
_ATOM = "{http://www.w3.org/2005/Atom}"
RELEASE_TAG = re.compile(r"^v(\d+(?:\.\d+)*)(?:-[0-9A-Za-z.]+)?$")
# Хвост журнала окна в «Скопировать диагностику»: строк и сколько читать с конца.
WINDOW_TAIL = 30
WINDOW_TAIL_BYTES = 16384
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
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


# Останов службы убирает маршруты (SvcStop просит у диспетчера до 60 с),
# запуск — секунды. Зависшая служба не отпустит и свой останов: по истечении
# STOP_WAIT её процесс снимаем.
SERVICE_STOP_WAIT = 60.0
SERVICE_START_WAIT = 30.0
_ALREADY_RUNNING = 1056     # ERROR_SERVICE_ALREADY_RUNNING


def restart_service(stop_wait=SERVICE_STOP_WAIT, start_wait=SERVICE_START_WAIT):
    """Поднимает службу, которая не отвечает по каналу: остановленную
    запускает, работающую перезапускает, зависшую снимает и запускает.

    Раньше окно в этом случае звало перезагрузить компьютер: процесс службы
    жив, диспетчер служб считает её работающей и сам не перезапустит.
    Возвращает итог для журнала; не вышло — OSError или pywintypes.error.
    Нужны права администратора: у трея и окна они есть.
    """
    import pywintypes
    import win32service

    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        h = win32service.OpenService(
            scm, paths.SERVICE_NAME,
            win32service.SERVICE_QUERY_STATUS | win32service.SERVICE_START
            | win32service.SERVICE_STOP)
        try:
            how = "запущена"
            first = win32service.QueryServiceStatusEx(h)["CurrentState"]
            st = _wait_service(h, (win32service.SERVICE_STOPPED,
                                   win32service.SERVICE_RUNNING), 10)
            if (first == win32service.SERVICE_START_PENDING
                    and st["CurrentState"] == win32service.SERVICE_RUNNING):
                # Застали запуск (вход в систему): перезапуск только сорвал бы его.
                return "служба запускалась сама — дождался"
            if st["CurrentState"] != win32service.SERVICE_STOPPED:
                try:
                    win32service.ControlService(h, win32service.SERVICE_CONTROL_STOP)
                except pywintypes.error:
                    # Уже стоит или не принимает команду (висит в *_PENDING):
                    # первое покажет ожидание, второе снимем ниже.
                    pass
                st = _wait_service(h, (win32service.SERVICE_STOPPED,), stop_wait)
                how = "перезапущена"
                if st["CurrentState"] != win32service.SERVICE_STOPPED:
                    # taskkill, а не TerminateProcess: он сам включает
                    # SeDebugPrivilege, без неё процесс LocalSystem не открыть.
                    _run([system32("taskkill.exe"), "/PID", str(st["ProcessId"]), "/F"])
                    _wait_service(h, (win32service.SERVICE_STOPPED,), 10)
                    how = f"не остановилась за {stop_wait:.0f} с — процесс снят, запущена"
            try:
                win32service.StartService(h, None)
            except pywintypes.error as exc:
                # После снятого процесса диспетчер мог запустить её сам
                # (действие при сбое — перезапуск через 5 с).
                if exc.winerror != _ALREADY_RUNNING:
                    raise
            st = _wait_service(h, (win32service.SERVICE_RUNNING,), start_wait)
            if st["CurrentState"] != win32service.SERVICE_RUNNING:
                raise TimeoutError(f"служба не запустилась за {start_wait:.0f} с")
            return f"служба {how}"
        finally:
            win32service.CloseServiceHandle(h)
    finally:
        win32service.CloseServiceHandle(scm)


def _wait_service(h, states, timeout):
    """Статус службы (QueryServiceStatusEx), как только она в одном из states,
    или последний, если за timeout секунд не дошла."""
    import time

    import win32service

    deadline = time.monotonic() + timeout
    while True:
        st = win32service.QueryServiceStatusEx(h)
        if st["CurrentState"] in states or time.monotonic() >= deadline:
            return st
        time.sleep(0.25)


# ------------------------------------------------------------- обновления

def _version_key(text):
    m = re.match(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else ()


def tls_context():
    """Корни Windows и certifi. Хранилище Windows Python берёт как есть, а
    недостающий корень Windows докачивает только для CryptoAPI (PowerShell,
    Explorer): на машине, где корня GitHub (Sectigo Root E46 или USERTrust ECC)
    ещё не было, запрос падал с CERTIFICATE_VERIFY_FAILED. Хранилище Windows
    остаётся: в нём корень антивируса или прокси, подменяющего сертификаты.
    certifi нет (тесты в CI ставят только pytest) — одни корни Windows."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    return ctx


def _get(url, accept):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"TunnelVPN/{paths.version()} (+{REPO_URL})", "Accept": accept})
    return urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT, context=tls_context())


def _fetch_releases():
    """Релизы из API, не ответил — из releases.atom. Оба не ответили —
    OSError с причинами обоих: окно пишет её в журнал."""
    try:
        with _get(RELEASES_API, "application/vnd.github+json") as resp:
            return json.load(resp)
    except (OSError, ValueError) as exc:
        api = exc
    try:
        with _get(RELEASES_FEED, "application/atom+xml") as resp:
            return feed_releases(resp.read(FEED_MAX))
    except (OSError, ValueError) as exc:
        raise OSError(f"API: {api}; releases.atom: {exc}") from exc


def feed_releases(raw):
    """releases.atom — список как у API: tag_name — хвост <id>, body — HTML
    из <content> разметкой, которую понимает plain_notes."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"не разобрать: {exc}") from exc
    out = []
    for entry in root.iter(_ATOM + "entry"):
        tag = (entry.findtext(_ATOM + "id") or "").rsplit("/", 1)[-1]
        content = entry.find(_ATOM + "content")
        body = (_html_notes(content.text or "")
                if content is not None and content.get("type") == "html" else "")
        out.append({"tag_name": tag, "body": body})
    return out


def _html_notes(text):
    # Пустое тело релиза лента заменяет сообщением коммита тега: заголовок
    # длиннее ~70 символов GitHub режет «…» и уносит хвост «…» в новый абзац.
    text = re.sub(r"…</p>\s*<p>…", "", text)
    # Переводы строк между тегами — вёрстка GitHub, абзацы и пункты дают теги.
    text = re.sub(r">\s*\n\s*<", "><", text)
    text = re.sub(r"<li\b[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>|</(?:li|ul|ol|div)>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|h[1-6]|pre)>", "\n\n", text, flags=re.I)
    return html.unescape(re.sub(r"<[^>]*>", "", text))


def check_update(current, fetch=_fetch_releases):
    """Сравнивает версию с релизами Windows на GitHub. Ответ:
    {state: latest|update|error, latest, url, notes}; notes — «Что нового»
    простым текстом; у error — reason для журнала, не для страницы. Скачивает
    и ставит update.py; адреса и там, и здесь собираются из тега, не из ответа API."""
    try:
        releases = fetch()
    except (OSError, ValueError) as exc:
        return {"state": "error", "reason": str(exc) or type(exc).__name__}
    best = None
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict):
            continue
        m = RELEASE_TAG.match(str(r.get("tag_name") or ""))
        if not m or r.get("draft"):
            continue
        key = _version_key(m.group(1))
        if best is None or key > best[0]:
            best = (key, r["tag_name"], r.get("body"))
    if best is None:
        return {"state": "error", "reason": f"нет релизов Windows в ответе: {str(releases)[:200]}"}
    tag = best[1]
    newer = best[0] > _version_key(current)
    return {"state": "update" if newer else "latest", "latest": tag[1:],
            "url": f"{REPO_URL}/releases/tag/{tag}", "notes": plain_notes(best[2])}


NOTES_MAX = 4000


def plain_notes(md):
    """Текст релиза из Markdown — простым текстом для «Что нового»: без
    заголовочных #, выделений, ссылок-скобок и HTML-комментариев; пункты
    списка — «•». Окно кладёт его textContent, разметки в нём не бывает."""
    if not isinstance(md, str):
        return ""
    text = re.sub(r"<!--.*?-->", "", md.replace("\r\n", "\n"), flags=re.S)
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"(\*\*|__|`)", "", line)
        lines.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text if len(text) <= NOTES_MAX else text[:NOTES_MAX - 1].rstrip() + "…"


def repo_url(url):
    """Ссылку наружу окно открывает только на свой репозиторий."""
    return isinstance(url, str) and REPO_PAGE.fullmatch(url) is not None


# ------------------------------------------------------------- диагностика

def diagnostics(st, info):
    """Текст для багрепорта: вывод `tunnelvpn status`, версии и хвост журнала
    окна (_window_tail). Адреса выхода и утечки IPv6 скрыты — отчёт обычно
    уходит в публичный issue."""
    from . import cli

    if st:
        # Ответ проверки основного — тот же адрес выхода (probe._check_result).
        tunnels = [{**t, "answer": "(адрес скрыт)"} if t.get("mode") == "all" and t.get("answer")
                   else t for t in st.get("tunnels") or []]
        st = {**st, "exit_ip": "скрыт" if st.get("exit_ip") else "",
              "v6_leak": "адрес скрыт" if st.get("v6_leak") else "", "tunnels": tunnels}
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
    tail = _window_tail()
    if tail:
        lines += ["", "window.log, последние строки:"] + tail
    return "\n".join(lines)


def _window_tail():
    """Последние WINDOW_TAIL строк журнала окна без IPv4-адресов: там
    причины сбоев окна, которых статус службы не знает. Читаем только
    конец файла — журнал до мегабайта (window.WINDOW_LOG_MAX)."""
    try:
        with open(paths.WINDOW_LOG, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            start = max(0, fh.tell() - WINDOW_TAIL_BYTES)
            fh.seek(start)
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    if start:
        lines = lines[1:]                  # первая — обрезанная
    return [_IPV4.sub("x.x.x.x", line) for line in lines[-WINDOW_TAIL:]]
