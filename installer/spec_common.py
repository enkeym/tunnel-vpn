import os
import re

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

INST = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(INST)
# Рисует make_icon.py в installer/gen (build.ps1 зовёт его до PyInstaller).
ICON = os.path.join(INST, "gen", "TunnelVPN.ico")

with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
    VERSION = fh.read().strip()


def version_info(description, filename):
    """Ресурс версии exe. Без него у FileDescription пусто, и диспетчер задач
    вместо «TunnelVPN» показывает имя файла.

    Числовая версия — только цифры из VERSION («0.2.9-beta» → 0.2.9.0):
    суффикс в FixedFileInfo не помещается, он остаётся в строковых полях.
    """
    nums = [int(n) for n in re.findall(r"\d+", VERSION.split("-")[0])][:4]
    nums += [0] * (4 - len(nums))
    strings = [
        StringStruct("CompanyName", "Enkeym"),
        StringStruct("FileDescription", description),
        StringStruct("FileVersion", VERSION),
        StringStruct("InternalName", filename),
        StringStruct("OriginalFilename", filename + ".exe"),
        StringStruct("ProductName", "TunnelVPN"),
        StringStruct("ProductVersion", VERSION),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple(nums), prodvers=tuple(nums)),
        kids=[
            # 0409 — английский (США), 04B0 — Unicode: таблица по умолчанию,
            # её Windows находит без перевода.
            StringFileInfo([StringTable("040904B0", strings)]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ])

def ui_datas():
    """Вёрстка окна по файлу, без ui/dev: там мок моста и скриншоты
    предпросмотра, в сборке им делать нечего. Путь внутри сборки — тот, где
    его ищет paths.UI_DIR во frozen."""
    ui = os.path.join(ROOT, "lib", "tunnelvpn", "ui")
    out = []
    for dirpath, dirnames, filenames in os.walk(ui):
        dirnames[:] = [d for d in dirnames if d != "dev"]
        rel = os.path.relpath(dirpath, ui).replace(os.sep, "/")
        dest = "tunnelvpn/ui" if rel == "." else f"tunnelvpn/ui/{rel}"
        out += [(os.path.join(dirpath, f), dest) for f in filenames]
    return out


datas = ui_datas() + [
    (os.path.join(ROOT, "VERSION"), "."),
    (os.path.join(ROOT, "happ_bridge", "LICENSE-happwn"), "."),
]

# Модули службы pywin32 подтягиваются через getattr, статический анализ их
# не видит и в сборку не кладёт.
hidden = [
    "win32timezone", "servicemanager", "win32serviceutil",
    "win32service", "win32event", "win32pipe", "win32file",
    "win32security", "ntsecuritycon", "win32api",
    # WMI в winnet: импортируются внутри функций.
    "pythoncom", "win32com.client", "win32process",
]
