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
