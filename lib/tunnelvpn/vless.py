import hashlib
import ipaddress
import json
import re
import uuid as _uuid
from urllib.parse import parse_qsl, quote, unquote, urlsplit

PROTO = "vless"
NAME_MAX_LEN = 80
MAX_PROFILES = 500
DEFAULT_PORT = 443
FP_VALUES = frozenset({
    "chrome", "firefox", "edge", "safari", "360", "qq", "ios", "android",
    "random", "randomized",
})

_LINK_KEYS = frozenset({
    "encryption", "security", "type", "host", "sni", "path", "fp",
    "allowinsecure", "headertype", "flow", "pbk", "sid", "spx",
    "support-x25519mlkem768", "servicename", "mode",
})
_TOP_KEYS = frozenset({"outbounds", "log", "dns", "route"})
_TOP_BLOCKED = ("inbounds", "endpoints", "experimental",
                "clash_api", "cache_file", "ntp", "certificate",
                "certificate_providers")
_OUT_KEYS = frozenset({
    "type", "tag", "server", "server_port", "uuid", "flow", "network",
    "tls", "transport", "domain_resolver",
})
_TLS_KEYS = frozenset({"enabled", "server_name", "utls", "insecure", "alpn", "reality"})
_UTLS_KEYS = frozenset({"enabled", "fingerprint"})
_WS_KEYS = frozenset({
    "type", "path", "headers", "max_early_data", "early_data_header_name",
    "service_name",
})
_NODE_TYPES = frozenset({"vless", "selector", "urltest", "direct", "block"})

_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _text(value, fallback=""):
    value = " ".join(_CONTROL.sub("", value).split())
    return value[:NAME_MAX_LEN] or fallback


def _unquote(value, field):
    if _BAD_PERCENT.search(value):
        raise ValueError(f"{field}: неверное percent-кодирование")
    try:
        return unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError(f"{field}: не UTF-8 после percent-декодирования") from None


def _check_uuid(value):
    if not isinstance(value, str):
        raise ValueError("UUID: ожидаю строку")
    try:
        return str(_uuid.UUID(value.strip())).lower()
    except (ValueError, AttributeError, TypeError):
        raise ValueError("UUID имеет неверный формат") from None


def _check_port(value):
    if isinstance(value, bool):
        raise ValueError("порт: ожидаю число")
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.isdigit():
        port = int(value)
    else:
        raise ValueError("порт: ожидаю число")
    if not 1 <= port <= 65535:
        raise ValueError(f"порт {port} вне диапазона 1-65535")
    return port


def _check_host(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field}: ожидаю строку")
    value = value.strip()
    if not value:
        raise ValueError(f"{field}: пустой адрес")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        if isinstance(ip, ipaddress.IPv6Address) and ip.scope_id:
            raise ValueError(f"{field}: IPv6 с зоной не поддерживается")
        return ip.compressed
    if not value.isascii():
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError(f"{field}: недопустимое имя") from None
    value = value.rstrip(".").lower()
    if len(value) > 253:
        raise ValueError(f"{field}: слишком длинное имя")
    labels = value.split(".")
    if any(not _LABEL.match(label) or len(label) > 63 for label in labels):
        raise ValueError(f"{field}: недопустимое имя")
    return value


def _check_path(value):
    if not isinstance(value, str):
        raise ValueError("path: ожидаю строку")
    if len(value) > 2048:
        raise ValueError("path: слишком длинный")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("path: пробелы и управляющие символы недопустимы")
    if not value.startswith("/"):
        raise ValueError("path должен начинаться с /")
    return value


def _check_service(value):
    if not isinstance(value, str):
        raise ValueError("serviceName: ожидаю строку")
    if len(value) > 256:
        raise ValueError("serviceName: слишком длинный")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("serviceName: пробелы и управляющие символы недопустимы")
    return value


def _check_fp(value):
    if not isinstance(value, str):
        raise ValueError("fp: ожидаю строку")
    fp = value.strip().lower()
    if not fp:
        return ""
    if fp not in FP_VALUES:
        raise ValueError(f"uTLS fingerprint {fp!r} не поддерживается")
    return fp


def _check_flow(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError("flow: ожидаю строку")
    flow = value.strip().lower()
    if not flow:
        return ""
    if flow != "xtls-rprx-vision":
        raise ValueError("flow не поддерживается")
    return flow


def _check_allow_insecure(value):
    low = value.strip().lower()
    if low in ("1", "true", "yes", "on"):
        raise ValueError("отключение проверки сертификата (allowInsecure) "
                         "не поддерживается")
    if low not in ("", "0", "false", "no", "off"):
        raise ValueError("allowInsecure: ожидаю 0 или 1")


def _check_header_type(value):
    low = value.strip().lower()
    if low not in ("", "none"):
        raise ValueError("поддерживается только WebSocket без HTTP-обфускации")
    return ""


def _profile(name, uuid_value, server, port, host, path, sni, fp,
             security="tls", kind="ws", public_key="", short_id="",
             service_name="", flow=""):
    uuid_value = _check_uuid(uuid_value)
    server = _check_host(server, "адрес сервера")
    if ":" in server:
        raise ValueError("IPv6-адрес сервера пока не поддерживается: нужен IPv4 или домен с A-записью")
    port = _check_port(port)
    path = _check_path(path or "/")
    host = _check_host(host, "host") if host else ""
    sni = _check_host(sni, "sni") if sni else ""
    fp = _check_fp(fp)
    if (security, kind) not in (("tls", "ws"), ("reality", "tcp"),
                                ("reality", "grpc")):
        raise ValueError("поддерживается VLESS WebSocket/TLS, Reality/TCP "
                         "или Reality/gRPC")
    if kind == "grpc":
        service_name = _check_service(service_name)
        path = "/"
    elif service_name:
        raise ValueError("serviceName задан для транспорта без gRPC")
    flow = _check_flow(flow)
    if flow and (security, kind) != ("reality", "tcp"):
        raise ValueError("flow xtls-rprx-vision поддерживается только "
                         "с Reality/TCP")
    if security == "reality":
        if not isinstance(public_key, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{43}", public_key):
            raise ValueError("Reality public key имеет неверный формат")
        if not isinstance(short_id, str) or not re.fullmatch(
                r"(?:[0-9a-fA-F]{2}){0,8}", short_id):
            raise ValueError("Reality short id имеет неверный формат")
        if not sni:
            raise ValueError("Reality требует sni")
    elif public_key or short_id:
        raise ValueError("ключ Reality задан для обычного TLS")
    name = _text(name if isinstance(name, str) else "",
                 f"VLESS · {server}")
    return {
        "name": name,
        "proto": PROTO,
        "vless": {
            "uuid": uuid_value,
            "server": server,
            "server_port": port,
            "security": "tls",
            "type": "ws",
            "encryption": "none",
            "host": host,
            "path": path,
            "sni": sni,
            "fp": fp,
            "security": security,
            "type": kind,
            "public_key": public_key,
            "short_id": short_id.lower(),
            "allow_insecure": False,
            "service_name": service_name,
            "flow": flow,
        },
    }


def _query_values(query):
    if len(query) > 4096:
        raise ValueError("слишком длинный запрос ссылки")
    if _BAD_PERCENT.search(query):
        raise ValueError("неверное percent-кодирование в ссылке")
    try:
        pairs = parse_qsl(query, keep_blank_values=True, errors="strict")
    except UnicodeDecodeError:
        raise ValueError("параметры ссылки не UTF-8") from None
    values = {}
    for key, value in pairs:
        key = key.lower()
        if key not in _LINK_KEYS:
            raise ValueError(f"неподдерживаемый параметр ссылки: {key}")
        if key in values:
            raise ValueError(f"параметр {key} повторяется")
        values[key] = value
    return values


def parse_link(text):
    if not isinstance(text, str):
        raise ValueError("ссылка: ожидаю строку")
    text = text.strip()
    if not text:
        raise ValueError("пустая ссылка")
    if "\n" in text or "\r" in text:
        raise ValueError("ожидается одна ссылка vless://")
    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError("неверная ссылка vless://") from None
    if parts.scheme.lower() != "vless":
        raise ValueError("поддерживается только vless:// "
                         "(WireGuard/AWG — через .conf)")
    if parts.password is not None:
        raise ValueError("userinfo с паролем в ссылке не поддерживается")
    if parts.path not in ("", "/"):
        raise ValueError("лишний путь в ссылке vless://")
    if not parts.username:
        raise ValueError("в ссылке нет UUID")
    if hostname is None:
        raise ValueError("в ссылке нет адреса сервера")
    values = _query_values(parts.query)
    encryption = values.get("encryption", "none").strip().lower()
    if encryption not in ("", "none"):
        raise ValueError("поддерживается только encryption=none")
    security = values.get("security", "").strip().lower()
    if security not in ("tls", "reality"):
        raise ValueError("нужен TLS или Reality")
    kind = values.get("type", "").strip().lower()
    if (security, kind) not in (("tls", "ws"), ("reality", "tcp"),
                                ("reality", "grpc")):
        raise ValueError("поддерживается WebSocket/TLS, Reality/TCP "
                         "или Reality/gRPC")
    mode = values.get("mode", "").strip().lower()
    if kind == "grpc":
        if mode not in ("", "gun", "multi"):
            raise ValueError("неверный mode gRPC")
    elif mode:
        raise ValueError("параметр mode относится только к gRPC")
    service_name = values.get("servicename", "")
    if kind != "grpc" and service_name:
        raise ValueError("serviceName задан для транспорта без gRPC")
    if security == "reality":
        if "spx" in values:
            _check_path(values["spx"] or "/")
        if values.get("support-x25519mlkem768", "").lower() not in (
                "", "0", "1", "false", "true"):
            raise ValueError("неверный support-x25519mlkem768")
    elif any(key in values for key in ("pbk", "sid", "spx", "support-x25519mlkem768")):
        raise ValueError("параметры Reality заданы для обычного TLS")
    _check_header_type(values.get("headertype", ""))
    flow = _check_flow(values.get("flow", ""))
    _check_allow_insecure(values.get("allowinsecure", ""))
    path = values.get("path", "") or "/"
    name = _unquote(parts.fragment, "имя") if parts.fragment else ""
    return _profile(
        name,
        _unquote(parts.username, "UUID"),
        _unquote(hostname, "адрес сервера"),
        DEFAULT_PORT if port is None else port,
        values.get("host", ""),
        path,
        values.get("sni", ""),
        values.get("fp", ""),
        security,
        kind,
        values.get("pbk", ""),
        values.get("sid", ""),
        service_name,
        flow,
    )


def normalize(profile):
    fields = _fields(profile)
    name = profile.get("name") if isinstance(profile, dict) else ""
    return _profile(
        name,
        fields.get("uuid"),
        fields.get("server"),
        fields.get("server_port"),
        fields.get("host") or "",
        fields.get("path") or "/",
        fields.get("sni") or "",
        fields.get("fp") or "",
        fields.get("security") or "tls",
        fields.get("type") or "ws",
        fields.get("public_key") or "",
        fields.get("short_id") or "",
        fields.get("service_name") or "",
        fields.get("flow") or "",
    )


def _fields(profile):
    if not isinstance(profile, dict):
        raise ValueError("профиль VLESS: ожидаю объект")
    fields = profile.get("vless")
    if fields is None and "server" in profile and "uuid" in profile:
        fields = profile
    if not isinstance(fields, dict):
        raise ValueError("профиль VLESS: нет полей vless")
    return fields


def _normalized_fields(profile):
    fields = _fields(profile)
    server = _check_host(fields.get("server"), "адрес сервера")
    host = fields.get("host") or ""
    sni = fields.get("sni") or ""
    return _profile(
        "",
        fields.get("uuid"),
        server,
        fields.get("server_port"),
        _check_host(host, "host") if host else "",
        fields.get("path") or "/",
        _check_host(sni, "sni") if sni else "",
        fields.get("fp") or "",
        fields.get("security") or "tls",
        fields.get("type") or "ws",
        fields.get("public_key") or "",
        fields.get("short_id") or "",
        fields.get("service_name") or "",
        fields.get("flow") or "",
    )["vless"]


def canonical(profile):
    fields = _normalized_fields(profile)
    server = fields["server"]
    authority = f"[{server}]" if ":" in server else server
    values = (
        ("encryption", "none"),
        ("security", fields["security"]),
        ("type", fields["type"]),
    )
    if fields["security"] == "reality":
        if fields["flow"]:
            values += (("flow", fields["flow"]),)
        values += (("sni", fields["sni"]), ("fp", fields["fp"]),
                   ("pbk", fields["public_key"]), ("sid", fields["short_id"]))
        if fields["type"] == "grpc":
            values += (("serviceName", fields["service_name"]),)
    else:
        values += (("host", fields["host"]), ("sni", fields["sni"]),
                   ("path", fields["path"]), ("fp", fields["fp"]),
                   ("allowInsecure", "0"))
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in values)
    return (f"vless://{fields['uuid']}@{authority}:"
            f"{fields['server_port']}?{query}")


def link(profile):
    normalized = normalize(profile)
    return canonical(normalized) + "#" + quote(normalized["name"], safe="")


def fingerprint(profile):
    return hashlib.sha256(canonical(profile).encode("utf-8")).hexdigest()


def to_outbound(profile, tag):
    if not isinstance(tag, str) or not tag:
        raise ValueError("тег VLESS outbound: ожидаю непустую строку")
    fields = _normalized_fields(profile)
    tls = {
        "enabled": True,
        "server_name": fields["sni"] or fields["server"],
    }
    if fields["fp"]:
        tls["utls"] = {"enabled": True, "fingerprint": fields["fp"]}
    if fields["security"] == "reality":
        tls["reality"] = {"enabled": True,
                          "public_key": fields["public_key"],
                          "short_id": fields["short_id"]}
    out = {
        "type": "vless",
        "tag": tag,
        "server": fields["server"],
        "server_port": fields["server_port"],
        "uuid": fields["uuid"],
        "tls": tls,
    }
    if fields["flow"]:
        out["flow"] = fields["flow"]
    if fields["type"] == "ws":
        transport = {"type": "ws", "path": fields["path"]}
        if fields["host"]:
            transport["headers"] = {"Host": fields["host"]}
        out["transport"] = transport
    elif fields["type"] == "grpc":
        out["transport"] = {"type": "grpc",
                            "service_name": fields["service_name"]}
    return out


def from_outbound(outbound):
    for key in outbound:
        if key not in _OUT_KEYS:
            if key in ("detour", "dialer"):
                raise ValueError(f"поле {key} не импортируется: профиль "
                                 f"должен подключаться напрямую")
            raise ValueError(f"поле {key} в VLESS outbound не поддерживается")
    network = outbound.get("network")
    if network not in (None, "", "tcp", "ws", "grpc"):
        raise ValueError(f"network {network!r} не поддерживается: "
                         f"только TCP/WebSocket/gRPC")
    flow = _check_flow(outbound.get("flow", ""))
    tls = outbound.get("tls")
    if not isinstance(tls, dict):
        raise ValueError("у VLESS outbound нет TLS")
    for key in tls:
        if key not in _TLS_KEYS:
            if key in ("certificate", "certificate_path"):
                raise ValueError("сертификаты и локальные пути в JSON "
                                 "не импортируются")
            raise ValueError(f"поле tls.{key} не поддерживается")
    if tls.get("enabled") is not True:
        raise ValueError("TLS должен быть включён")
    if tls.get("insecure"):
        raise ValueError("отключение проверки сертификата (insecure) "
                         "не поддерживается")
    if tls.get("alpn"):
        raise ValueError("ALPN в JSON не импортируется")
    sni = tls.get("server_name") or ""
    if not isinstance(sni, str):
        raise ValueError("tls.server_name: ожидаю строку")
    fp = ""
    utls = tls.get("utls")
    if utls is not None:
        if not isinstance(utls, dict):
            raise ValueError("tls.utls: ожидаю объект")
        for key in utls:
            if key not in _UTLS_KEYS:
                raise ValueError(f"поле tls.utls.{key} не поддерживается")
        enabled = utls.get("enabled")
        if enabled not in (None, True, False):
            raise ValueError("tls.utls.enabled: ожидаю true или false")
        fp = _check_fp(utls.get("fingerprint", ""))
        if enabled is False and fp:
            raise ValueError("uTLS выключен, но fingerprint задан")
        if enabled is True and not fp:
            raise ValueError("uTLS включён без fingerprint")
    reality = tls.get("reality")
    security, kind, public_key, short_id = "tls", "tcp", "", ""
    service_name, path, host = "", "/", ""
    transport = outbound.get("transport")
    if reality is not None:
        if not isinstance(reality, dict) or set(reality) - {"enabled", "public_key", "short_id"}:
            raise ValueError("неверные параметры Reality")
        if reality.get("enabled") is not True:
            raise ValueError("Reality должен быть включён")
        security = "reality"
        public_key, short_id = reality.get("public_key", ""), reality.get("short_id", "")
    if transport is None:
        if security != "reality":
            raise ValueError("нужен WebSocket transport")
    else:
        if not isinstance(transport, dict):
            raise ValueError("transport: ожидаю объект")
        for key in transport:
            if key not in _WS_KEYS:
                raise ValueError(f"поле transport.{key} не поддерживается")
        ttype = transport.get("type")
        if ttype == "grpc":
            if security != "reality":
                raise ValueError("gRPC поддерживается только с Reality")
            kind = "grpc"
            service_name = _check_service(transport.get("service_name", ""))
        elif ttype == "ws":
            kind = "ws"
            path = transport.get("path") or "/"
            headers = transport.get("headers")
            if headers is not None:
                if not isinstance(headers, dict):
                    raise ValueError("transport.headers: ожидаю объект")
                for key, value in headers.items():
                    if key.lower() != "host":
                        raise ValueError(f"заголовок {key} в транспорте "
                                         f"не поддерживается")
                    if not isinstance(value, str):
                        raise ValueError("Host: ожидаю строку")
                    host = value
        else:
            raise ValueError("поддерживается только WebSocket или gRPC transport")
    return _profile(
        outbound.get("tag") or "",
        outbound.get("uuid"),
        outbound.get("server"),
        outbound.get("server_port"),
        host,
        path,
        sni,
        fp,
        security,
        kind,
        public_key,
        short_id,
        service_name,
        flow,
    )


def parse_singbox_json(text):
    if not isinstance(text, str):
        raise ValueError("JSON: ожидаю строку")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"не JSON: {exc.msg}") from None
    if not isinstance(doc, dict):
        raise ValueError("JSON sing-box: ожидаю объект")
    for key in _TOP_BLOCKED:
        if key in doc:
            if key in ("inbounds", "endpoints"):
                raise ValueError("это полная конфигурация sing-box "
                                 "(inbounds/TUN); импортируются только VLESS "
                                 "outbounds — возьмите обычную подписку")
            raise ValueError(f"поле {key} в JSON не импортируется: "
                             f"только VLESS outbounds")
    for key in doc:
        if key not in _TOP_KEYS:
            raise ValueError(f"поле {key} в JSON не импортируется: "
                             f"только VLESS outbounds")
    raw = doc.get("outbounds")
    if not isinstance(raw, list) or not raw:
        raise ValueError("в JSON нет outbounds")
    if len(raw) > MAX_PROFILES:
        raise ValueError(f"outbounds больше {MAX_PROFILES}")

    nodes = {}
    order = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("outbound должен быть объектом")
        kind = item.get("type")
        if kind not in _NODE_TYPES:
            raise ValueError(f"outbound {kind!r} не поддерживается: "
                             f"только VLESS")
        tag = item.get("tag")
        if tag is not None:
            if not isinstance(tag, str) or not tag.strip():
                raise ValueError("у outbound пустой tag")
            if tag in nodes:
                raise ValueError(f"tag {tag!r} повторяется")
            nodes[tag] = item
        order.append(item)

    selectors = [item for item in order if item.get("type") == "selector"]
    urltests = [item for item in order if item.get("type") == "urltest"]
    if selectors:
        roots = selectors
    elif urltests:
        roots = urltests
    else:
        roots = [item for item in order if item.get("type") == "vless"]

    def resolve(node, stack):
        kind = node.get("type")
        if kind == "vless":
            return [node]
        if kind in ("direct", "block"):
            return []
        if kind not in ("selector", "urltest"):
            raise ValueError(f"outbound {kind!r} не поддерживается: "
                             f"только VLESS")
        tag = node.get("tag")
        if tag is None:
            raise ValueError(f"у {kind} нет tag")
        if tag in stack:
            raise ValueError(f"{kind} {tag!r} ссылается сам на себя")
        children = node.get("outbounds")
        if not isinstance(children, list) or not children:
            raise ValueError(f"{kind} {tag!r}: пустой outbounds")
        found = []
        for child in children:
            if not isinstance(child, str) or not child:
                raise ValueError(f"{kind} {tag!r}: неверная ссылка")
            target = nodes.get(child)
            if target is None:
                raise ValueError(f"{kind} {tag!r} ссылается на неизвестный "
                                 f"outbound: {child}")
            found.extend(resolve(target, stack | {tag}))
        return found

    profiles = []
    seen = set()
    for root in roots:
        for outbound in resolve(root, frozenset()):
            profile = from_outbound(outbound)
            mark = fingerprint(profile)
            if mark in seen:
                continue
            seen.add(mark)
            profiles.append(profile)
    if not profiles:
        raise ValueError("в JSON нет VLESS outbounds")
    return profiles
