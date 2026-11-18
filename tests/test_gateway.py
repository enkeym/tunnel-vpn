import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from conftest import vpn_key
from tunnelvpn import buildconfig, gateway, paths

CONF = """[Interface]
PrivateKey = $WIREGUARD_CLIENT_PRIVATE_KEY
Address = 10.8.0.2/32
DNS = 10.8.0.1
Jc = 3
Jmin = 3
Jmax = 4
[Peer]
PublicKey = cHVibGljLWtleS1wdWJsaWMta2V5LXB1YmxpYy1rZXk=
Endpoint = 198.51.100.7:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""


def _rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _container(country="Молдова", conf=CONF):
    last = json.dumps({"config": conf, "Itime": 1234})
    return {"server_country_name": country, "awg": {"last_config": last}}


def _request_keys(private, body):
    data = json.loads(body)
    keys = json.loads(private.decrypt(base64.b64decode(data["key_payload"]),
                                      padding.PKCS1v15()))
    return data, base64.b64decode(keys["aes_key"]), base64.b64decode(keys["aes_iv"])


def _answer(private, body, payload):
    _, key, iv = _request_keys(private, body)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv[:gateway.BLOCK])).encryptor()
    return encryptor.update(gateway._pad(payload)) + encryptor.finalize()


def _fake_gateway(monkeypatch, status, payload):
    private = _rsa()

    def fake_post(body, timeout):
        return status, _answer(private, body, payload)

    monkeypatch.setattr(gateway, "_public_key", lambda: private.public_key())
    monkeypatch.setattr(gateway, "_post", fake_post)
    monkeypatch.setattr(gateway, "installation_uuid", lambda: "7e7e")
    return private


def test_конверт_расшифровывается_получателем():
    private = _rsa()
    body, key, iv = gateway._envelope(b'{"a": 1}', private.public_key())
    _, got_key, got_iv = _request_keys(private, body)
    data = json.loads(body)

    assert (got_key, got_iv) == (key, iv)
    assert gateway._decrypt(base64.b64decode(data["api_payload"]), key, iv) == {"a": 1}


def test_добивка_до_блока_обратима():
    for size in (1, gateway.BLOCK - 1, gateway.BLOCK, gateway.BLOCK + 1):
        data = b"x" * size
        padded = gateway._pad(data)
        assert len(padded) % gateway.BLOCK == 0
        assert gateway._unpad(padded) == data


def test_мусор_в_ответе_не_разбирается():
    with pytest.raises(ValueError, match="не разбирается"):
        gateway._decrypt(b"\x01" * 32, b"k" * 32, b"i" * 32)


def test_тело_запроса_шлюзу(monkeypatch):
    private = _fake_gateway(monkeypatch, 503, b"")
    seen = {}

    def fake_post(body, timeout):
        data, key, iv = _request_keys(private, body)
        seen.update(gateway._decrypt(base64.b64decode(data["api_payload"]),
                                     key, iv))
        return 503, b""

    monkeypatch.setattr(gateway, "_post", fake_post)
    with pytest.raises(ValueError, match="HTTP 503"):
        gateway.profiles("fake.key", "ru", "awg")

    assert seen["auth_data"] == {"api_key": "fake.key"}
    assert seen["service_type"] == gateway.SERVICE_TYPE
    assert seen["service_protocol"] == "awg"
    assert seen["user_country_code"] == "ru"
    assert seen["app_version"] == gateway.CLIENT_VERSION
    assert seen["installation_uuid"] == "7e7e"
    assert "server_country_code" not in seen
    assert len(base64.b64decode(seen["public_key"])) == 32


def test_выдача_по_ответу_шлюза(monkeypatch):
    cfg = {"containers": [_container(), _container()],
           "api_config": {"available_countries": ["md"]}}
    _fake_gateway(monkeypatch, 200,
                  json.dumps({"config": vpn_key(cfg)}).encode())

    fetched = gateway.profiles("fake.key")

    assert [p["name"] for p in fetched] == ["Amnezia Молдова",
                                            "Amnezia Молдова 2"]
    assert all(buildconfig.check_conf_text(p["conf"]) is None for p in fetched)
    assert all(gateway.PRIV_KEY_PLACEHOLDER not in p["conf"] for p in fetched)
    assert buildconfig.is_awg(buildconfig.parse_text(fetched[0]["conf"]))


def test_ответ_без_контейнеров(monkeypatch):
    _fake_gateway(monkeypatch, 200,
                  json.dumps({"config": vpn_key({"api_config": {}})}).encode())
    with pytest.raises(ValueError, match="нет контейнеров"):
        gateway.profiles("fake.key")


def test_ответ_без_конфигов(monkeypatch):
    _fake_gateway(monkeypatch, 200,
                  json.dumps({"config": vpn_key({"containers": []})}).encode())
    with pytest.raises(ValueError, match="нет конфигов"):
        gateway.profiles("fake.key")


def test_отказ_шлюза_виден_в_причине(monkeypatch):
    _fake_gateway(monkeypatch, 403,
                  json.dumps({"error": "api key expired"}).encode())
    with pytest.raises(ValueError, match="api key expired"):
        gateway.profiles("fake.key")


def test_ответ_шлюза_не_конфиг(monkeypatch):
    _fake_gateway(monkeypatch, 200, json.dumps({"other": 1}).encode())
    with pytest.raises(ValueError, match="не вернул конфиг"):
        gateway.profiles("fake.key")


def test_профиль_без_текста_пропускается():
    assert gateway._profile({"awg": {"last_config": json.dumps({"Itime": 1})}},
                            {}, 1, "K") is None
    assert gateway._profile({"awg": {}}, {}, 1, "K") is None
    assert gateway._profile({"description": "пусто"}, {"name": "d"}, 1, "K") is None


COUNTRIES = [{"server_country_code": "md", "available_protocols": ["awg"]},
             {"server_country_code": "de",
              "available_protocols": ["awg", "vless"]},
             {"server_country_code": "kz", "available_protocols": ["vless"]}]


def _country(code, name, conf=CONF, countries=None):
    last = json.dumps({"config": conf, "Itime": 1234})
    return {"containers": [{"awg": {"last_config": last}}],
            "server_country_code": code, "server_country_name": name,
            "api_config": {"available_countries": countries or []}}


def _country_gateway(monkeypatch, by_code):
    private = _rsa()
    seen = []

    def fake_post(body, timeout):
        data, key, iv = _request_keys(private, body)
        request = gateway._decrypt(base64.b64decode(data["api_payload"]),
                                   key, iv)
        code = request.get("server_country_code", "")
        seen.append(code)
        cfg = by_code.get(code)
        if cfg is None:
            return 503, b""
        return 200, _answer(private, body,
                            json.dumps({"config": vpn_key(cfg)}).encode())

    monkeypatch.setattr(gateway, "_public_key", lambda: private.public_key())
    monkeypatch.setattr(gateway, "_post", fake_post)
    monkeypatch.setattr(gateway, "installation_uuid", lambda: "7e7e")
    return seen


def test_проба_даёт_профиль_по_умолчанию_и_коды_остальных_стран(monkeypatch):
    seen = _country_gateway(
        monkeypatch, {"": _country("md", "Молдова", countries=COUNTRIES)})

    probed = gateway.probe("fake.key")

    assert seen == [""]
    assert [p["name"] for p in probed["profiles"]] == ["Amnezia Молдова"]
    assert probed["countries"] == ["de"]


def test_все_страны_запрашиваются_по_очереди(monkeypatch):
    seen = _country_gateway(monkeypatch, {
        "": _country("md", "Молдова", countries=COUNTRIES),
        "de": _country("de", "Германия", countries=COUNTRIES,
                       conf=CONF.replace("198.51.100.7", "198.51.100.8"))})

    fetched = gateway.all_profiles("fake.key")

    assert seen == ["", "de"]
    assert [p["name"] for p in fetched] == ["Amnezia Молдова",
                                            "Amnezia Германия"]


def test_сбой_страны_не_ломает_остальные(monkeypatch):
    seen = _country_gateway(
        monkeypatch, {"": _country("md", "Молдова", countries=COUNTRIES)})

    fetched = gateway.all_profiles("fake.key")

    assert seen == ["", "de"]
    assert [p["name"] for p in fetched] == ["Amnezia Молдова"]


def test_повтор_страны_не_дублирует_конфиг(monkeypatch):
    _country_gateway(monkeypatch, {
        "": _country("md", "Молдова", countries=COUNTRIES),
        "de": _country("md", "Молдова", countries=COUNTRIES)})

    fetched = gateway.all_profiles("fake.key")

    assert [p["name"] for p in fetched] == ["Amnezia Молдова"]


def test_битый_last_config():
    with pytest.raises(ValueError, match="не разобран"):
        gateway._profile({"awg": {"last_config": "{не json"}}, {}, 1, "K")


def test_негодный_конфиг_шлюза():
    with pytest.raises(ValueError, match="не годится"):
        gateway._profile(_container(conf="[Interface]\nPrivateKey = x\n"),
                         {}, 1, "K")


def test_имя_без_страны_берётся_из_контейнера():
    container = _container(country="")
    container["name"] = "Frankfurt 1"
    profile = gateway._profile(container, {}, 1, "K")
    assert profile["name"] == "Frankfurt 1"


def test_id_установки_постоянен(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "STATE", str(tmp_path))
    first = gateway.installation_uuid()

    assert first
    assert gateway.installation_uuid() == first
    assert (tmp_path / "gateway.json").exists()
