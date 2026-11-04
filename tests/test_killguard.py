import os
import sys

import pytest

from tunnelvpn import killguard, paths


def _data(proto="vless", enabled=True, on=True, apps=()):
    return {"kill_switch": on, "tunnels": [
        {"id": "home", "mode": "all", "proto": proto,
         "enabled": enabled, "exclude_apps": list(apps)}]}


def _conditions(wfp, key):
    guid = bytes(killguard.GUID.parse(key))
    return [(layer, action, conditions) for layer, action, conditions in wfp.rules
            if any(bytes(c.key) == guid for c in conditions)]


@pytest.mark.parametrize("proto", ["wg", "vless"])
def test_блокирует_обе_семьи_до_появления_tun(wfp, proto):
    killguard.apply(_data(proto))

    assert wfp.layer
    assert {(layer, action) for layer, action, conds in wfp.rules if not conds} == {
        (layer, killguard.BLOCK) for layer in killguard.LAYERS}
    assert _conditions(wfp, killguard.INTERFACE) == []


def test_разрешает_только_текущий_tun_и_заменяет_его_атомарно(wfp):
    killguard.apply(_data(), 42)
    killguard.apply(_data(), 73)

    rules = _conditions(wfp, killguard.INTERFACE)
    assert len(rules) == 2
    assert all(action == killguard.PERMIT for _, action, _ in rules)
    assert all(conds[0]._keep.value == 73 for _, _, conds in rules)


def test_ошибка_обновления_сохраняет_предыдущую_блокировку(wfp):
    killguard.apply(_data(), 42)
    before = list(wfp.rules)
    wfp.failure = True

    with pytest.raises(OSError):
        killguard.apply(_data(), 73)

    assert wfp.rules == before


def test_транспорт_и_явные_исключения_разрешены(wfp, monkeypatch, tmp_path):
    singbox = tmp_path / "sing-box.exe"
    app = tmp_path / "browser.exe"
    for file in (singbox, app):
        file.touch()
    monkeypatch.setattr(paths, "SINGBOX", str(singbox))

    killguard.apply(_data(apps=[str(app)]))

    rules = _conditions(wfp, killguard.APP)
    for layer in killguard.LAYERS:
        assert {conds[0].filename for family, action, conds in rules
                if family == layer and action == killguard.PERMIT and len(conds) == 1} == {
                    os.path.abspath(singbox), os.path.abspath(app)}
    dns = [conds for _, _, conds in rules if conds[0].filename == sys.executable]
    assert len(dns) == 2
    for conditions in dns:
        assert len(conditions) == 3
        assert conditions[1].value.u16 == 53
        assert conditions[2].value.u8 == 17


def test_локальная_сеть_не_разрешает_обход_через_системный_dns(wfp):
    killguard.apply(_data())

    rules = _conditions(wfp, killguard.REMOTE)
    assert rules
    assert all(layer == killguard.LAYERS[0] for layer, _, _ in rules)
    assert all(conds[1].match == 10 and conds[1].value.u16 == 53
               for _, _, conds in rules)


@pytest.mark.parametrize("data", [_data(on=False), _data(enabled=False),
                                  {"kill_switch": True, "tunnels": []}])
def test_выключение_защиты_снимает_фильтры(wfp, data):
    killguard.apply(_data(), 42)

    killguard.apply(data)

    assert wfp.rules == []
    assert not wfp.layer


def test_явный_stop_снимает_фильтры(wfp):
    killguard.apply(_data(), 42)

    killguard.release()

    assert wfp.rules == []
