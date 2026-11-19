import base64
import http.client
import json
import os
import platform
import secrets
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from . import buildconfig, paths, subscriptions

HOST = "gw.amnezia.org"
PORT = 80
CONFIG_PATH = "/v1/config"
SERVICE_TYPE = "amnezia-premium"
CLIENT_NAME = "TunnelVPN"
# Шлюз сверяет app_version со своим списком клиентов и отказывает старым
# («client version update is required»), поэтому здесь версия протокола,
# которую мы умеем, а не версия сборки TunnelVPN.
CLIENT_VERSION = "5.1.2.0"
LANGUAGE = "ru"
PROTOCOL = "awg"
NAME_PREFIX = "Amnezia"
AES_KEY_LEN = 32
AES_IV_LEN = 32
AES_SALT_LEN = 8
BLOCK = 16
MAX_BODY = 1 << 20
DEFAULT_TIMEOUT = 20.0
# «too many requests» — не отказ ключа, а просьба подождать: шлюз режет частые
# выдачи по одному ключу. Такую страну досылаем через паузу, а не записываем
# в недоступные.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT = 5.0
PRIV_KEY_PLACEHOLDER = "$WIREGUARD_CLIENT_PRIVATE_KEY"

# Открытый ключ шлюза: base64 от PEM, как его кладёт в свою сборку Amnezia.
PUBLIC_KEY_B64 = ("LS0tLS1CRUdJTiBQVUJMSUMgS0VZLS0tLS0KTUlJQ0lqQU5CZ2txaGtpRzl3MEJBUUVG"
                  "QUFPQ0FnOEFNSUlDQ2dLQ0FnRUFqNW14bC80REwzU2s4OW50eHM1RwpYM0phd0dRV0lvcT"
                  "Zydk5rT3pOR3VOZ2VkTlMyK3BpNmhabDNJemwxSW85b200S2lVbE1UNm1nTE8xaFRyOXEr"
                  "CnM3Q1lobHZyb0ZBN0VydWNGKzlMKzdGQ3QwSWdpMGtJSy9SMi92eGQvMkhhVXJvcm4vYV"
                  "N2dnV0a1l3YmZ4cVcKU3d0ekUrUnVCZURXR3ZFdDkzN09XMG9xWU9OUFl2OUU0VDU2RHov"
                  "RVo2djJ0OGVqQW5LTGJHRC9Hb2NNbWlwSwo3ZXRGU2lTTUFCMlJtYXp0cVRxNE5sZUJlcG"
                  "ZPODBYcFlsVzlwQ1NYdUhjRTh3eEhjemt6eHNieU1BTXNHL0szCnZVUVk2cVB0b2hxcXpT"
                  "U0J3YS84dTJwdE5IQmVvcjdsN0RkWVhlUi9OcWNjNHo5MlZVa1o1bE9WUjRldmtTNVYKL3"
                  "dRcXA1dG5PSkVqM05qVWhFaFhGb05FYXBiWmQxYmg2aVFvVWs3akMxVGR2S0ovblBLR1pB"
                  "c0hScHIwck5LegpmeC9OL09vNmxyMnloLytwczZWeFRrYlBtQjZFODVXT08zVXZqSW1aVV"
                  "kwWFFkQmpXbGUvNGlKTGRFQzc3TnIwCmpYaGRnZXlwdWN5NmprQjZpQkhNZVZNbHJOTUVW"
                  "N1V4b0JSL2NDTng1NXp1LzhzbWw1QnlpRHZDRFQ3c1JvbU4KTmdWdDVTL0ZhVmpZdXpGVW"
                  "lmSjEyVG9DaFhGZ0VTS0ZtdXNvN1dsdUVhV3ZNSUdSRWRyTXJLUUtIZllMT3pXRgoyQjVa"
                  "SkRxdzRvMDNmVTRKLzZydzYxTTFiK3JqVnBYTWpQbnpjMkErUmdjalR2WHY5NTVnZlprd2"
                  "U0bHQ1d2svCjNqOHpNVm8zK3pMck1UQWFFZUlVTTBVQ0F3RUFBUT09Ci0tLS0tRU5EIFBV"
                  "QkxJQyBLRVktLS0tLQ==")

_CONTAINERS = (SERVICE_TYPE, "amnezia-awg", "awg", "amnezia-wg", "wireguard")


def _b64(data):
    return base64.b64encode(data).decode()


def _public_key():
    if not PUBLIC_KEY_B64:
        raise ValueError("в сборке нет открытого ключа шлюза Amnezia")
    try:
        return serialization.load_pem_public_key(base64.b64decode(PUBLIC_KEY_B64))
    except ValueError:
        raise ValueError("открытый ключ шлюза Amnezia повреждён") from None


def _pad(data):
    size = BLOCK - len(data) % BLOCK
    return data + bytes([size]) * size


def _unpad(data):
    if not data or len(data) % BLOCK:
        raise ValueError("ответ шлюза Amnezia повреждён")
    size = data[-1]
    if not 1 <= size <= BLOCK:
        raise ValueError("ответ шлюза Amnezia повреждён")
    return data[:-size]


def _cbc(key, iv, data, encrypting):
    algorithm = algorithms.AES(key)
    context = (Cipher(algorithm, modes.CBC(iv)).encryptor() if encrypting
               else Cipher(algorithm, modes.CBC(iv)).decryptor())
    return context.update(data) + context.finalize()


def _envelope(payload, public_key):
    """Тело запроса шлюза: AES-ключ пересылается RSA-ом, данные — CBC по 16 байт."""
    key = secrets.token_bytes(AES_KEY_LEN)
    iv = secrets.token_bytes(AES_IV_LEN)
    salt = secrets.token_bytes(AES_SALT_LEN)
    keys = json.dumps({"aes_key": _b64(key), "aes_iv": _b64(iv),
                       "aes_salt": _b64(salt)}).encode()
    body = json.dumps({
        "key_payload": _b64(public_key.encrypt(keys, padding.PKCS1v15())),
        "api_payload": _b64(_cbc(key, iv[:BLOCK], _pad(payload), True)),
    }).encode()
    return body, key, iv


def _decrypt(raw, key, iv):
    try:
        return json.loads(_unpad(_cbc(key, iv[:BLOCK], raw, False)).decode())
    except (UnicodeDecodeError, ValueError):
        raise ValueError("ответ шлюза Amnezia не разбирается") from None


def _reason(status, raw, key, iv):
    try:
        data = _decrypt(raw, key, iv)
    except ValueError:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            data = None
    if isinstance(data, dict):
        for field in ("error", "message", "details"):
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
    return f"шлюз Amnezia ответил HTTP {status}"


def installation_uuid():
    """Постоянный id установки: шлюз по нему помнит выдачу конфигов по ключу."""
    path = os.path.join(paths.STATE, "gateway.json")
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get("installation_uuid", "")
        if isinstance(value, str) and value:
            return value
    except (OSError, ValueError):
        pass
    value = str(uuid.uuid4())
    try:
        os.makedirs(paths.STATE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"installation_uuid": value}, fh)
    except OSError:
        pass
    return value


def _post(body, timeout):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    try:
        conn.request("POST", CONFIG_PATH, body=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "X-Client-Request-ID": str(uuid.uuid4()),
            "User-Agent": f"{CLIENT_NAME}/{paths.version()}",
        })
        response = conn.getresponse()
        chunks = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BODY:
                raise ValueError(f"ответ шлюза больше {MAX_BODY} байт")
            chunks.append(chunk)
        return response.status, b"".join(chunks)
    except (OSError, http.client.HTTPException):
        raise ValueError("шлюз Amnezia недоступен") from None
    finally:
        conn.close()


def _rate_limited(reason):
    """Шлюз просит подождать, а не отказывает по ключу."""
    return "too many requests" in reason.casefold()


def _client_key():
    """Пара Curve25519: публичную отдаём шлюзу, приватную он вернёт в конфиге."""
    private = X25519PrivateKey.generate()
    return (_b64(private.private_bytes(Encoding.Raw, PrivateFormat.Raw,
                                       NoEncryption())),
            _b64(private.public_key().public_bytes(Encoding.Raw,
                                                   PublicFormat.Raw)))


def _text(data, *names):
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _container(obj):
    for name in _CONTAINERS:
        part = obj.get(name)
        if isinstance(part, dict):
            return name, part
    return "", obj


def _profile(container, cfg, number, private_key):
    """Конфиг контейнера ответа шлюза; без готового текста — None."""
    kind, part = _container(container)
    last = part.get("last_config")
    if isinstance(last, str):
        try:
            last = json.loads(last)
        except ValueError:
            raise ValueError("контейнер шлюза Amnezia не разобран") from None
    if not isinstance(last, dict):
        return None
    text = last.get("config")
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.replace(PRIV_KEY_PLACEHOLDER, private_key)
    bad = buildconfig.check_conf_text(text)
    if bad:
        raise ValueError(f"конфиг шлюза Amnezia не годится: {bad}")
    country = _text(container, "server_country_name") or _text(
        cfg, "server_country_name")
    name = buildconfig.safe_name(
        f"{NAME_PREFIX} {country}" if country else
        _text(container, "name", "display_name") or
        _text(cfg, "name") or f"{NAME_PREFIX} {kind or PROTOCOL} {number}")
    return {"name": name, "conf": text}


def _fetch(api_key, country, protocol, server_country, timeout):
    private_key, public_key = _client_key()
    payload = {
        "user_country_code": country,
        "service_type": SERVICE_TYPE,
        "service_protocol": protocol or PROTOCOL,
        "public_key": public_key,
        "auth_data": {"api_key": api_key},
        "os_version": f"{platform.system()} {platform.release()}",
        "app_version": CLIENT_VERSION,
        "cli_name": CLIENT_NAME,
        "app_language": LANGUAGE,
        "installation_uuid": installation_uuid(),
    }
    if server_country:
        payload["server_country_code"] = server_country
    packed = json.dumps(payload).encode()
    body, key, iv = _envelope(packed, _public_key())
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        status, raw = _post(body, timeout)
        if status == 200:
            break
        reason = _reason(status, raw, key, iv)
        if attempt == RATE_LIMIT_RETRIES or not _rate_limited(reason):
            raise ValueError(reason)
        time.sleep(RATE_LIMIT_WAIT * (attempt + 1))
        body, key, iv = _envelope(packed, _public_key())
    text = _decrypt(raw, key, iv).get("config")
    if not isinstance(text, str):
        raise ValueError("шлюз Amnezia не вернул конфиг")
    config = subscriptions.vpn_payload(text)
    containers = config.get("containers")
    if not isinstance(containers, list):
        raise ValueError("в ответе шлюза Amnezia нет контейнеров")
    taken, out = set(), []
    for number, container in enumerate(containers, 1):
        if not isinstance(container, dict):
            continue
        profile = _profile(container, config, number, private_key)
        if profile is None:
            continue
        name, repeats = profile["name"], 1
        while name in taken:
            repeats += 1
            name = buildconfig.safe_name(f"{profile['name']} {repeats}")
        profile["name"] = name
        taken.add(name)
        out.append(profile)
    if not out:
        raise ValueError("в ответе шлюза Amnezia нет конфигов")
    return out, config


def _country_codes(config, protocol):
    api = config.get("api_config")
    items = api.get("available_countries") if isinstance(api, dict) else None
    if not isinstance(items, list):
        return []
    codes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("server_country_code", "")).strip().lower()
        protos = item.get("available_protocols")
        if not code or code in codes:
            continue
        if isinstance(protos, list) and protos and protocol not in protos:
            continue
        codes.append(code)
    return codes


def profiles(api_key, country="", protocol="", timeout=DEFAULT_TIMEOUT):
    """Конфиги Amnezia Premium по ключу: [{"name": имя, "conf": текст}]."""
    return _fetch(api_key, country, protocol, "", timeout)[0]


def probe(api_key, country="", protocol="", timeout=DEFAULT_TIMEOUT):
    fetched, config = _fetch(api_key, country, protocol, "", timeout)
    default = str(config.get("server_country_code", "")).strip().lower()
    codes = [c for c in _country_codes(config, protocol or PROTOCOL)
             if c != default]
    return {"profiles": fetched, "countries": codes, "country": default}



