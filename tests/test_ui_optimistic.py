"""Общий оптимистичный слой UI (блок B плана): хранилище желаемого —
одна реализация в core.js, ею пользуются и карточки, и топ-бар; отказ
службы откатывает все желаемые состояния."""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "lib", "tunnelvpn", "ui", "js")

SIGNS = ("const WANT = new Map();",
         "function wanted(",
         "function sendWanted(",
         "const PENDING_MS")


def _src(name):
    with open(os.path.join(JS, name), encoding="utf-8") as fh:
        return fh.read()


def test_хранилище_желаемого_определено_только_в_core():
    for name in sorted(f for f in os.listdir(JS) if f.endswith(".js")):
        for sig in SIGNS:
            assert (sig in _src(name)) == (name == "core.js"), f"{sig} в {name}"


def test_топбар_флипает_vpn_через_общее_желаемое():
    top = _src("topbar.js")
    assert "WANT.set('vpn:up'" in top
    assert "wanted('vpn:up'" in top
    assert "PENDING?.mode" not in top


def test_плитка_при_поднятии_желает_и_vpn_включённым():
    assert "WANT.set('vpn:up', {val: true, at});" in _src("cards.js")


def test_отказ_команды_откатывает_желаемое():
    assert "WANT.clear()" in _src("core.js")
