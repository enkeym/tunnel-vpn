# PyInstaller: три exe за один проход.
#
#   TunnelVPN-Tray.exe      оконный    значок в трее (установленная версия)
#   tunnelvpn.exe           консольный CLI и хост службы
#   TunnelVPN-Portable.exe  оконный    всё в одном файле, без установки
#
# Первые два делят одну папку. Разными вызовами PyInstaller их собрать нельзя —
# каждый onedir-вызов делает свой каталог со своей копией рантайма, и sing-box
# с wintun лёг бы в сборку дважды. Здесь два EXE и один COLLECT, поэтому общее
# лежит один раз.
#
# Служба обязана быть консольной: StartServiceCtrlDispatcher у оконного
# процесса не поднимается, и диспетчер отваливается по таймауту (ошибка 1053).

import os
import sys

sys.path.insert(0, SPECPATH)

from spec_common import INST, ICON, ROOT, datas, hidden, version_info

cli_a = Analysis(
    [os.path.join(INST, "entry_cli.py")],
    pathex=[os.path.join(ROOT, "lib")],
    datas=datas, hiddenimports=hidden,
)
tray_a = Analysis(
    [os.path.join(INST, "entry_tray.py")],
    pathex=[os.path.join(ROOT, "lib")],
    datas=datas, hiddenimports=hidden,
)

# MERGE учит второй пакет брать общие модули из первого, а не нести свою копию.
MERGE((cli_a, "entry_cli", "tunnelvpn"), (tray_a, "entry_tray", "TunnelVPN"))

cli_pyz = PYZ(cli_a.pure)
tray_pyz = PYZ(tray_a.pure)

cli_exe = EXE(
    cli_pyz, cli_a.scripts, [], exclude_binaries=True,
    name="tunnelvpn", console=True, icon=ICON,
    version=version_info("TunnelVPN", "tunnelvpn"),
)
# Имя трея обязано отличаться от CLI не только регистром: в Windows
# TunnelVPN.exe и tunnelvpn.exe — один файл, трей ложился поверх CLI, и
# установщик, звавший «tunnelvpn.exe service install», запускал трей и висел на нём.
tray_exe = EXE(
    tray_pyz, tray_a.scripts, [], exclude_binaries=True,
    name="TunnelVPN-Tray", console=False, icon=ICON,
    version=version_info("TunnelVPN Tray", "TunnelVPN-Tray"),
    # Трей работает от администратора, но права поднимает сам (tray.run), а
    # не манифестом: с requireAdministrator ярлык спрашивал UAC и тогда, когда
    # трей уже есть и нужно только показать окно. Окно запускается из трея и
    # наследует права. Автозапуск — задачей планировщика с наивысшими правами.
)

COLLECT(
    cli_exe, cli_a.binaries, cli_a.datas,
    tray_exe, tray_a.binaries, tray_a.datas,
    name="TunnelVPN",
)
