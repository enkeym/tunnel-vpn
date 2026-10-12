"""Обновление из окна: скачивание, проверка, тихий запуск и разбор кодов setup."""

import base64
import hashlib
import io
import os
import re
import threading

import pytest

from tunnelvpn import update

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = update.SETUP_NAME.format(version="0.3.8")
BODY = b"MZ" + b"\0" * 1000


class _Resp(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def _opener(files):
    def opener(url):
        name = url.rsplit("/", 1)[1]
        if name not in files:
            raise OSError(f"404 {name}")
        return _Resp(files[name])
    return opener


def _sums(body=BODY, name=SETUP):
    return f"{hashlib.sha256(body).hexdigest()}  {name}\n".encode()


def _signed(subject="CN=Enkeym", status="Valid"):
    return lambda files: [{"status": "Valid", "subject": "CN=Enkeym"},
                          {"status": status, "subject": subject}][:len(files)]


@pytest.fixture(autouse=True)
def _empty_dir():
    update.clear()
    yield
    update.clear()


def _prepare(files, signers_fn=None, cancel=None):
    return update.prepare("0.3.8", lambda got, total: None, cancel or threading.Event(),
                          opener=_opener(files), signers_fn=signers_fn or _signed(),
                          own="tunnelvpn.exe")


def test_сумма_из_sha256sums():
    text = f"{'a' * 64}  TunnelVPN-Portable.exe\n{'B' * 64} *{SETUP}\n"
    assert update.expected_sum(text, SETUP) == "b" * 64
    assert update.expected_sum(text, "other.exe") is None


def test_проверенный_установщик_остаётся():
    path = _prepare({update.SUMS_NAME: _sums(), SETUP: BODY})
    assert path == os.path.join(update.DIR, SETUP)
    with open(path, "rb") as fh:
        assert fh.read() == BODY


def test_адреса_из_тега():
    assert update.asset_url("0.3.8", SETUP) == \
        f"https://github.com/enkeym/tunnel-vpn/releases/download/v0.3.8/{SETUP}"
    assert not update.valid_version("0.3.8/../../evil")
    with pytest.raises(update.UpdateError):
        update.prepare("../x", lambda *a: None, threading.Event())


@pytest.mark.parametrize("files, signers_fn", [
    ({update.SUMS_NAME: _sums(b"other"), SETUP: BODY}, None),
    ({update.SUMS_NAME: _sums(name="TunnelVPN-0.3.7-setup.exe"), SETUP: BODY}, None),
    ({update.SUMS_NAME: _sums(), SETUP: BODY}, _signed(status="NotSigned", subject="")),
    ({update.SUMS_NAME: _sums(), SETUP: BODY}, _signed(subject="CN=Someone Else")),
    ({update.SUMS_NAME: _sums(), SETUP: BODY}, lambda files: None),
], ids=["сумма", "нет-в-списке", "без-подписи", "чужой-издатель", "powershell-молчит"])
def test_не_прошедший_проверку_удалён(files, signers_fn):
    with pytest.raises(update.UpdateError) as err:
        _prepare(files, signers_fn)
    assert err.value.stage == "verify"
    assert os.listdir(update.DIR) == []


def test_своя_сборка_без_подписи_не_ставит():
    def unsigned_own(files):
        return [{"status": "NotSigned", "subject": ""}, {"status": "Valid", "subject": "CN=Enkeym"}]
    with pytest.raises(update.UpdateError):
        _prepare({update.SUMS_NAME: _sums(), SETUP: BODY}, unsigned_own)
    assert not update.can_install()            # из исходников — только ссылка


def test_отмена_и_обрыв_не_оставляют_файлов():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(update.Cancelled):
        _prepare({update.SUMS_NAME: _sums(), SETUP: BODY}, cancel=cancel)
    assert os.listdir(update.DIR) == []

    def short(url):
        return _Resp(BODY, length=len(BODY) + 10) if url.endswith(SETUP) else _Resp(_sums())
    with pytest.raises(update.UpdateError) as err:
        update.download("0.3.8", lambda *a: None, threading.Event(), short)
    assert err.value.stage == "download" and os.listdir(update.DIR) == []


def test_прогресс_по_кускам(monkeypatch):
    monkeypatch.setattr(update, "CHUNK", 400)
    seen = []
    update.download("0.3.8", lambda got, total: seen.append((got, total)), threading.Event(),
                    _opener({SETUP: BODY}))
    assert seen == [(400, 1002), (800, 1002), (1002, 1002)]


def _decode(cmd):
    return base64.b64decode(cmd[cmd.index("-EncodedCommand") + 1]).decode("utf-16-le")


def test_setup_ждёт_powershell_вне_дерева_трея():
    calls = []
    setup = os.path.join(update.DIR, SETUP)
    update.launch(setup, "0.3.8", popen=lambda cmd, **kw: calls.append(cmd), now=lambda: 1000.0)

    outer = _decode(calls[0])
    # Первый PowerShell только запускает второй и выходит: setup снимает
    # дерево трея, и ждущий в нём погиб бы вместе с окном.
    assert outer.startswith("Start-Process ") and "-Wait" not in outer
    inner = base64.b64decode(re.search(r"'-EncodedCommand','([^']+)'", outer).group(1)).decode("utf-16-le")
    assert "'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'" in inner
    assert "/SILENT'" not in inner and "-Wait" not in inner and "WaitForExit()" in inner
    assert f"Set-Content -LiteralPath '{update.CODE_FILE}' -Value $code" in inner
    assert "Get-Process -Name 'TunnelVPN-Tray'" in inner and "'--background'" in inner
    assert update.pending(now=lambda: 1001.0) == {"version": "0.3.8", "from": update.paths.version(),
                                                  "code": None, "state": "installing"}


def test_кавычка_в_пути_не_рвёт_строку():
    s = update.waiter_script(r"C:\Data\O'Brien\setup.exe", r"C:\Program Files\TunnelVPN\TunnelVPN-Tray.exe")
    assert r"$setup = 'C:\Data\O''Brien\setup.exe'" in s


def _finished(code, at=1000.0):
    update._write(update.MARK_FILE, f'{{"version": "0.3.8", "from": "0.3.7", "at": {at}}}')
    if code is not None:
        update._write(update.CODE_FILE, f"{code}\r\n")
    return update.pending(now=lambda: 1010.0)


@pytest.mark.parametrize("code, state", [
    (0, "updated"), (12, "updated"), (11, "tunnel"), (10, "error"), (7, "error"),
    (8, "error"), (5, "error"), (-1, "error"),
])
def test_итог_по_коду_выхода(code, state):
    p = _finished(code)
    assert (p["code"], p["state"]) == (code, state)
    assert ("text" in p) == (state == "error")


def test_метка_без_кода_устаревает():
    assert _finished(None)["state"] == "installing"
    p = _finished(None, at=1010.0 - update.STALE - 1)
    assert p["state"] == "error" and p["code"] is None


def test_без_метки_итога_нет():
    assert update.pending() is None
    update._write(update.CODE_FILE, "0")
    assert update.pending() is None


def test_коды_совпадают_с_установщиком():
    with open(os.path.join(ROOT, "installer", "tunnelvpn.iss"), encoding="utf-8-sig") as fh:
        iss = fh.read()
    body = re.search(r"function GetCustomSetupExitCode: Integer;(.*?)\nend;", iss, re.S).group(1)
    pairs = re.findall(r"(ServiceError|TunnelLost|WebView2Missing)\b.*?Result := (\d+)", body, re.S)
    assert pairs == [("ServiceError", str(update.EXIT_SERVICE)),
                     ("TunnelLost", str(update.EXIT_TUNNEL)),
                     ("WebView2Missing", str(update.EXIT_WEBVIEW2))]
    assert "NeedsRestart := True" in iss                     # 8 — отказ с перезагрузкой
    assert f"OutputBaseFilename={{#MyName}}-{{#MyVersion}}-setup" in iss
