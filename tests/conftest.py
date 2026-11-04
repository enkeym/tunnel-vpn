"""Общая обвязка тестов.

Данные уводим в отдельный каталог ДО импорта пакета: paths читает
TUNNELVPN_DATA при импорте и определяет раскладку один раз на весь процесс.
Без этого тесты лезли бы в настоящий C:\\ProgramData\\TunnelVPN — а на машине
разработчика там лежат живые ключи.

Тесты идут на Windows, как и само приложение, но без обращений к сети и живой
службе. Поведение Windows целиком (каналы, PowerShell, службы) так не
проверить, для него нужна настоящая машина.
"""

import atexit
import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = tempfile.mkdtemp(prefix="tunnelvpn-tests-")

# Каталог свой на каждый прогон, иначе %TEMP% зарастает сотнями tunnelvpn-tests-*.
atexit.register(shutil.rmtree, _DATA, ignore_errors=True)
os.environ["TUNNELVPN_DATA"] = _DATA
# Туда же профиль пользователя: paths.LOCAL (журнал окна, WebView2).
os.environ["LOCALAPPDATA"] = os.path.join(_DATA, "local")
for _sub in ("conf", "state", os.path.join("state", "logs")):
    os.makedirs(os.path.join(_DATA, _sub), exist_ok=True)

sys.path.insert(0, os.path.join(_ROOT, "lib"))


import contextlib
import pytest

from tunnelvpn import killguard


class FakeWFP:
    def __init__(self):
        self.rules = []
        self.failure = False
        self.layer = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    @contextlib.contextmanager
    def transaction(self):
        before, layer = list(self.rules), self.layer
        try:
            yield
        except BaseException:
            self.rules, self.layer = before, layer
            raise

    def clear(self):
        self.rules = []
        self.layer = False

    def sublayer(self):
        self.layer = True

    @contextlib.contextmanager
    def app(self, filename):
        condition = killguard._condition(killguard.APP, 12, killguard.Blob())
        condition.filename = filename
        yield condition

    def luid(self, index):
        return killguard.ct.c_uint64(index)

    def add(self, layer, action, *conditions):
        if self.failure:
            raise OSError("WFP недоступен")
        self.rules.append((layer, action, conditions))


@pytest.fixture(autouse=True)
def wfp(monkeypatch):
    engine = FakeWFP()
    monkeypatch.setattr(killguard, "Engine", lambda: engine)
    return engine
