"""Ключ vpn:// Amnezia Premium: распознавание и декод пэйлоада."""

import pytest

from conftest import vpn_key
from tunnelvpn import subscriptions


def test_ключ_распознаётся_как_vpn():
    assert subscriptions.detect_kind(vpn_key()) == "vpn"


def test_декод_отдаёт_api_key_и_параметры_сервиса():
    assert subscriptions.decode_vpn_key(vpn_key()) == {
        "api_key": "fake.key", "service_protocol": "awg",
        "user_country_code": "ru", "name": "Amnezia Premium"}


def test_битый_base64():
    with pytest.raises(ValueError, match="base64"):
        subscriptions.decode_vpn_key("vpn://!!!!")


def test_неожиданный_заголовок():
    import base64
    raw = b"\x00\x00\x00\xfe" + b"\x78\x9c"
    key = "vpn://" + base64.b64encode(raw, altchars=b"-_").decode()
    with pytest.raises(ValueError, match="заголовок"):
        subscriptions.decode_vpn_key(key)


def test_повреждённое_тело():
    import base64
    raw = b"\x00\x00\x00\xff" + b"not zlib"
    key = "vpn://" + base64.b64encode(raw, altchars=b"-_").decode()
    with pytest.raises(ValueError, match="повреждено"):
        subscriptions.decode_vpn_key(key)


def test_чужой_сервис_в_ключе():
    cfg = {"config_version": 2,
           "api_config": {"service_type": "self-hosted"},
           "auth_data": {"api_key": "k"}}
    with pytest.raises(ValueError, match="не от Amnezia Premium"):
        subscriptions.decode_vpn_key(vpn_key(cfg))


def test_ключ_без_api_key():
    cfg = {"config_version": 2,
           "api_config": {"service_type": "amnezia-premium"}}
    with pytest.raises(ValueError, match="нет api_key"):
        subscriptions.decode_vpn_key(vpn_key(cfg))


def test_импорт_отказывается_одним_сообщением():
    with pytest.raises(ValueError) as exc1:
        subscriptions.parse_import(vpn_key())
    with pytest.raises(ValueError) as exc2:
        subscriptions.parse_body("auto", vpn_key())
    assert str(exc1.value) == str(exc2.value) == subscriptions.VPN_IMPORT_ERROR


def test_обычные_форматы_остались_на_месте():
    assert subscriptions.detect_kind('{"outbounds": []}') == "singbox"
    assert subscriptions.detect_kind("vless://uuid@host:443") == "uri-list"
