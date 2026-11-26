import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import winchain

MAX_REDIRECTS = 3
MAX_BODY = 1 << 20
MAX_URL = 4096
MAX_ADDRESSES = 32
DEFAULT_TIMEOUT = 10.0

_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_ALWAYS_BLOCKED_V4 = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("240.0.0.0/4"),
)
_ALWAYS_BLOCKED_V6 = (
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fec0::/10"),
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


def _ascii_host(host):
    if not isinstance(host, str):
        raise ValueError("адрес источника: ожидаю строку")
    host = host.strip()
    if not host:
        raise ValueError("адрес источника пуст")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("недопустимое имя источника") from None
    if any(c in host for c in " \t\r\n/\\@%[]"):
        raise ValueError("недопустимое имя источника")
    host = host.rstrip(".").lower()
    if not host or len(host) > 253:
        raise ValueError("недопустимое имя источника")
    return host


def parse_url(url):
    if not isinstance(url, str):
        raise ValueError("адрес подписки: ожидаю строку")
    url = url.strip()
    if not url:
        raise ValueError("пустой адрес подписки")
    if len(url) > MAX_URL:
        raise ValueError("слишком длинный адрес подписки")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise ValueError("подписка только по HTTPS")
    if parts.username is not None or parts.password is not None:
        raise ValueError("userinfo в адресе подписки не поддерживается")
    if parts.hostname is None:
        raise ValueError("в адресе подписки нет хоста")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("неверный порт в адресе подписки") from None
    host = _ascii_host(parts.hostname)
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return {"url": url, "host": host,
            "port": 443 if port is None else port, "path": path}


def _unwrap(addr):
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return addr.ipv4_mapped
        if addr in _NAT64:
            return ipaddress.IPv4Address(addr.packed[-4:])
        if addr in _SIX_TO_FOUR:
            return ipaddress.IPv4Address(addr.packed[2:6])
    return addr


def _check_address(address, allow_private):
    addr = _unwrap(address)
    if isinstance(addr, ipaddress.IPv4Address):
        if (addr.is_loopback or addr.is_link_local or addr.is_unspecified
                or addr.is_multicast or addr.is_reserved):
            raise ValueError("адрес источника запрещён")
        if any(addr in net for net in _ALWAYS_BLOCKED_V4):
            raise ValueError("адрес источника запрещён")
        if any(addr in net for net in _RFC1918):
            if not allow_private:
                raise ValueError("частный адрес источника запрещён")
        return
    if (addr.is_loopback or addr.is_link_local or addr.is_unspecified
            or addr.is_multicast or addr.is_reserved):
        raise ValueError("адрес источника запрещён")
    if any(addr in net for net in _ALWAYS_BLOCKED_V6):
        raise ValueError("адрес источника запрещён")


def resolve_checked(host, allow_private=False):
    name = _ascii_host(host)
    try:
        infos = socket.getaddrinfo(name, 0, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("источник не разрешается в адрес") from None
    addresses = []
    seen = set()
    for info in infos:
        try:
            raw = info[4][0]
            addr = ipaddress.ip_address(raw)
        except (ValueError, IndexError):
            continue
        _check_address(addr, allow_private)
        text = str(addr)
        if text in seen:
            continue
        seen.add(text)
        addresses.append(text)
        if len(addresses) > MAX_ADDRESSES:
            raise ValueError("слишком много адресов у источника")
    if not addresses:
        raise ValueError("источник не разрешается в адрес")
    return addresses


def check_url(url, allow_private=False):
    info = parse_url(url)
    info["ips"] = resolve_checked(info["host"], allow_private)
    return info


def _tls_context(cadata=""):
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if cadata:
        context.load_verify_locations(cadata=cadata)
    return context


def _cert_reason(exc):
    message = getattr(exc, "verify_message", "") or ""
    if "local issuer" in message or "first certificate" in message:
        return "источник не прислал промежуточный сертификат"
    if "expired" in message:
        return "сертификат источника истёк"
    if "hostname" in message.casefold() or "match" in message:
        return "сертификат источника выдан другому имени"
    if "self-signed" in message or "self signed" in message:
        return "источник предъявил самоподписанный сертификат"
    return "сертификат источника не подтверждён"


def _peer_certificate(info, timeout):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = _PinnedHTTPSConnection(info["host"], info["port"], info["ips"],
                                  context, timeout)
    try:
        conn.connect()
        return conn.sock.getpeercert(True)
    finally:
        conn.close()


def _completed_chain(info, timeout):
    if not winchain.available():
        return ""
    try:
        der = _peer_certificate(info, timeout)
    except (OSError, ssl.SSLError):
        return ""
    return "".join(winchain.pems(der))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, ips, context, timeout):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ips = tuple(ips)
        self._context = context

    def connect(self):
        last = None
        for ip in self._ips:
            try:
                sock = socket.create_connection((ip, self.port), self.timeout)
            except OSError as exc:
                last = exc
                continue
            try:
                self.sock = self._context.wrap_socket(
                    sock, server_hostname=self.host)
            except (OSError, ssl.SSLError) as exc:
                sock.close()
                last = exc
                continue
            return
        if last is not None:
            raise last
        raise OSError("нет доступных адресов источника")


def _read_body(stream, max_bytes):
    chunks = []
    total = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"ответ больше {max_bytes} байт")
        chunks.append(chunk)
    return b"".join(chunks)


def _exchange(info, context, timeout, max_bytes):
    conn = _PinnedHTTPSConnection(info["host"], info["port"], info["ips"],
                                  context, timeout)
    try:
        conn.request("GET", info["path"], headers={
            "User-Agent": "TunnelVPN",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        response = conn.getresponse()
        status = response.status
        location = response.getheader("Location")
        if status != 200:
            return status, location, b""
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                raise ValueError("неверный Content-Length в ответе") from None
            if size < 0:
                raise ValueError("неверный Content-Length в ответе")
            if size > max_bytes:
                raise ValueError(f"ответ больше {max_bytes} байт")
        return status, location, _read_body(response, max_bytes)
    finally:
        conn.close()


def _request(info, timeout, max_bytes):
    deadline = time.monotonic() + timeout
    for _ in range(2):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            return _exchange(info, _tls_context(), left, max_bytes)
        except ssl.SSLCertVerificationError as exc:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ValueError(_cert_reason(exc)) from None
            cadata = _completed_chain(info, left)
            if not cadata:
                raise ValueError(_cert_reason(exc)) from None
            left = deadline - time.monotonic()
            if left <= 0:
                raise ValueError(_cert_reason(exc)) from None
            try:
                return _exchange(info, _tls_context(cadata), left, max_bytes)
            except ssl.SSLCertVerificationError as again:
                raise ValueError(_cert_reason(again)) from None
            except (OSError, ssl.SSLError, http.client.HTTPException):
                raise ValueError("источник недоступен по HTTPS") from None
        except (OSError, ssl.SSLError, http.client.HTTPException):
            continue
    raise ValueError("источник недоступен по HTTPS")


def open_https(url, allow_private=False, timeout=DEFAULT_TIMEOUT,
               max_bytes=MAX_BODY):
    if max_bytes <= 0:
        raise ValueError("предел размера ответа должен быть больше нуля")
    seen = set()
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if current in seen:
            raise ValueError("циклический redirect подписки")
        seen.add(current)
        info = check_url(current, allow_private)
        status, location, body = _request(info, timeout, max_bytes)
        if status == 200:
            return body
        if status in (301, 302, 303, 307, 308):
            if not location:
                raise ValueError("redirect без Location")
            try:
                target = urljoin(current, location)
                scheme = urlsplit(target).scheme.lower()
            except ValueError:
                raise ValueError("неверный redirect подписки") from None
            if scheme != "https":
                raise ValueError("redirect на не-HTTPS запрещён")
            current = target
            continue
        raise ValueError(f"источник ответил HTTP {status}")
    raise ValueError("слишком много redirect подписки")
