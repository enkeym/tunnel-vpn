"""Класс службы: win32serviceutil должен найти его по строке модуля.

С Python 3.14 pickle.whichmodule проверяет, что класс лежит в модуле под своим
именем; без этого падают все команды tunnelvpn service.
"""

import win32serviceutil

from tunnelvpn import service


def test_класс_службы_находится_по_строке_модуля():
    cls = service._service_class()

    assert service.TunnelVPNService is cls
    assert (win32serviceutil.GetServiceClassString(cls)
            == "tunnelvpn.service.TunnelVPNService")
