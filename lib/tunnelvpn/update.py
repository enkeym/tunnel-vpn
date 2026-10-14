"""Обновление из окна: скачать установщик релиза, проверить и поставить тихо.

Путь общий для окна и трея (тихий запуск — docs/dev/install.md, README
«Установка» → «Тихо»):

  1. SHA256SUMS.txt и TunnelVPN-<версия>-setup.exe релиза — адреса из тега;
  2. сумма из SHA256SUMS.txt и подпись Authenticode того же издателя, что у
     работающего exe. Не сошлось — файл удаляется и не запускается. Своя
     сборка без подписи — установки из окна нет вовсе (can_install);
  3. setup /VERYSILENT; итог — по коду выхода (outcome).

Ждать setup окну нельзя: он снимает трей вместе с деревом процессов
(taskkill /T) и всё под именем tunnelvpn.exe. Ждёт PowerShell, запущенный
через другой PowerShell, который сразу выходит: дерево трея до него не
дотягивается. Код выхода он пишет в CODE_FILE и, если трея нет, запускает его.
MARK_FILE окно кладёт до запуска: по нему трей и окно показывают «Обновляю
TunnelVPN…», а трей после обновления открывает окно с итогом.

Каталог — state\\update\\: писать туда могут только SYSTEM и администраторы, а
окно установленной версии работает с правами трея. Проверенный файл обычный
пользователь до запуска не подменит.
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from . import paths, portable, settings

# Имена файлов релиза: OutputBaseFilename в tunnelvpn.iss и SHA256SUMS.txt
# из build.ps1.
SETUP_NAME = "TunnelVPN-{version}-setup.exe"
SUMS_NAME = "SHA256SUMS.txt"
TRAY_IMAGE = os.path.splitext(settings.TRAY_EXE)[0]

DIR = os.path.join(paths.STATE, "update")
MARK_FILE = os.path.join(DIR, "update.json")
CODE_FILE = os.path.join(DIR, "code")

MAX_SIZE = 300 * 2 ** 20
CHUNK = 256 * 1024
TIMEOUT = 30.0
# Сколько VPN лежит при обновлении: служба стоит от остановки до нового
# запуска, потом start туннеля. Оценка — замерить на хосте (docs/dev/install.md).
DOWNTIME = 30
# Метка без кода дольше этого — setup не дожил до конца (машину выключили).
STALE = 15 * 60

# Коды выхода setup: 10–12 — GetCustomSetupExitCode в tunnelvpn.iss (порядок
# там же: служба > туннель > WebView2), 7 и 8 — отказ PrepareToInstall, прочие
# ненулевые — сам Inno. Таблица — README, «Установка».
EXIT_OK = 0
EXIT_PREPARE = 7
EXIT_RESTART = 8
EXIT_SERVICE = 10
EXIT_TUNNEL = 11
EXIT_WEBVIEW2 = 12

_PS_ARGS = ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass")
_SETUP_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")


class UpdateError(Exception):
    """Шаг не прошёл. stage — download, verify или launch: после verify окно
    говорит «не прошёл проверку и удалён»."""

    def __init__(self, text, stage):
        super().__init__(text)
        self.stage = stage


class Cancelled(Exception):
    """Скачивание отменили."""


# --------------------------------------------------------------- скачивание

def valid_version(version):
    return isinstance(version, str) and settings.RELEASE_TAG.fullmatch("v" + version) is not None


def asset_url(version, name):
    return f"{settings.REPO_URL}/releases/download/v{version}/{name}"


def _open(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"TunnelVPN/{paths.version()} (+{settings.REPO_URL})"})
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=settings.tls_context())


def expected_sum(text, name):
    """Сумма файла name из SHA256SUMS.txt («<hex>  <имя>»); None — его там нет."""
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.strip())
        if m and m.group(2) == name:
            return m.group(1).lower()
    return None


def fetch_sum(version, opener=_open):
    name = SETUP_NAME.format(version=version)
    try:
        with opener(asset_url(version, SUMS_NAME)) as resp:
            text = resp.read(64 * 1024).decode("ascii", "replace")
    except (OSError, ValueError) as exc:
        raise UpdateError(f"не скачался {SUMS_NAME}: {exc}", "download") from exc
    found = expected_sum(text, name)
    if found is None:
        raise UpdateError(f"в {SUMS_NAME} нет {name}", "verify")
    return found


def download(version, progress, cancel, opener=_open):
    """Установщик релиза в DIR: (путь, sha256). progress(got, total) — на каждый
    кусок, total 0 — сервер размер не назвал. Сумма считается на лету;
    недокачанное (.part) удаляется при любом исходе, кроме успеха."""
    name = SETUP_NAME.format(version=version)
    os.makedirs(DIR, exist_ok=True)
    dest = os.path.join(DIR, name)
    part = dest + ".part"
    digest = hashlib.sha256()
    got = 0
    try:
        with opener(asset_url(version, name)) as resp, open(part, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            if total > MAX_SIZE:
                raise UpdateError(f"{name}: {total} байт — больше предела", "download")
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                got += len(chunk)
                if got > MAX_SIZE:
                    raise UpdateError(f"{name}: больше {MAX_SIZE} байт", "download")
                fh.write(chunk)
                digest.update(chunk)
                progress(got, total)
        if total and got != total:
            raise UpdateError(f"{name} скачался не целиком: {got} из {total} байт", "download")
        os.replace(part, dest)
    except (OSError, ValueError) as exc:
        _remove(part)
        raise UpdateError(f"не скачался {name}: {exc}", "download") from exc
    except BaseException:
        _remove(part)
        raise
    return dest, digest.hexdigest()


# ------------------------------------------------------------------ подпись

_SIGNERS_PS = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "ConvertTo-Json -Compress -InputObject @(foreach ($f in @({files})) {{ "
    "$s = Get-AuthenticodeSignature -LiteralPath $f; "
    "[pscustomobject]@{{status = [string]$s.Status; "
    "subject = [string]$s.SignerCertificate.Subject}} }})")


def _q(text):
    """Строка PowerShell в одинарных кавычках."""
    return "'" + str(text).replace("'", "''") + "'"


def _encode(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _powershell():
    return settings.system32("WindowsPowerShell", "v1.0", "powershell.exe")


def signers(files, run=settings._run):
    """Подписи Authenticode файлов в том же порядке: [{status, subject}].
    status Valid — подпись цела и цепочка доверенная. None — PowerShell не
    ответил или ответил не то."""
    script = _SIGNERS_PS.format(files=", ".join(_q(f) for f in files))
    r = run([_powershell(), *_PS_ARGS, "-EncodedCommand", _encode(script)], timeout=60)
    if r is None or r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout.decode("utf-8-sig", "replace"))
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) != len(files) \
            or not all(isinstance(d, dict) for d in data):
        return None
    return [{"status": str(d.get("status") or ""), "subject": str(d.get("subject") or "")}
            for d in data]


def check_signature(setup, own, signers_fn=signers):
    """Установщик подписан и тем же издателем, что работающий exe own.
    Сверяется субъект, а не отпечаток: продлённый сертификат — новый отпечаток."""
    got = signers_fn([own, setup])
    if got is None:
        raise UpdateError("подпись не проверить: PowerShell не ответил", "verify")
    mine, theirs = got
    if mine["status"] != "Valid":
        raise UpdateError("у установленной версии нет действительной подписи", "verify")
    if theirs["status"] != "Valid":
        raise UpdateError(f"подпись установщика: {theirs['status'] or 'нет'}", "verify")
    if theirs["subject"] != mine["subject"]:
        raise UpdateError(f"установщик подписан не тем издателем: {theirs['subject']}", "verify")


_own_signed = None


def can_install(signers_fn=signers):
    """Ставить ли обновление из окна: установленная сборка (не портативная и
    не из исходников), сама подписанная. Иначе — только ссылка на релиз:
    неподписанный установщик не с чем сверить. Права проверяет окно."""
    global _own_signed
    if not getattr(sys, "frozen", False) or portable.is_portable():
        return False
    if _own_signed is None:
        got = signers_fn([sys.executable])
        if got is None:
            return False               # PowerShell не ответил — спросим в следующий раз
        _own_signed = got[0]["status"] == "Valid"
    return _own_signed


def prepare(version, progress, cancel, stage=lambda name: None, opener=_open,
            signers_fn=signers, own=None):
    """Скачать и проверить установщик version; путь к проверенному файлу.
    stage("verify") — перед проверкой. Не прошёл — файл удалён, UpdateError."""
    if not valid_version(version):
        raise UpdateError(f"неверная версия: {version!r}", "download")
    clear()
    want = fetch_sum(version, opener)
    path, got = download(version, progress, cancel, opener)
    stage("verify")
    try:
        if got != want:
            raise UpdateError(f"сумма {got} не та, что в {SUMS_NAME}", "verify")
        check_signature(path, own or sys.executable, signers_fn)
    except BaseException:
        _remove(path)
        raise
    return path


# ------------------------------------------------------------------ запуск

def waiter_script(setup, tray):
    """Ждущий PowerShell: setup тихо, код — в CODE_FILE, установщик — удалить,
    трея нет — запустить его значком; окно с итогом трей откроет сам (pending).
    WaitForExit, а не -Wait: тот ждёт всё дерево, а setup в конце запускает
    трей. $p.Handle — до выхода: без него ExitCode бывает пустым."""
    args = ",".join(_q(a) for a in _SETUP_ARGS)
    return (f"$setup = {_q(setup)}; $code = -1; "
            f"try {{ $p = Start-Process -FilePath $setup -ArgumentList {args} -PassThru; "
            "$null = $p.Handle; $p.WaitForExit(); $code = $p.ExitCode } catch { } "
            f"Set-Content -LiteralPath {_q(CODE_FILE)} -Value $code -Encoding Ascii; "
            "Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue; "
            f"if (-not (Get-Process -Name {_q(TRAY_IMAGE)} -ErrorAction SilentlyContinue)) "
            f"{{ Start-Process -FilePath {_q(tray)} -ArgumentList '--background' }}")


def launch(setup, version, popen=subprocess.Popen, now=time.time):
    """Запускает проверенный setup тихо через ждущий PowerShell вне дерева
    трея. Окно и трей после этого закроет сам setup."""
    clear(keep=setup)
    _write(MARK_FILE, json.dumps({"version": version, "from": paths.version(), "at": now()}))
    inner = waiter_script(setup, os.path.join(paths.BASE, settings.TRAY_EXE))
    outer = (f"Start-Process -WindowStyle Hidden -FilePath {_q(_powershell())} -ArgumentList "
             + ",".join(_q(a) for a in (*_PS_ARGS, "-EncodedCommand", _encode(inner))))
    try:
        popen([_powershell(), *_PS_ARGS, "-EncodedCommand", _encode(outer)],
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        clear()
        raise UpdateError(f"установщик не запустился: {exc}", "launch") from exc


# ------------------------------------------------------------------- итог

def outcome(code):
    """Итог по коду выхода setup: state updated — обновлено; tunnel —
    обновлено, а VPN сам не включился; error — text для человека. 12 (нет
    WebView2) — обновлено: окно, которое это читает, WebView2 уже открыл."""
    if code in (EXIT_OK, EXIT_WEBVIEW2):
        return {"state": "updated"}
    if code == EXIT_TUNNEL:
        return {"state": "tunnel"}
    if code == EXIT_SERVICE:
        text = ("Обновление установлено, но служба TunnelVPN не запустилась. "
                "Подробности — в журнале установки: %TEMP%\\Setup Log …")
    elif code == EXIT_PREPARE:
        text = "Установка не началась: файлы и настройки не тронуты."
    elif code == EXIT_RESTART:
        text = ("Нужна перезагрузка: Windows ещё удаляет прежнюю службу. "
                "Перезагрузите компьютер и обновите снова.")
    elif code is None:
        text = "Обновление не завершилось."
    else:
        text = f"Установка не удалась (код {code})."
    return {"state": "error", "text": text}


def pending(now=time.time):
    """Обновление из окна, о котором ещё не рассказали: {version, from, code,
    state[, text]}; state installing — setup ещё работает. None — нет такого."""
    try:
        with open(MARK_FILE, encoding="utf-8") as fh:
            mark = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(mark, dict):
        return None
    info = {"version": str(mark.get("version") or ""), "from": str(mark.get("from") or "")}
    try:
        with open(CODE_FILE, encoding="ascii", errors="replace") as fh:
            code = int(fh.read().strip())
    except (OSError, ValueError):
        code = None
    if code is None:
        at = mark.get("at")
        if isinstance(at, (int, float)) and 0 <= now() - at < STALE:
            return {**info, "code": None, "state": "installing"}
    return {**info, "code": code, **outcome(code)}


def clear(keep=None):
    """Метка, код и прошлые файлы в DIR; keep — путь, который оставить."""
    try:
        names = os.listdir(DIR)
    except OSError:
        return
    for name in names:
        path = os.path.join(DIR, name)
        if path != keep:
            _remove(path)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
