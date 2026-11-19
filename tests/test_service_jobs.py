import queue
import threading

import pytest

from tunnelvpn.service import JOBS_KEPT, Core


def _core():
    core = Core.__new__(Core)
    core.logged = []
    core.log = core.logged.append
    core._jobs = {}
    core._jobs_lock = threading.Lock()
    core._jobs_done = {}
    core._job_seq = 0
    core._job_queue = queue.Queue()
    core._job_worker = None
    core._conf_checks = {}
    core._tests_lock = threading.Lock()
    core._test_queues = {}
    core._test_busy = set()
    return core


def test_задача_отдаёт_итог_после_ожидания():
    core = _core()

    jid = core._job_start("add", 2, lambda j: core._job_finish(j, added=2, failed=1))
    job = core._job_wait(jid, timeout=10)

    assert job["state"] == "done" and job["added"] == 2 and job["failed"] == 1
    assert core._job_last == job


def test_сбой_задачи_виден_в_итоге():
    core = _core()

    jid = core._job_start("add", 1, lambda j: 1 / 0)
    job = core._job_wait(jid, timeout=10)

    assert job["state"] == "error" and "division" in job["error"]
    assert core.logged[-1].startswith("!! фоновая задача")


def test_реестр_хранит_последние_задачи():
    core = _core()

    for _ in range(JOBS_KEPT + 5):
        jid = core._job_start("add", 0, lambda j: core._job_finish(j))
        core._job_wait(jid, timeout=10)

    assert len(core._jobs) == JOBS_KEPT
    assert not core._jobs_done


def test_проверка_amnezia_идёт_общей_очередью(monkeypatch):
    from tunnelvpn import paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0
    answers = {"Amnezia Литва": ("up", ""),
               "Amnezia Германия": ("error", "сервер не отвечает")}
    calls = []

    def test_conf(tid, name, wait=True, swap=0.0):
        calls.append((tid, name, wait))
        result, answer = answers[name]
        core._conf_checks[(tid, name)] = {"result": result, "answer": answer,
                                          "checking": False}
        return {"ok": True}

    core._test_conf = test_conf
    removed = []
    core._remove_config = lambda name, tunnel="", drop_tunnel=False: (
        removed.append(name) or {"ok": True})
    monkeypatch.setattr(paths, "SINGBOX", __file__)

    jid = core._job_start("add", 2, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia Литва"), ("t1", "Amnezia Германия")]))
    job = core._job_wait(jid, timeout=10)

    assert calls == [("t1", "Amnezia Литва", False),
                     ("t1", "Amnezia Германия", False),
                     ("t1", "Amnezia Германия", False)]
    assert removed == ["Amnezia Германия"]
    assert job["state"] == "done" and job["added"] == 1 and job["failed"] == 1
    assert job["problems"] == [{"name": "Amnezia Германия",
                                "reason": "сервер не отвечает"}]


def test_проверка_amnezia_без_итога_конфиг_оставляет(monkeypatch):
    from tunnelvpn import paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0

    def test_conf(tid, name, wait=True, swap=0.0):
        core._conf_checks[(tid, name)] = {"result": "none", "answer": "",
                                          "checking": False}
        return {"ok": True}

    core._test_conf = test_conf
    removed = []
    core._remove_config = lambda name, tunnel="", drop_tunnel=False: (
        removed.append(name) or {"ok": True})
    monkeypatch.setattr(paths, "SINGBOX", __file__)

    jid = core._job_start("add", 1, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia Литва")]))
    job = core._job_wait(jid, timeout=10)

    assert job["added"] == 1 and job["failed"] == 0
    assert removed == []


def test_проверка_amnezia_общий_сбой_ничего_не_удаляет(monkeypatch):
    from tunnelvpn import paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0

    def test_conf(tid, name, wait=True, swap=0.0):
        core._conf_checks[(tid, name)] = {"result": "error",
                                          "answer": "сервер не отвечает",
                                          "checking": False}
        return {"ok": True}

    core._test_conf = test_conf
    removed = []
    core._remove_config = lambda name, tunnel="", drop_tunnel=False: (
        removed.append(name) or {"ok": True})
    monkeypatch.setattr(paths, "SINGBOX", __file__)

    jid = core._job_start("add", 2, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia Литва"), ("t1", "Amnezia Германия")]))
    job = core._job_wait(jid, timeout=10)

    assert removed == []
    assert job["state"] == "done" and job["failed"] == 2
    assert any("подозрение" in line for line in core.logged)


def test_проверка_amnezia_сбой_проверки_конфиг_оставляет(monkeypatch):
    from tunnelvpn import paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0
    calls = []

    def test_conf(tid, name, wait=True, swap=0.0):
        calls.append(name)
        core._conf_checks[(tid, name)] = {"result": "error",
                                          "answer": "проверка упала",
                                          "checking": False}
        return {"ok": True}

    core._test_conf = test_conf
    removed = []
    core._remove_config = lambda name, tunnel="", drop_tunnel=False: (
        removed.append(name) or {"ok": True})
    monkeypatch.setattr(paths, "SINGBOX", __file__)

    jid = core._job_start("add", 1, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia Литва")]))
    job = core._job_wait(jid, timeout=10)

    assert calls == ["Amnezia Литва"]
    assert removed == []
    assert job["added"] == 1 and job["failed"] == 1


def test_проверка_amnezia_незавершённая_проверка_конфиг_оставляет(monkeypatch):
    from tunnelvpn import paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0
    core._await_tests = lambda jid, keys: None

    def test_conf(tid, name, wait=True, swap=0.0):
        core._conf_checks[(tid, name)] = {"result": None, "answer": "",
                                          "checking": True, "queued": True}
        return {"ok": True}

    core._test_conf = test_conf
    removed = []
    core._remove_config = lambda name, tunnel="", drop_tunnel=False: (
        removed.append(name) or {"ok": True})
    monkeypatch.setattr(paths, "SINGBOX", __file__)

    jid = core._job_start("add", 1, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia Литва")]))
    job = core._job_wait(jid, timeout=10)

    assert removed == []
    assert job["added"] == 1 and job["failed"] == 0


def test_проверка_amnezia_обновляет_устаревший_конфиг(monkeypatch):
    from tunnelvpn import paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0
    answers = [("error", "сервер не отвечает"), ("error", "сервер не отвечает"),
               ("up", "")]

    def test_conf(tid, name, wait=True, swap=0.0):
        result, answer = answers.pop(0) if answers else ("up", "")
        core._conf_checks[(tid, name)] = {"result": result, "answer": answer,
                                          "checking": False}
        return {"ok": True}

    core._test_conf = test_conf
    refreshed = []
    core._refresh_amnezia = lambda tid, name, key, codes: (
        refreshed.append(name) or True)
    removed = []
    core._remove_config = lambda name, tunnel="", drop_tunnel=False: (
        removed.append(name) or {"ok": True})
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    jid = core._job_start("add", 1, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia UAE")], key, {"Amnezia UAE": "ae"}))
    job = core._job_wait(jid, timeout=10)

    assert refreshed == ["Amnezia UAE"]
    assert removed == []
    assert job["added"] == 1 and job["failed"] == 0


def test_проверка_amnezia_без_нового_конфига_удаляет(monkeypatch):
    from tunnelvpn import paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0
    answers = {"Amnezia Литва": ("up", ""),
               "Amnezia Германия": ("error", "сервер не отвечает"),
               "Amnezia Финляндия": ("error", "сервер не отвечает")}

    def test_conf(tid, name, wait=True, swap=0.0):
        result, answer = answers[name]
        core._conf_checks[(tid, name)] = {"result": result, "answer": answer,
                                          "checking": False}
        return {"ok": True}

    core._test_conf = test_conf
    core._refresh_amnezia = lambda tid, name, key, codes: False
    removed = []
    core._remove_config = lambda name, tunnel="", drop_tunnel=False: (
        removed.append(name) or {"ok": True})
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}
    codes = {"Amnezia Литва": "lt", "Amnezia Германия": "de",
             "Amnezia Финляндия": "fi"}

    jid = core._job_start("add", 3, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia Литва"), ("t1", "Amnezia Германия"),
            ("t1", "Amnezia Финляндия")], key, codes))
    job = core._job_wait(jid, timeout=10)

    assert removed == ["Amnezia Германия", "Amnezia Финляндия"]
    assert job["added"] == 1 and job["failed"] == 2


def test_импорт_amnezia_проверяет_страну_до_следующей_выдачи(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    core._swap_from = lambda tid, name: 0.0
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    order = []

    def fake_fetch(api, country, protocol, code, timeout):
        order.append(("fetch", code))
        return [{"name": f"Amnezia {code}", "conf": "x"}], {}

    def test_conf(tid, name, wait=True, swap=0.0):
        order.append(("test", name))
        core._conf_checks[(tid, name)] = {"result": "up", "answer": "300 мс",
                                          "checking": False}
        return {"ok": True}

    monkeypatch.setattr(gateway, "_fetch", fake_fetch)
    core._add_config = lambda name, text, kind="", tunnel="", place="": (
        order.append(("add", name)) or {"ok": True, "tunnel": "t1",
                                        "name": name, "place": ""})
    core._test_conf = test_conf
    core._refresh_amnezia = lambda *args: False
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    jid = core._job_start("add", 3, lambda j: core._import_amnezia(
        j, key, [("t1", "Amnezia Туркменистан")],
        {"Amnezia Туркменистан": "tm"}, ["de", "fr"]))
    job = core._job_wait(jid, timeout=10)

    assert order == [("test", "Amnezia Туркменистан"),
                     ("fetch", "de"), ("add", "Amnezia de"),
                     ("test", "Amnezia de"),
                     ("fetch", "fr"), ("add", "Amnezia fr"),
                     ("test", "Amnezia fr")]
    assert job["state"] == "done" and job["added"] == 3 and job["failed"] == 0


def test_обновление_amnezia_пишет_конфиг_один_раз(monkeypatch, tmp_path):
    from tunnelvpn import gateway, tunnels

    core = _core()
    path = tmp_path / "Amnezia UAE.conf"
    path.write_text("PrivateKey = старый\n", encoding="utf-8")
    monkeypatch.setattr(tunnels, "conf_path", lambda tid, name: str(path))
    monkeypatch.setattr(Core, "_write_conf", staticmethod(
        lambda tid, name, text: path.write_text(text, encoding="utf-8")))
    monkeypatch.setattr(gateway, "_fetch", lambda *a: (
        [{"name": "Amnezia UAE", "conf": "PrivateKey = новый\n"}], {}))
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    assert core._refresh_amnezia("t1", "Amnezia UAE", key,
                                 {"Amnezia UAE": "ae"}) is True
    assert path.read_text(encoding="utf-8") == "PrivateKey = новый\n"
    assert core._refresh_amnezia("t1", "Amnezia UAE", key,
                                 {"Amnezia UAE": "ae"}) is False
    assert core._refresh_amnezia("t1", "Amnezia UAE", key, {}) is False


def test_проверка_amnezia_без_singbox_ничего_не_удаляет(monkeypatch, tmp_path):
    from tunnelvpn import paths

    core = _core()
    core._test_conf = lambda *a, **kw: pytest.fail("проба без sing-box")
    monkeypatch.setattr(paths, "SINGBOX", str(tmp_path / "нет.exe"))

    jid = core._job_start("add", 1, lambda j: core._check_amnezia(
        j, [("t1", "Amnezia Литва")]))
    job = core._job_wait(jid, timeout=10)

    assert job["state"] == "error" and "sing-box" in job["error"]


def test_повторная_проверка_конфига_не_дублируется():
    import collections

    core = _core()
    core._swap_from = lambda tid, name: 0.0
    core._test_workers = {}
    core._test_queues = {"t1": collections.deque(
        [({"id": "t1", "name": "Amnezia Литва"}, "Amnezia Литва", 0.0)])}

    core._queue_test({"id": "t1", "name": "Amnezia Литва"}, "Amnezia Литва")
    assert len(core._test_queues["t1"]) == 1

    core._test_queues = {}
    core._test_busy = {("t1", "Amnezia Литва")}
    core._queue_test({"id": "t1", "name": "Amnezia Литва"}, "Amnezia Литва")
    assert not core._test_queues.get("t1")
