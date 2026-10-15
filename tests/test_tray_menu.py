"""Пункты туннелей в меню трея: подписи и цвета кружков из статуса службы,
клик — окно у карточки туннеля."""

import pystray
import pytest
from pystray._util import win32

from tunnelvpn import tray

NONE = frozenset()


def _work(**over):
    return {"id": "work", "name": "Работа", "mode": "list", "check": "up", **over}


def _home(**over):
    return {"id": "home", "name": "Личный", "mode": "all", "check": "up", **over}


def _up(*items, **st):
    return {"up": True, "exit_ip": "1.2.3.4", "exit_state": "tunnel",
            "tunnels": list(items or (_work(), _home())), **st}


def test_выключенный_vpn_серый_даже_во_время_проверки():
    st = {**_up(), "up": False}
    assert tray._conf_colors(st, frozenset({"work", "home"})) == {"work": "off", "home": "off"}


def test_во_время_проверки_рыжий():
    assert tray._conf_colors(_up(), frozenset({"work", "home"})) == {"work": "busy",
                                                                    "home": "busy"}


def test_все_работают_зелёные():
    st = _up(_work(), _work(id="lab"), _home())
    assert tray._conf_colors(st, NONE) == {"work": "up", "lab": "up", "home": "up"}


def test_проверенный_основной_зелёный_пока_рабочий_ещё_рыжий():
    st = _up(_work(check=""), _home())
    assert tray._conf_colors(st, frozenset({"work"})) == {"work": "busy", "home": "up"}


def test_проверка_службы_по_туннелям_тоже_рыжая():
    st = _up(_work(checking=True), _home(checking=False))
    assert tray._conf_colors(st, NONE) == {"work": "busy", "home": "up"}


def test_идет_включение_все_рыжие():
    assert set(tray._conf_colors(_up(busy="включаю"), NONE).values()) == {"busy"}


def test_туннели_в_lparam_и_обратно():
    ids = ("work", "lab", "home")
    for pending in (NONE, frozenset({"lab"}), frozenset({"work", "home"}), frozenset(ids)):
        assert tray._sides_of(tray._side_bits(pending, ids), ids) == pending


def test_удалённый_туннель_в_lparam_не_попадает():
    assert tray._side_bits(frozenset({"gone", "home"}), ("work", "home")) == 0b10


def test_основной_на_запасном_выходе_красный_даже_с_итогом_up():
    """Сторож переключает выход раньше, чем проверка его перемерит."""
    colors = tray._conf_colors(_up(out="direct"), NONE)
    assert colors == {"work": "up", "home": "error"}


@pytest.mark.parametrize("check, color", [("error", "error"), ("none", "off"), ("", "off"),
                                          ("rules", "busy")])
def test_молчит_красный_а_без_домена_проверки_серый(check, color):
    assert tray._conf_colors(_up(_work(check=check)), NONE) == {"work": color}


def test_запасной_выход_в_подсказке_значка():
    app = tray.Tray.__new__(tray.Tray)
    app.installing = False
    app.status = _up(_work(), _home(name="Дом"), out="direct", exit_state="direct")

    assert "запасной выход" in app._title()
    assert "«Дом» не работает" in app._title()
    assert "УТЕЧКА" not in app._title()


def test_без_основного_подсказка_всё_остальное_напрямую():
    app = tray.Tray.__new__(tray.Tray)
    app.installing = False
    app.status = _up(_work(), exit_ip="5.6.7.8", exit_state="direct")

    assert app._title() == "TunnelVPN — работает · всё остальное напрямую"


def test_выключенный_основной_подсказка_всё_остальное_напрямую():
    """Выключенный основной остаётся основным, но не везёт ничего: выход
    напрямую — так задумано, это не запасной выход."""
    app = tray.Tray.__new__(tray.Tray)
    app.installing = False
    app.status = _up(_work(), _home(enabled=False, check=""), out="direct",
                     exit_state="direct")

    assert app._title() == "TunnelVPN — работает · всё остальное напрямую"
    assert tray._conf_colors(app.status, NONE) == {"work": "up", "home": "off"}
    assert tray._state_word(_home(enabled=False, confs=["nl-1"]), app.status, NONE) == "выключен"


@pytest.mark.parametrize("t, name", [
    ({}, None),
    ({"confs": ["nl-1"]}, "nl-1"),
    ({"confs": ["nl-1", "nl-2"], "active": "nl-2"}, "nl-2"),
    # Из нескольких без выбора сборка не возьмёт ни один — и меню не покажет.
    ({"confs": ["nl-1", "nl-2"]}, None),
    ({"confs": ["nl-1"], "active": "удалённый"}, None),
])
def test_имя_конфига_туннеля(t, name):
    assert tray._conf_name(t) == name


def _hooked(calls):
    # Значок не настоящий: обработчики сообщений без окна в трее.
    class Icon:
        _message_handlers = {win32.WM_NOTIFY: lambda w, l: calls.append(("orig", l))}

    app = tray.Tray.__new__(tray.Tray)
    app.icon = Icon()
    app.on_window = lambda: calls.append(("window", None))
    app._hook_menu()
    return app.icon._message_handlers[win32.WM_NOTIFY]


def test_одиночный_левый_клик_открывает_окно():
    calls = []
    notify = _hooked(calls)

    notify(0, win32.WM_LBUTTONUP)

    assert calls == [("window", None)]


def test_двойной_левый_клик_сам_окно_не_открывает():
    calls = []
    notify = _hooked(calls)

    notify(0, tray.WM_LBUTTONDBLCLK)

    assert calls == []


@pytest.mark.parametrize("t, st, word", [
    (_work(), {}, "нет конфига"),
    (_work(confs=["a", "b"]), {}, "не выбран конфиг"),
    (_work(confs=["a"], enabled=False), {}, "выключен"),
    (_work(confs=["a"]), {"up": False}, "ждёт включения VPN"),
    (_work(confs=["a"], last="error"), {"up": False}, "не работал в прошлый раз"),
    (_work(confs=["a"]), {"busy": "включаю"}, "включаю…"),
    (_work(confs=["a"], checking=True), {}, "проверяю…"),
    (_work(confs=["a"]), {}, "работает"),
    (_work(confs=["a"], check="error"), {}, "не отвечает"),
    (_work(confs=["a"], check="rules"), {}, "домен не открывается"),
    (_work(confs=["a"], check="none"), {}, "нечем проверить"),
    (_work(confs=["a"], check=""), {}, "не проверен"),
    (_home(confs=["nl-1"]), {"out": "direct"}, "не работает · интернет напрямую"),
])
def test_подпись_имя_туннеля_и_короткое_состояние(t, st, word):
    assert tray._labels(_up(t, **st), NONE) == {t["id"]: f"{t['name']} · {word}"}


def test_ждущий_ответа_трея_проверяется_а_остальные_нет():
    labels = tray._labels(_up(_work(confs=["a"]), _home(confs=["nl-1"])), frozenset({"work"}))

    assert labels == {"work": "Работа · проверяю…", "home": "Личный · работает"}


def test_длинное_имя_туннеля_обрезается():
    label = tray._labels(_up(_work(name="x" * 100)), NONE)["work"]

    assert label == "x" * (tray.NAME_MAX - 1) + "… · нет конфига"


def _menu_app(st):
    app = tray.Tray()
    app.status = st
    app._menu_ids = tuple(t["id"] for t in st["tunnels"])
    app.opened = []
    app.on_window = lambda focus=None: app.opened.append(focus)
    return app


def test_клик_по_туннелю_открывает_окно_у_его_карточки():
    app = _menu_app(_up(_work(), _home(confs=["nl-1"])))

    work, home = app._tunnel_entries(pystray)
    work(None)
    home(None)

    assert work.text == "Работа · нет конфига"
    assert home.text == "Личный · работает"
    assert app.opened == ["work", "home"]


def test_пункты_туннелей_те_же_объекты_а_удалённый_забыт():
    app = _menu_app(_up(_work(), _work(id="lab", name="Лаб"), _home()))
    work, lab, home = app._tunnel_entries(pystray)

    app.status = _up(_home(), _work())
    app._menu_ids = ("home", "work")
    again = app._tunnel_entries(pystray)

    assert again == (home, work)
    assert set(app._tunnel_items) == {"home", "work"}


def test_в_меню_только_статус_туннелей_без_конфигов_и_галочек():
    """Добавление конфига и вкл/выкл туннеля — только в окне: ни подменю,
    ни «Добавить конфиг…», ни «Включён» даже у туннеля «по списку» с конфигом."""
    app = _menu_app(_up(_work(confs=["office"], enabled=False), _home()))

    items = [i for i in app._menu().items if i is not pystray.Menu.SEPARATOR]

    assert [i.text for i in items] == ["Выключить", "Перезапустить", "Работа · выключен",
                                       "Личный · нет конфига", "Закрыть"]
    assert not [i for i in items if i.submenu or i.checked]


def test_выключенный_серый_и_на_живом_и_на_выключенном_vpn():
    off = _work(enabled=False, check="", last="error")

    assert tray._conf_colors(_up(off, _home()), frozenset({"work", "home"})) == {
        "work": "off", "home": "busy"}
    assert tray._conf_colors({**_up(off), "up": False}, NONE) == {"work": "off"}
    assert tray._conf_colors(_up(off, busy="включаю"), NONE) == {"work": "off"}


