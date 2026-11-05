import hashlib
from urllib.parse import quote, urlsplit

from . import vless

PROTO = "hysteria2"
_LINK_KEYS = frozenset({"alpn", "fp", "obfs", "obfs-password", "security", "sni"})


def _profile(name, server, port, password, sni, alpn, fp, obfs, obfs_password):
    server = vless._check_host(server, "адрес сервера")
    port = vless._check_port(port)
    if not isinstance(password, str) or not password or len(password) > 2048:
        raise ValueError("Hysteria2 требует пароль")
    sni = vless._check_host(sni, "sni") if sni else ""
    fp = vless._check_fp(fp)
    if not isinstance(alpn, str) or any(not x.strip() for x in alpn.split(",")):
        raise ValueError("неверный ALPN")
    if obfs not in ("", "salamander"):
        raise ValueError("поддерживается только obfs=salamander")
    if obfs and (not isinstance(obfs_password, str) or not obfs_password):
        raise ValueError("obfs=salamander требует obfs-password")
    if not obfs and obfs_password:
        raise ValueError("obfs-password задан без obfs")
    return {
        "name": vless._text(name if isinstance(name, str) else "", f"Hysteria2 · {server}"),
        "proto": PROTO,
        "hysteria2": {"server": server, "server_port": port,
                      "password": password, "sni": sni, "alpn": alpn,
                      "fp": fp, "obfs": obfs,
                      "obfs_password": obfs_password},
    }


def parse_link(text):
    if not isinstance(text, str) or "\n" in text or "\r" in text:
        raise ValueError("ожидается одна ссылка hysteria2://")
    try:
        parts = urlsplit(text.strip())
        server, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError("неверная ссылка hysteria2://") from None
    if parts.scheme.lower() != PROTO or not server or not port or parts.path not in ("", "/"):
        raise ValueError("неверная ссылка hysteria2://")
    if parts.username is None or parts.password is not None:
        raise ValueError("Hysteria2 требует пароль в ссылке")
    password = vless._unquote(parts.username, "пароль")
    if len(parts.query) > 4096 or vless._BAD_PERCENT.search(parts.query):
        raise ValueError("неверное percent-кодирование в ссылке")
    from urllib.parse import parse_qsl
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, errors="strict")
    except UnicodeDecodeError:
        raise ValueError("параметры ссылки не UTF-8") from None
    values = {}
    for key, value in pairs:
        key = key.lower()
        if key not in _LINK_KEYS or key in values:
            raise ValueError(f"неподдерживаемый или повторный параметр: {key}")
        values[key] = value
    if values.get("security", "tls").lower() != "tls":
        raise ValueError("Hysteria2 требует TLS")
    return _profile(vless._unquote(parts.fragment, "имя") if parts.fragment else "",
                    server, port, password, values.get("sni", ""),
                    values.get("alpn", ""), values.get("fp", ""),
                    values.get("obfs", ""), values.get("obfs-password", ""))


def normalize(profile):
    fields = profile.get(PROTO)
    if not isinstance(fields, dict):
        raise ValueError("нет параметров Hysteria2")
    return _profile(profile.get("name", ""), fields.get("server"),
                    fields.get("server_port"), fields.get("password"),
                    fields.get("sni", ""), fields.get("alpn", ""),
                    fields.get("fp", ""), fields.get("obfs", ""),
                    fields.get("obfs_password", ""))


def canonical(profile):
    fields = normalize(profile)[PROTO]
    server = fields["server"]
    authority = f"[{server}]" if ":" in server else server
    values = (("security", "tls"), ("sni", fields["sni"]),
              ("alpn", fields["alpn"]), ("fp", fields["fp"]),
              ("obfs", fields["obfs"]),
              ("obfs-password", fields["obfs_password"]))
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in values)
    return f"hysteria2://{quote(fields['password'], safe='')}@{authority}:{fields['server_port']}?{query}"


def fingerprint(profile):
    return hashlib.sha256(canonical(profile).encode("utf-8")).hexdigest()


def to_outbound(profile, tag):
    fields = normalize(profile)[PROTO]
    tls = {"enabled": True}
    if fields["sni"]:
        tls["server_name"] = fields["sni"]
    if fields["alpn"]:
        tls["alpn"] = [x.strip() for x in fields["alpn"].split(",")]
    if fields["fp"]:
        tls["utls"] = {"enabled": True, "fingerprint": fields["fp"]}
    out = {"type": PROTO, "tag": tag, "server": fields["server"],
           "server_port": fields["server_port"], "password": fields["password"],
           "tls": tls}
    if fields["obfs"]:
        out["obfs"] = {"type": fields["obfs"], "password": fields["obfs_password"]}
    return out


def from_outbound(outbound):
    if set(outbound) - {"type", "tag", "server", "server_port", "password",
                         "obfs", "tls", "domain_resolver"}:
        raise ValueError("Hysteria2 outbound содержит неподдерживаемые поля")
    tls = outbound.get("tls")
    if not isinstance(tls, dict) or tls.get("enabled") is not True or tls.get("insecure"):
        raise ValueError("Hysteria2 требует TLS с проверкой сертификата")
    if set(tls) - {"enabled", "server_name", "alpn", "utls"}:
        raise ValueError("Hysteria2 TLS содержит неподдерживаемые поля")
    utls = tls.get("utls") or {}
    if not isinstance(utls, dict) or set(utls) - {"enabled", "fingerprint"}:
        raise ValueError("неверные параметры uTLS")
    if utls and utls.get("enabled") is not True:
        raise ValueError("uTLS выключен")
    obfs = outbound.get("obfs") or {}
    if not isinstance(obfs, dict) or set(obfs) - {"type", "password"}:
        raise ValueError("неверные параметры obfs")
    alpn = tls.get("alpn") or []
    if not isinstance(alpn, list) or any(not isinstance(x, str) for x in alpn):
        raise ValueError("неверный ALPN")
    return _profile(outbound.get("tag") or "", outbound.get("server"),
                    outbound.get("server_port"), outbound.get("password"),
                    tls.get("server_name", ""), ",".join(alpn),
                    utls.get("fingerprint", ""), obfs.get("type", ""),
                    obfs.get("password", ""))
