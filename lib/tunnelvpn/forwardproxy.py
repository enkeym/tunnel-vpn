import hashlib
from urllib.parse import quote, urlsplit

from . import vless

PROTOS = ("http", "socks4", "socks5")
OUTBOUND_TYPES = ("http", "socks")


def normalize(profile):
    proto = profile.get("proto")
    fields = profile.get(proto)
    if proto not in PROTOS or not isinstance(fields, dict):
        raise ValueError("нет параметров HTTP/SOCKS прокси")
    server = vless._check_host(fields.get("server"), "адрес прокси")
    port = vless._check_port(fields.get("server_port"))
    username, password = fields.get("username", ""), fields.get("password", "")
    for value in (username, password):
        if not isinstance(value, str) or vless._CONTROL.search(value):
            raise ValueError("неверные учётные данные прокси")
    if password and not username:
        raise ValueError("пароль прокси задан без логина")
    if proto == "socks4" and password:
        raise ValueError("SOCKS4 не поддерживает пароль")
    name = profile.get("name", "")
    if not isinstance(name, str):
        raise ValueError("неверное имя прокси")
    return {"proto": proto,
            "name": vless._text(name, f"{proto.upper()} · {server}:{port}"),
            proto: {"server": server, "server_port": port,
                    "username": username, "password": password}}


def parse_link(text):
    if not isinstance(text, str) or vless._CONTROL.search(text):
        raise ValueError("ожидается одна ссылка HTTP/SOCKS прокси")
    try:
        parts = urlsplit(text.strip())
        server, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError("неверная ссылка прокси") from None
    if (parts.scheme not in PROTOS or not server or port is None
            or parts.path not in ("", "/") or parts.query):
        raise ValueError("прокси: нужен адрес protocol://host:port без пути и параметров")
    return normalize({"proto": parts.scheme,
                      "name": vless._unquote(parts.fragment, "имя"),
                      parts.scheme: {"server": server, "server_port": port,
                                     "username": vless._unquote(parts.username or "", "логин"),
                                     "password": vless._unquote(parts.password or "", "пароль")}})


def canonical(profile):
    profile = normalize(profile)
    proto = profile["proto"]
    fields = profile[proto]
    server = fields["server"]
    authority = f"[{server}]" if ":" in server else server
    auth = ""
    if fields["username"]:
        auth = quote(fields["username"], safe="")
        if fields["password"]:
            auth += ":" + quote(fields["password"], safe="")
        auth += "@"
    return f"{proto}://{auth}{authority}:{fields['server_port']}"


def fingerprint(profile):
    return hashlib.sha256(canonical(profile).encode("utf-8")).hexdigest()


def link(profile):
    profile = normalize(profile)
    return canonical(profile) + "#" + quote(profile["name"], safe="")


def to_outbound(profile, tag):
    profile = normalize(profile)
    proto = profile["proto"]
    out = {"type": "http" if proto == "http" else "socks", "tag": tag,
           **profile[proto]}
    if proto != "http":
        out["version"] = "4" if proto == "socks4" else "5"
        out["network"] = "tcp"
    return out


def from_outbound(outbound):
    allowed = {"type", "tag", "server", "server_port", "username", "password",
               "version", "network", "domain_resolver"}
    if set(outbound) - allowed or outbound.get("network", "tcp") != "tcp":
        raise ValueError("неподдерживаемые параметры HTTP/SOCKS outbound")
    kind = outbound.get("type")
    version = outbound.get("version", "5")
    if kind not in OUTBOUND_TYPES or (kind == "socks" and version not in ("4", "5")):
        raise ValueError("неподдерживаемый тип прокси")
    proto = "http" if kind == "http" else "socks" + version
    fields = {key: outbound[key] for key in
              ("server", "server_port", "username", "password") if key in outbound}
    return normalize({"proto": proto, "name": outbound.get("tag", ""), proto: fields})
