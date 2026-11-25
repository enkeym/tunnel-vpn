import os
import sys

sys.path.insert(0, SPECPATH)

from spec_common import ICON, INST, ROOT, datas, hidden, version_info

# --------------------------------------------------------- портативная версия
#
# Отдельный однофайловый exe: ни установки, ни службы, ни распакованной папки.
# Внутрь кладём и sing-box.exe с wintun.dll — иначе «портативная» версия всё
# равно требовала бы носить рядом два файла, и смысл терялся бы.
#
# В MERGE выше её включать нельзя: MERGE учит сборки брать общие модули друг у
# друга, а onefile обязан быть самодостаточным.

portable_datas = datas + [
    (os.path.join(ROOT, "lib", "bin", "sing-box.exe"), "."),
    (os.path.join(ROOT, "lib", "bin", "wintun.dll"), "."),
    (os.path.join(ROOT, "lib", "bin", "happwn.dll"), "."),
]

portable_a = Analysis(
    [os.path.join(INST, "entry_portable.py")],
    pathex=[os.path.join(ROOT, "lib")],
    datas=portable_datas, hiddenimports=hidden,
)
portable_pyz = PYZ(portable_a.pure)

portable_exe = EXE(
    portable_pyz, portable_a.scripts,
    portable_a.binaries, portable_a.datas, [],
    name="TunnelVPN-Portable",
    console=False, icon=ICON,
    version=version_info("TunnelVPN Portable", "TunnelVPN-Portable"),
    # Манифест requireAdministrator: UAC спрашивается один раз при запуске.
    # Без него процесс не сможет ни создать адаптер, ни править маршруты, и
    # приложение молча не заработало бы.
    uac_admin=True,
)
