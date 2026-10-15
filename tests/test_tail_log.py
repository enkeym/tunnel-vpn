"""Хвост журнала sing-box: окно просит его раз в две секунды.

Журнал за долгую сессию растёт до мегабайт, поэтому важно не только что
вернулось, но и сколько прочитано с диска.
"""

import datetime
import os
import types

import pytest

from tunnelvpn import paths, service, tunnels
from tunnelvpn.service import Core


@pytest.fixture
def core(monkeypatch, tmp_path):
    """Core без службы и без туннелей: читается только журнал vpn, журнала
    службы нет."""
    core = Core.__new__(Core)
    core.tunnel = types.SimpleNamespace(sides=[])
    monkeypatch.setattr(Core, "_tunnels", staticmethod(tunnels.empty))
    monkeypatch.setattr(service, "SERVICE_LOG", str(tmp_path / "service.log"))
    return core


def _log(name, lines):
    path = os.path.join(paths.LOGS, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(f"{line}\n" for line in lines)
    return path


@pytest.fixture
def big_log():
    path = os.path.join(paths.LOGS, "vpn-2099-12-31_235959.log")
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        for i in range(80000):
            fh.write(f"+0300 2026-10-04 12:00:00 INFO [{i}] строка номер {i:06d}\n")
    try:
        assert os.path.getsize(path) > 5 * 1024 * 1024
        yield path
    finally:
        os.remove(path)


def test_возвращает_последние_строки_большого_журнала(core, big_log):
    lines = core._tail_log(400)
    assert len(lines) == 400
    assert lines[0].endswith("строка номер 079600")
    assert lines[-1].endswith("строка номер 079999")


def test_не_читает_журнал_целиком(core, big_log, monkeypatch):
    read = []
    real_open = open

    class Spy:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()

        def seek(self, *a):
            return self.fh.seek(*a)

        def read(self, *a):
            data = self.fh.read(*a)
            read.append(len(data))
            return data

    monkeypatch.setattr(service, "open",
                        lambda *a, **kw: Spy(real_open(*a, **kw)),
                        raising=False)
    core._tail_log(400)
    assert sum(read) <= service.LOG_TAIL_BYTES


def test_короткий_журнал_отдаётся_с_первой_строки(core):
    path = _log("vpn-2099-12-31_235958.log", ["первая", "вторая"])
    try:
        assert core._tail_log(400) == ["первая", "вторая"]
    finally:
        os.remove(path)


def test_журналы_туннеля_не_путаются_с_соседом_по_префиксу():
    made = [_log(n, ["x"]) for n in (
        "tunnel-work-2026-10-09_120000.log",
        "tunnel-work-2-2026-10-09_130000.log",
        "tunnel-work-2026-10-09_110000.log",
        "tunnel-work-старый.log")]
    try:
        assert [os.path.basename(p) for p in paths.log_files("tunnel-work")] == [
            "tunnel-work-2026-10-09_110000.log", "tunnel-work-2026-10-09_120000.log"]
        assert [os.path.basename(p) for p in paths.log_files("tunnel-work-2")] == [
            "tunnel-work-2-2026-10-09_130000.log"]
    finally:
        for p in made:
            os.remove(p)


def test_до_первого_включения_туннели_берутся_из_tunnels_json(core, monkeypatch):
    data = tunnels.validate({"tunnels": [
        {"id": "work", "name": "Работа", "mode": "list"}]})
    monkeypatch.setattr(Core, "_tunnels", staticmethod(lambda: data))
    path = _log("tunnel-work-2099-12-31_235959.log",
                ["+0300 2026-10-04 12:00:00 INFO из туннеля"])
    try:
        assert ("[Работа] +0300 2026-10-04 12:00:00 INFO из туннеля"
                in core._tail_log(10))
    finally:
        os.remove(path)


def test_журнал_проверки_конфига_идёт_с_меткой_туннеля(core, monkeypatch):
    """«Сервер не отвечает» в проверке — причина в журнале временного sing-box."""
    data = tunnels.validate({"tunnels": [
        {"id": "work", "name": "Работа", "mode": "list"}]})
    monkeypatch.setattr(Core, "_tunnels", staticmethod(lambda: data))
    path = _log("trial-work-2099-12-31_235959.log",
                ["+0300 2026-10-04 12:00:00 ERROR handshake"])
    try:
        assert ("[Работа] +0300 2026-10-04 12:00:00 ERROR handshake"
                in core._tail_log(10))
    finally:
        os.remove(path)


def test_строки_службы_идут_в_окно_с_меткой_и_уровнем(core, monkeypatch, tmp_path):
    """Без них фильтры окна по туннелю и «Предупреждения» видели один sing-box:
    какой конфиг, адрес пира и итог проверки лежали только в service.log."""
    data = tunnels.validate({"tunnels": [
        {"id": "t1", "name": "Офис", "mode": "list"},
        {"id": "t2", "name": "Дом", "mode": "all"}]})
    monkeypatch.setattr(Core, "_tunnels", staticmethod(lambda: data))
    monkeypatch.setattr(paths, "LOGS", str(tmp_path))
    (tmp_path / "service.log").write_text("\n".join([
        "14:00:00.000 → строка старого вида, без даты",
        "2026-10-04 12:00:01.000 → проверка a.conf («Офис»): up",
        "2026-10-04 12:00:02.000 !! маршрут к пиру 10.0.0.1 пропал",
        "2026-10-04 12:00:02.001    вручную: Remove-NetRoute",
        "2026-10-04 12:00:03.000 → собираю конфиг",
        "2026-10-04 12:00:03.001   [wg-t1] vpn.example → 10.0.0.1 (системный DNS)",
        "2026-10-04 12:00:04.000 → процессы «Дом» и «Офис»",
    ]) + "\n", encoding="utf-8")
    _log("vpn-2099-12-31_235959.log", ["+0300 2026-10-04 12:00:02 INFO sing-box"])

    lines = core._tail_log(10)

    tz = datetime.datetime.now().astimezone().strftime("%z")
    assert lines == [
        f"[Офис] {tz} 2026-10-04 12:00:01 INFO [служба] проверка a.conf («Офис»): up",
        "+0300 2026-10-04 12:00:02 INFO sing-box",
        f"{tz} 2026-10-04 12:00:02 WARN [служба] маршрут к пиру 10.0.0.1 пропал",
        f"{tz} 2026-10-04 12:00:02 WARN [служба] вручную: Remove-NetRoute",
        f"{tz} 2026-10-04 12:00:03 INFO [служба] собираю конфиг",
        f"[Офис] {tz} 2026-10-04 12:00:03 INFO [служба] [wg-t1] vpn.example → 10.0.0.1 (системный DNS)",
        f"{tz} 2026-10-04 12:00:04 INFO [служба] процессы «Дом» и «Офис»",
    ]
