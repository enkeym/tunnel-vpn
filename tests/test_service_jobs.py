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
    core._amnezia_lock = threading.Lock()
    core._amnezia_jobs = {}
    core._amnezia_ask = {}
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


def test_импорт_amnezia_добавляет_один_конфиг_и_останавливается(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    order = []

    def fake_probe(api, country, protocol):
        order.append("probe")
        return {"profiles": [{"name": "Amnezia Туркменистан", "conf": "x"}],
                "countries": ["de", "fr"], "country": "tm"}

    def fake_fetch(api, country, protocol, code, timeout):
        order.append(("fetch", code))
        return [{"name": f"Amnezia {code}", "conf": "x"}], {}

    verdicts = iter([("error", "сервер не отвечает"), ("up", "300 мс")])
    core._amnezia_check = lambda profile: (
        order.append(("check", profile["name"])) or next(verdicts))
    added = []
    core._add_config = lambda name, text, kind="", tunnel="", place="", source_token="": (
        added.append(name) or {"ok": True, "tunnel": "t1", "name": name})
    monkeypatch.setattr(gateway, "probe", fake_probe)
    monkeypatch.setattr(gateway, "_fetch", fake_fetch)
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    jid = core._job_start("add", 1, lambda j: core._search_amnezia(j, key))
    job = core._job_wait(jid, timeout=10)

    assert order == ["probe", ("check", "Amnezia Туркменистан"),
                     ("fetch", "de"), ("check", "Amnezia de")]
    assert added == ["Amnezia de"]
    assert job["state"] == "done" and job["added"] == 1
    assert job["tunnel"] == "t1" and job["name"] == "Amnezia de"
    assert job["done"] == 2 and job["attempted"] == 2 and job["failed"] == 1


def test_импорт_amnezia_без_рабочих_конфигов_ничего_не_сохраняет(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    monkeypatch.setattr(gateway, "probe", lambda *a: {
        "profiles": [{"name": "Amnezia Туркменистан", "conf": "x"}],
        "countries": ["de"], "country": "tm"})
    monkeypatch.setattr(gateway, "_fetch", lambda *a: (
        [{"name": "Amnezia de", "conf": "x"}], {}))
    core._amnezia_check = lambda profile: ("error", "сервер не отвечает")
    core._add_config = lambda *a, **kw: pytest.fail("конфиг не должен добавиться")
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    jid = core._job_start("add", 1, lambda j: core._search_amnezia(j, key))
    job = core._job_wait(jid, timeout=10)

    assert job["state"] == "error" and job["added"] == 0
    assert job["done"] == 2 and job["failed"] == 2
    assert [p["name"] for p in job["problems"]] == ["Amnezia Туркменистан",
                                                    "Amnezia de"]


def test_локальный_сбой_останавливает_выдачу_следующей_страны(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    monkeypatch.setattr(gateway, "probe", lambda *a: {
        "profiles": [{"name": "Первый", "conf": "x"}],
        "countries": ["de"], "country": "ru"})
    monkeypatch.setattr(gateway, "_fetch", lambda *a: pytest.fail("лишняя выдача"))
    core._amnezia_check = lambda profile: ("local_error", "sing-box не запустился")
    core._add_config = lambda *a, **kw: pytest.fail("нечего сохранять")

    jid = core._job_start("add", 1, lambda j: core._search_amnezia(j, {
        "api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}))
    job = core._job_wait(jid, timeout=10)

    assert job["state"] == "error"
    assert job["done"] == 1 and job["error"] == "sing-box не запустился"


def test_ошибка_шлюза_при_следующей_стране_останавливает_поиск(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    monkeypatch.setattr(gateway, "probe", lambda *a: {
        "profiles": [{"name": "Первый", "conf": "x"}],
        "countries": ["de", "fr"], "country": "ru"})
    calls = []

    def fetch(*args):
        calls.append(args[3])
        raise ValueError("доступ запрещён")

    monkeypatch.setattr(gateway, "_fetch", fetch)
    core._amnezia_check = lambda profile: ("error", "сервер не отвечает")

    jid = core._job_start("add", 1, lambda j: core._search_amnezia(j, {
        "api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}))
    job = core._job_wait(jid, timeout=10)

    assert calls == ["de"]
    assert job["state"] == "error" and job["error"] == "доступ запрещён"


def test_импорт_amnezia_отвечает_до_сети(monkeypatch):
    from tunnelvpn import gateway

    core = _core()
    monkeypatch.setattr(gateway, "probe",
                        lambda *a: pytest.fail("сеть в момент ответа"))
    core._search_amnezia = lambda jid, key, tunnel="", place="", token="": (
        core._job_finish(jid))

    reply = core._add_amnezia({"api_key": "k", "user_country_code": "ru",
                               "service_protocol": "awg"}, "t1")

    assert reply == {"ok": True, "job": reply["job"], "checking": True,
                     "added": 0, "mode": "amnezia_single", "tunnel": "t1"}


def test_импорт_amnezia_без_singbox_сеть_не_трогает(monkeypatch, tmp_path):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", str(tmp_path / "нет.exe"))
    monkeypatch.setattr(gateway, "probe",
                        lambda *a: pytest.fail("проба без sing-box"))
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    jid = core._job_start("add", 1, lambda j: core._search_amnezia(j, key))
    job = core._job_wait(jid, timeout=10)

    assert job["state"] == "error" and "sing-box" in job["error"]


def test_повторный_запрос_того_же_ключа_не_удваивает_поиск(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    monkeypatch.setattr(gateway, "probe", lambda *a: {
        "profiles": [{"name": "Amnezia Литва", "conf": "x"}],
        "countries": [], "country": "lt"})
    started, release = threading.Event(), threading.Event()

    def check(profile):
        started.set()
        release.wait(5)
        return "error", "не связался"

    core._amnezia_check = check
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    first = core._add_amnezia(key)
    started.wait(5)
    second = core._add_amnezia(key, "t1")
    release.set()
    job = core._job_wait(first["job"], timeout=10)

    assert second["job"] == first["job"] and second["checking"] is True
    assert second["tunnel"] == "t1"
    assert job["state"] == "error" and job["added"] == 0
    assert not core._amnezia_jobs


def test_итог_проверки_победителя_переносится_на_файл(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    monkeypatch.setattr(gateway, "probe", lambda *a: {
        "profiles": [{"name": "Amnezia Туркменистан", "conf": "x"}],
        "countries": [], "country": "tm"})
    core._amnezia_check = lambda profile: ("up", "300 мс")
    core._add_config = lambda name, text, kind="", tunnel="", place="", source_token="": {
        "ok": True, "tunnel": "t1", "name": name}
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    jid = core._job_start("add", 1, lambda j: core._search_amnezia(j, key))
    job = core._job_wait(jid, timeout=10)

    rec = core._conf_checks[("t1", "Amnezia Туркменистан")]
    assert job["added"] == 1
    assert rec["result"] == "up" and rec["answer"] == "300 мс"
    assert rec["checking"] is False


def test_уточнение_места_пишет_удержанного_без_выдачи(monkeypatch):
    from tunnelvpn import gateway, paths

    core = _core()
    monkeypatch.setattr(paths, "SINGBOX", __file__)
    probes = []
    monkeypatch.setattr(gateway, "probe", lambda *a: (
        probes.append(1) or {"profiles": [{"name": "Amnezia Литва", "conf": "x"}],
                             "countries": [], "country": "lt"}))
    core._amnezia_check = lambda profile: ("up", "300 мс")
    replies = [{"ok": False,
                "ask": {"id": "t9", "name": "Работа", "conf": "y"},
                "error": "похоже на «Работа»: заменить его конфиг или "
                         "добавить отдельным туннелем?"},
               {"ok": True, "tunnel": "t9", "name": "Amnezia Литва"}]
    calls = []
    core._add_config = lambda name, text, kind="", tunnel="", place="", source_token="": (
        calls.append((name, tunnel, place)) or replies.pop(0))
    key = {"api_key": "k", "user_country_code": "ru", "service_protocol": "awg"}

    jid = core._add_amnezia(key)["job"]
    job = core._job_wait(jid, timeout=10)
    assert job["state"] == "awaiting_place" and job["ask"]["id"] == "t9"
    assert probes == [1] and core._amnezia_ask
    retry = core._add_amnezia(key)
    assert retry["job"] == jid and probes == [1]

    reply = core._add_amnezia(key, place="replace")

    assert reply == {"ok": True, "tunnel": "t9", "name": "Amnezia Литва",
                     "added": 1, "mode": "amnezia_single"}
    assert probes == [1]
    assert calls == [("Amnezia Литва", "", ""),
                     ("Amnezia Литва", "t9", "replace")]
    assert not core._amnezia_ask
    assert core._job_snapshot(jid)["state"] == "done"


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
