from . import hysteria2, vless

PROTOS = (vless.PROTO, hysteria2.PROTO)


def _module(profile):
    proto = profile.get("proto") if isinstance(profile, dict) else None
    if proto == vless.PROTO:
        return vless
    if proto == hysteria2.PROTO:
        return hysteria2
    raise ValueError(f"тип профиля {proto!r} не поддерживается")


def parse_link(text):
    scheme = text.split(":", 1)[0].lower() if isinstance(text, str) else ""
    if scheme == vless.PROTO:
        return vless.parse_link(text)
    if scheme == hysteria2.PROTO:
        return hysteria2.parse_link(text)
    raise ValueError("поддерживаются ссылки vless:// и hysteria2://")


def normalize(profile):
    return _module(profile).normalize(profile)


def canonical(profile):
    return _module(profile).canonical(profile)


def fingerprint(profile):
    return _module(profile).fingerprint(profile)


def to_outbound(profile, tag):
    return _module(profile).to_outbound(profile, tag)


def fields(profile):
    return profile[profile.get("proto", vless.PROTO)]


def from_outbound(outbound):
    if outbound.get("type") == vless.PROTO:
        return vless.from_outbound(outbound)
    if outbound.get("type") == hysteria2.PROTO:
        return hysteria2.from_outbound(outbound)
    raise ValueError("неподдерживаемый outbound")
