import ipaddress
import socket

import pytest

from tunnelvpn import netguard

PUBLIC = "93.184.216.34"


def _infos(*addresses):
    result = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        result.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                       "", (address, 0)))
    return result


def _resolve(monkeypatch, addresses, allow_private=False):
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *args, **kwargs: _infos(*addresses))
    return netguard.resolve_checked("sub.example.test", allow_private)


def test_разбор_https_адреса():
    info = netguard.parse_url("https://sub.example.test/path?x=1")

    assert info == {"url": "https://sub.example.test/path?x=1",
                    "host": "sub.example.test", "port": 443,
                    "path": "/path?x=1"}


def test_умолчания_адреса():
    info = netguard.parse_url("https://sub.example.test")

    assert info["port"] == 443
    assert info["path"] == "/"


def test_порт_и_фрагмент():
    info = netguard.parse_url("https://sub.example.test:8443/x#part")

    assert info["port"] == 8443
    assert info["path"] == "/x"


def test_кириллический_хост():
    info = netguard.parse_url("https://пример.рф/sub")

    assert info["host"] == "xn--e1afmkfd.xn--p1ai"


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    None,
    42,
    "http://sub.example.test/sub",
    "ftp://sub.example.test/sub",
    "https://user@sub.example.test/sub",
    "https://user:pass@sub.example.test/sub",
    "https:///sub",
    "https://sub.example.test:99999/sub",
    "https://sub.example.test:abc/sub",
    "https://exa mple.test/sub",
    "https://sub.example.test\\evil",
    "https://" + "a" * 5000,
])
def test_неверный_адрес(bad):
    with pytest.raises(ValueError):
        netguard.parse_url(bad)


@pytest.mark.parametrize("address", [
    "127.0.0.1",
    "127.10.0.1",
    "::1",
    "169.254.169.254",
    "fe80::1",
    "0.0.0.0",
    "::",
    "224.0.0.1",
    "ff02::1",
    "255.255.255.255",
    "100.64.0.1",
    "10.0.0.5",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.0.1",
    "fc00::1",
    "::ffff:127.0.0.1",
])
def test_запрещённые_адреса(monkeypatch, address):
    with pytest.raises(ValueError):
        _resolve(monkeypatch, [address])


@pytest.mark.parametrize("address", [
    "10.0.0.5",
    "172.16.0.1",
    "192.168.1.1",
    "::ffff:10.0.0.5",
    "64:ff9b::a00:5",
    "2002:0a00:0005::",
])
def test_allow_private_разрешает_rfc1918(monkeypatch, address):
    expected = str(ipaddress.ip_address(address))

    assert _resolve(monkeypatch, [address], True) == [expected]


@pytest.mark.parametrize("address", [
    "127.0.0.1",
    "::1",
    "169.254.169.254",
    "100.64.0.1",
])
def test_allow_private_не_разрешает_остальное(monkeypatch, address):
    with pytest.raises(ValueError):
        _resolve(monkeypatch, [address], True)


def test_публичные_адреса(monkeypatch):
    addresses = [PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"]

    assert _resolve(monkeypatch, addresses) == addresses


def test_дубли_адресов_убираются(monkeypatch):
    assert _resolve(monkeypatch, [PUBLIC, PUBLIC]) == [PUBLIC]


def test_смешанные_адреса_отклоняются(monkeypatch):
    with pytest.raises(ValueError):
        _resolve(monkeypatch, [PUBLIC, "10.0.0.5"])


def test_слишком_много_адресов(monkeypatch):
    addresses = [f"93.184.216.{i}" for i in range(1, 34)]

    with pytest.raises(ValueError):
        _resolve(monkeypatch, addresses)


def test_пустой_ответ_dns(monkeypatch):
    with pytest.raises(ValueError):
        _resolve(monkeypatch, [])


def test_ошибка_dns(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("нет адреса")

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fail)

    with pytest.raises(ValueError):
        netguard.resolve_checked("sub.example.test")


def _info(url):
    return {"url": url, "host": "sub.example.test", "port": 443,
            "path": "/", "ips": [PUBLIC]}


def _patch_http(monkeypatch, responses):
    calls = []

    def check(url, allow_private=False):
        calls.append(("check", url, allow_private))
        return _info(url)

    def request(info, timeout, max_bytes):
        calls.append(("request", info["url"], timeout, max_bytes))
        return responses[info["url"]]

    monkeypatch.setattr(netguard, "check_url", check)
    monkeypatch.setattr(netguard, "_request", request)
    return calls


def test_https_ответ(monkeypatch):
    calls = _patch_http(monkeypatch, {
        "https://sub.example.test/start": (200, None, b"ok"),
    })

    assert netguard.open_https("https://sub.example.test/start") == b"ok"
    assert [call[1] for call in calls] == [
        "https://sub.example.test/start",
        "https://sub.example.test/start",
    ]


def test_переход_по_https_redirect(monkeypatch):
    _patch_http(monkeypatch, {
        "https://sub.example.test/start": (302, "/next", b""),
        "https://sub.example.test/next": (200, None, b"next"),
    })

    assert netguard.open_https("https://sub.example.test/start") == b"next"


def test_redirect_на_http_запрещён(monkeypatch):
    _patch_http(monkeypatch, {
        "https://sub.example.test/start":
            (302, "http://sub.example.test/next", b""),
    })

    with pytest.raises(ValueError, match="не-HTTPS"):
        netguard.open_https("https://sub.example.test/start")


def test_redirect_без_location(monkeypatch):
    _patch_http(monkeypatch, {
        "https://sub.example.test/start": (301, None, b""),
    })

    with pytest.raises(ValueError, match="Location"):
        netguard.open_https("https://sub.example.test/start")


def test_циклический_redirect(monkeypatch):
    _patch_http(monkeypatch, {
        "https://sub.example.test/start": (302, "/start", b""),
    })

    with pytest.raises(ValueError, match="циклический"):
        netguard.open_https("https://sub.example.test/start")


def test_слишком_много_redirect(monkeypatch):
    _patch_http(monkeypatch, {
        "https://sub.example.test/1": (302, "/2", b""),
        "https://sub.example.test/2": (302, "/3", b""),
        "https://sub.example.test/3": (302, "/4", b""),
        "https://sub.example.test/4": (302, "/5", b""),
    })

    with pytest.raises(ValueError, match="слишком много"):
        netguard.open_https("https://sub.example.test/1")


def test_ошибочный_статус(monkeypatch):
    _patch_http(monkeypatch, {
        "https://sub.example.test/start": (404, None, b""),
    })

    with pytest.raises(ValueError, match="HTTP 404"):
        netguard.open_https("https://sub.example.test/start")


def test_нулевой_предел_размера():
    with pytest.raises(ValueError):
        netguard.open_https("https://sub.example.test/start", max_bytes=0)


def test_allow_private_доходит_до_проверки(monkeypatch):
    calls = _patch_http(monkeypatch, {
        "https://sub.example.test/start": (200, None, b"ok"),
    })

    netguard.open_https("https://sub.example.test/start", allow_private=True)

    assert all(call[2] is True for call in calls if call[0] == "check")


class _Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self._headers = headers or {}
        self._pos = 0

    def getheader(self, name):
        for key, value in self._headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def read(self, size):
        chunk = self._body[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


class _Connection:
    def __init__(self, response):
        self.response = response
        self.closed = False
        self.request_args = None

    def request(self, method, path, headers=None):
        self.request_args = (method, path, headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def _request(monkeypatch, response, max_bytes=1024):
    conn = _Connection(response)
    monkeypatch.setattr(netguard, "_PinnedHTTPSConnection",
                        lambda *args, **kwargs: conn)
    info = _info("https://sub.example.test/start")

    return conn, netguard._request(info, 5.0, max_bytes)


def test_запрос_читает_тело(monkeypatch):
    conn, result = _request(monkeypatch, _Response(body=b"payload"))

    assert result == (200, None, b"payload")
    assert conn.request_args[0] == "GET"
    assert conn.request_args[1] == "/"
    assert conn.closed is True


def test_огромный_content_length(monkeypatch):
    response = _Response(headers={"Content-Length": "2048"})

    with pytest.raises(ValueError, match="больше"):
        _request(monkeypatch, response)


def test_неверный_content_length(monkeypatch):
    response = _Response(headers={"Content-Length": "много"})

    with pytest.raises(ValueError, match="Content-Length"):
        _request(monkeypatch, response)


def test_отрицательный_content_length(monkeypatch):
    response = _Response(headers={"Content-Length": "-1"})

    with pytest.raises(ValueError, match="Content-Length"):
        _request(monkeypatch, response)


def test_тело_больше_предела(monkeypatch):
    response = _Response(body=b"x" * 1025)

    with pytest.raises(ValueError, match="больше"):
        _request(monkeypatch, response)


def test_не_200_тело_не_читается(monkeypatch):
    response = _Response(status=404, headers={"Location": "/next"}, body=b"x")

    assert _request(monkeypatch, response)[1] == (404, "/next", b"")


def test_сетевая_ошибка(monkeypatch):
    class Broken(_Connection):
        def getresponse(self):
            raise OSError("нет связи")

    conn = Broken(_Response())
    monkeypatch.setattr(netguard, "_PinnedHTTPSConnection",
                        lambda *args, **kwargs: conn)

    with pytest.raises(ValueError, match="недоступен"):
        netguard._request(_info("https://sub.example.test/start"), 5.0, 1024)
