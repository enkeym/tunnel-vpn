"""Журнал службы: по штампам соседних строк меряют шаги включения."""

import re
import threading

from tunnelvpn import service
from tunnelvpn.service import Core


def test_штамп_с_миллисекундами(monkeypatch, tmp_path):
    log = tmp_path / "service.log"
    monkeypatch.setattr(service, "SERVICE_LOG", str(log))

    Core.log(None, "→ собираю конфиг")

    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} → собираю конфиг\n",
                        log.read_text(encoding="utf-8"))


def test_строки_из_разных_потоков_не_затирают_друг_друга(monkeypatch, tmp_path):
    """Процессы туннелей запускаются параллельно, и их строки в одну
    миллисекунду затирали друг друга: от строки оставался хвост «3.log»."""
    log = tmp_path / "service.log"
    monkeypatch.setattr(service, "SERVICE_LOG", str(log))
    start = threading.Barrier(8)

    def write(n):
        start.wait()
        for i in range(200):
            Core.log(None, f"→ поток {n} строка {i} " + "x" * 80)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8 * 200
    assert all(re.fullmatch(r"\S+ → поток \d строка \d+ x{80}", s) for s in lines)


def test_переросший_журнал_уходит_в_точку_один(monkeypatch, tmp_path):
    """Служба живёт неделями: без потолка service.log копился с установки."""
    log = tmp_path / "service.log"
    monkeypatch.setattr(service, "SERVICE_LOG", str(log))
    monkeypatch.setattr(service, "SERVICE_LOG_BYTES", 100)

    Core.log(None, "первая " + "x" * 40)
    Core.log(None, "вторая " + "x" * 40)
    Core.log(None, "третья")

    assert "первая" in (tmp_path / "service.log.1").read_text(encoding="utf-8")
    assert "вторая" in (tmp_path / "service.log.1").read_text(encoding="utf-8")
    assert re.fullmatch(r"\S+ третья\n", log.read_text(encoding="utf-8"))


def test_старый_точка_один_затирается_а_не_копится(monkeypatch, tmp_path):
    log = tmp_path / "service.log"
    monkeypatch.setattr(service, "SERVICE_LOG", str(log))
    monkeypatch.setattr(service, "SERVICE_LOG_BYTES", 50)

    Core.log(None, "старая " + "x" * 50)
    Core.log(None, "новая " + "x" * 50)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["service.log.1"]
    old = (tmp_path / "service.log.1").read_text(encoding="utf-8")
    assert "новая" in old and "старая" not in old


def test_под_потолком_журнал_не_трогается(monkeypatch, tmp_path):
    log = tmp_path / "service.log"
    monkeypatch.setattr(service, "SERVICE_LOG", str(log))
    monkeypatch.setattr(service, "SERVICE_LOG_BYTES", 10_000)

    for i in range(20):
        Core.log(None, f"строка {i}")

    assert not (tmp_path / "service.log.1").exists()
    assert len(log.read_text(encoding="utf-8").splitlines()) == 20
