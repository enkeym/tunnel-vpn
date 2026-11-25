"""Журналы sing-box: свежий файл продолжаем, но не без конца.

Цикл падений не должен плодить файлы: иначе десять последних вытеснялись бы
за пару минут. А под трафиком sing-box пишет постоянно, файл всегда свежий —
12 октября все перезапуски сессии ушли в один vpn-*.log на 10 МБ.
"""

import os

import pytest

from tunnelvpn import paths, tunnel

PREFIX = "roll-test"


@pytest.fixture
def logs():
    yield
    for path in paths.log_files(PREFIX):
        os.remove(path)


def _old_log(size):
    """Файл прошлого запуска, только что дописанный: mtime — сейчас."""
    path = os.path.join(paths.LOGS, f"{PREFIX}-2000-01-01_000000.log")
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def _open(prefix=PREFIX):
    fh = tunnel.Tunnel.open_log(None, prefix)
    fh.close()
    return fh.name


def test_свежий_файл_под_потолком_продолжается(logs, monkeypatch):
    monkeypatch.setattr(tunnel, "LOG_ROLL_BYTES", 1000)
    old = _old_log(999)

    assert _open() == old
    assert paths.log_files(PREFIX) == [old]


def test_свежий_файл_сверх_потолка_сменяется_новым(logs, monkeypatch):
    monkeypatch.setattr(tunnel, "LOG_ROLL_BYTES", 1000)
    old = _old_log(1000)

    path = _open()

    assert path != old
    assert paths.log_files(PREFIX) == [old, path]
    with open(path, encoding="utf-8") as fh:
        assert "TunnelVPN ===" in fh.read()


def test_новый_файл_после_потолка_продолжается_циклом_падений(logs, monkeypatch):
    monkeypatch.setattr(tunnel, "LOG_ROLL_BYTES", 1000)
    _old_log(1000)

    first = _open()

    assert _open() == first
    assert len(paths.log_files(PREFIX)) == 2


def _block(path, text):
    with open(path, "ab") as fh:
        fh.write(text.encode("utf-8"))
        return fh.tell()


def test_причина_не_из_прошлого_запуска(tmp_path):
    path = str(tmp_path / "trial.log")
    _block(path, "+0300 2026-11-25 13:31:57 ERROR [1 66ms] "
                 "connection: open connection: EOF\n")
    start = _block(path, "\n=== 2026-11-25 13:32:22 TunnelVPN ===\n")

    assert tunnel._fatal_line(path, start) == "sing-box ничего не напечатал"


def test_своя_ошибка_без_номера_соединения(tmp_path):
    path = str(tmp_path / "trial.log")
    _block(path, "+0300 2026-11-25 13:31:57 ERROR [1 66ms] "
                 "connection: open connection: EOF\n")
    start = _block(path, "\n=== 2026-11-25 13:32:22 TunnelVPN ===\n")
    _block(path, "+0300 2026-11-25 13:32:38 ERROR [2 5ms] "
                 "connection: другой отказ\n")

    assert tunnel._fatal_line(path, start) == "connection: другой отказ"


def test_старые_сверх_keep_logs_удаляются(logs):
    for day in range(1, tunnel.KEEP_LOGS + 2):
        path = os.path.join(paths.LOGS, f"{PREFIX}-2000-01-{day:02d}_000000.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        os.utime(path, (0, 0))

    path = _open()

    files = paths.log_files(PREFIX)
    assert len(files) == tunnel.KEEP_LOGS
    assert files[-1] == path
    assert files[0].endswith("2000-01-03_000000.log")
