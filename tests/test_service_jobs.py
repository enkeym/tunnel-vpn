import queue
import threading

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
