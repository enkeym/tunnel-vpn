"""Окно: порт DevTools открывается только из исходников.

К порту цепляется playwright-cdp (installer/dev/cdp-window.ps1). В сборке
переменную не читаем: через открытый порт окном управлял бы любой локальный
процесс.
"""

import sys

from tunnelvpn import window


def test_из_исходников_порт_из_переменной(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv(window.DEVTOOLS_PORT_ENV, "9222")

    assert window._devtools_port() == 9222


def test_в_сборке_переменную_не_читаем(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(window.DEVTOOLS_PORT_ENV, "9222")

    assert window._devtools_port() is None


def test_без_переменной_или_с_мусором_порта_нет(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv(window.DEVTOOLS_PORT_ENV, raising=False)
    assert window._devtools_port() is None

    monkeypatch.setenv(window.DEVTOOLS_PORT_ENV, "порт")
    assert window._devtools_port() is None
