import hashlib
import json
from urllib.parse import quote, urlencode

import pytest

from tunnelvpn import vless

UUID = "00000000-0000-4000-8000-000000000001"


def _link(**over):
    query = {"type": "ws", "security": "tls", "encryption": "none",
             "path": "/ws", "host": "example.com", "sni": "example.com",
             "fp": "chrome"}
    query.update(over)
    query = {k: v for k, v in query.items() if v is not None}
    return f"vless://{UUID}@example.com:443?{urlencode(query)}#Test%20Server"


def _outbound(**over):
    ob = {"type": "vless", "tag": "cdn", "server": "example.com",
          "server_port": 443, "uuid": UUID,
          "transport": {"type": "ws", "path": "/ws",
                        "headers": {"Host": "example.com"}},
          "tls": {"enabled": True, "server_name": "example.com",
                  "utls": {"enabled": True, "fingerprint": "chrome"}}}
    for key, value in over.items():
        if value is None:
            ob.pop(key, None)
        else:
            ob[key] = value
    return ob


def _json(*outbounds, **top):
    data = {"outbounds": list(outbounds)}
    data.update(top)
    return json.dumps(data)


def test_разбор_ссылки():
    p = vless.parse_link(_link())
    f = p["vless"]

    assert p["name"] == "Test Server"
    assert p["proto"] == "vless"
    assert f["uuid"] == UUID
    assert f["server"] == "example.com"
    assert f["server_port"] == 443
    assert f["type"] == "ws"
    assert f["security"] == "tls"
    assert f["encryption"] == "none"
    assert f["path"] == "/ws"
    assert f["host"] == "example.com"
    assert f["sni"] == "example.com"
    assert f["fp"] == "chrome"
    assert f["allow_insecure"] is False


def test_умолчания_ссылки():
    p = vless.parse_link(f"vless://{UUID}@example.com?type=ws&security=tls")
    f = p["vless"]

    assert f["server_port"] == 443
    assert f["path"] == "/"
    assert f["host"] == ""
    assert f["sni"] == ""
    assert f["fp"] == ""
    assert p["name"] == "VLESS · example.com"


def test_имя_с_кириллицей_и_разделителем_сохраняется():
    link = (_link().replace("#Test%20Server",
                            "#" + quote("Токио · Cloudflare — TLS")))

    p = vless.parse_link(link)

    assert p["name"] == "Токио · Cloudflare — TLS"


@pytest.mark.parametrize("bad", [
    "",
    "http://example.com",
    "vless://not-a-uuid@example.com:443?type=ws&security=tls",
    f"vless://{UUID}@:443?type=ws&security=tls",
    f"vless://{UUID}@example.com:70000?type=ws&security=tls",
    f"vless://{UUID}@example.com:443/some/path?type=ws&security=tls",
    f"vless://{UUID}@example.com:443?type=ws&security=tls\nsecond",
])
def test_неверная_ссылка(bad):
    with pytest.raises(ValueError):
        vless.parse_link(bad)


@pytest.mark.parametrize("over", [
    {"type": "tcp"},
    {"type": None},
    {"security": "reality"},
    {"security": "none"},
    {"encryption": "aes"},
    {"fp": "super-browser"},
    {"allowinsecure": "1"},
    {"allowinsecure": "maybe"},
    {"flow": "xtls-rprx-vision"},
    {"headertype": "http"},
    {"path": "ws"},
])
def test_неподдерживаемые_параметры_ссылки(over):
    with pytest.raises(ValueError):
        vless.parse_link(_link(**over))


def test_повтор_параметра():
    with pytest.raises(ValueError):
        vless.parse_link(
            f"vless://{UUID}@example.com:443?type=ws&security=tls&path=/a&path=/b")


def test_userinfo_с_паролем():
    with pytest.raises(ValueError):
        vless.parse_link(
            f"vless://{UUID}:secret@example.com:443?type=ws&security=tls")


def test_каноническая_ссылка():
    p = vless.parse_link(_link())

    assert vless.canonical(p) == (
        f"vless://{UUID}@example.com:443?encryption=none&security=tls"
        "&type=ws&host=example.com&sni=example.com&path=%2Fws"
        "&fp=chrome&allowInsecure=0")


def test_полная_ссылка_включает_имя():
    profile = vless.parse_link(_link())

    assert vless.link(profile) == vless.canonical(profile) + "#Test%20Server"


def test_порядок_параметров_не_влияет():
    first = vless.parse_link(_link())
    second = vless.parse_link(
        f"vless://{UUID}@example.com:443?fp=chrome&sni=example.com"
        "&host=example.com&path=%2Fws&encryption=none&security=tls&type=ws")

    assert vless.canonical(first) == vless.canonical(second)


def test_имя_не_влияет_на_отпечаток():
    first = vless.parse_link(_link()[:-len("#Test%20Server")] + "#First")
    second = vless.parse_link(_link()[:-len("#Test%20Server")] + "#Second")

    assert first["name"] != second["name"]
    assert vless.fingerprint(first) == vless.fingerprint(second)


def test_регистр_uuid_и_домена():
    link = _link().replace(UUID, UUID.upper()).replace("example.com", "EXAMPLE.COM")
    p = vless.parse_link(link)

    assert p["vless"]["uuid"] == UUID
    assert p["vless"]["server"] == "example.com"
    assert vless.canonical(p) == vless.canonical(vless.parse_link(_link()))


def test_ipv6_адрес():
    with pytest.raises(ValueError, match="IPv6-адрес сервера"):
        vless.parse_link(
            f"vless://{UUID}@[2001:db8::1]:443?type=ws&security=tls"
            "&path=/ws&host=example.com&sni=example.com&fp=chrome")


def test_ipv6_сервер_в_json_отклоняется():
    with pytest.raises(ValueError, match="IPv6-адрес сервера"):
        vless.parse_singbox_json(_json(_outbound(server="2001:db8::1")))


def test_отпечаток_это_sha256_канонической_ссылки():
    p = vless.parse_link(_link())

    assert vless.fingerprint(p) == hashlib.sha256(
        vless.canonical(p).encode("utf-8")).hexdigest()


def test_outbound_для_бокового_процесса():
    ob = vless.to_outbound(vless.parse_link(_link()), "vless-work")

    assert ob == {
        "type": "vless",
        "tag": "vless-work",
        "server": "example.com",
        "server_port": 443,
        "uuid": UUID,
        "tls": {"enabled": True, "server_name": "example.com",
                "utls": {"enabled": True, "fingerprint": "chrome"}},
        "transport": {"type": "ws", "path": "/ws",
                      "headers": {"Host": "example.com"}},
    }


def test_outbound_без_хоста_и_отпечатка():
    p = vless.parse_link(f"vless://{UUID}@example.com:443?type=ws&security=tls")
    ob = vless.to_outbound(p, "t")

    assert ob["tls"] == {"enabled": True, "server_name": "example.com"}
    assert ob["transport"] == {"type": "ws", "path": "/"}


def test_outbound_без_тега():
    with pytest.raises(ValueError):
        vless.to_outbound(vless.parse_link(_link()), "")


GRPC_LINK = (
    f"vless://{UUID}@example.com:443?encryption=none&type=grpc"
    "&serviceName=grpc-vk&mode=multi&security=reality"
    "&sni=ads.x5.ru&fp=firefox"
    "&pbk=sgFFdQvfeujBKh6g7N7klU_u0mKYlr800MFbko5FzUg"
    "&sid=844352756c1fabba#Riot")


def test_разбор_ссылки_grpc_reality():
    f = vless.parse_link(GRPC_LINK)["vless"]

    assert f["type"] == "grpc"
    assert f["security"] == "reality"
    assert f["service_name"] == "grpc-vk"
    assert f["sni"] == "ads.x5.ru"


def test_outbound_grpc_reality():
    ob = vless.to_outbound(vless.parse_link(GRPC_LINK), "vless-work")

    assert ob["transport"] == {"type": "grpc", "service_name": "grpc-vk"}
    assert ob["tls"]["reality"]["public_key"] == (
        "sgFFdQvfeujBKh6g7N7klU_u0mKYlr800MFbko5FzUg")


def test_ссылка_grpc_сохраняет_сервис():
    profile = vless.parse_link(GRPC_LINK)

    assert "type=grpc" in vless.link(profile)
    assert "serviceName=grpc-vk" in vless.link(profile)
    assert vless.canonical(vless.from_outbound(
        vless.to_outbound(profile, "t"))) == vless.canonical(profile)


@pytest.mark.parametrize("over", [
    {"type": "grpc"},
    {"type": "grpc", "security": "reality", "mode": "side",
     "pbk": "sgFFdQvfeujBKh6g7N7klU_u0mKYlr800MFbko5FzUg",
     "sid": "844352756c1fabba"},
])
def test_grpc_без_reality_или_с_неверным_mode(over):
    with pytest.raises(ValueError):
        vless.parse_link(_link(serviceName="svc", **over))


def test_serviceName_только_для_grpc():
    with pytest.raises(ValueError, match="serviceName"):
        vless.parse_link(_link(serviceName="svc"))


VISION_LINK = (
    f"vless://{UUID}@example.com:443?type=tcp&security=reality"
    "&encryption=none&pbk=sgFFdQvfeujBKh6g7N7klU_u0mKYlr800MFbko5FzUg"
    "&flow=xtls-rprx-vision&fp=chrome&sni=www.wikipedia.org"
    "&sid=4f38af1528d65068#Reality")


def test_разбор_ссылки_reality_vision():
    f = vless.parse_link(VISION_LINK)["vless"]

    assert (f["security"], f["type"], f["flow"]) == (
        "reality", "tcp", "xtls-rprx-vision")


def test_outbound_reality_vision():
    ob = vless.to_outbound(vless.parse_link(VISION_LINK), "vless-work")

    assert ob["flow"] == "xtls-rprx-vision"
    assert ob["tls"]["reality"]["public_key"] == (
        "sgFFdQvfeujBKh6g7N7klU_u0mKYlr800MFbko5FzUg")
    assert "transport" not in ob


def test_ссылка_reality_vision_сохраняет_flow():
    profile = vless.parse_link(VISION_LINK)

    assert "flow=xtls-rprx-vision" in vless.link(profile)
    assert vless.canonical(vless.from_outbound(
        vless.to_outbound(profile, "t"))) == vless.canonical(profile)


@pytest.mark.parametrize("over", [
    {"flow": "xtls-rprx-vision"},
    {"flow": "xtls-rprx-direct"},
])
def test_flow_только_для_reality_tcp(over):
    with pytest.raises(ValueError):
        vless.parse_link(_link(**over))


def test_json_импортирует_grpc_reality():
    tls = {"enabled": True, "server_name": "ads.x5.ru",
           "utls": {"enabled": True, "fingerprint": "firefox"},
           "reality": {"enabled": True,
                       "public_key": "sgFFdQvfeujBKh6g7N7klU_u0mKYlr800MFbko5FzUg",
                       "short_id": "844352756c1fabba"}}
    ob = _outbound(tls=tls,
                   transport={"type": "grpc", "service_name": "grpc-vk"})

    (profile,) = vless.parse_singbox_json(_json(ob))

    assert profile["vless"]["type"] == "grpc"
    assert profile["vless"]["service_name"] == "grpc-vk"


def test_json_подписки_бота():
    config = _json(
        _outbound(domain_resolver={"server": "dns-bootstrap",
                                   "strategy": "prefer_ipv4"}),
        {"type": "urltest", "tag": "auto", "outbounds": ["cdn"],
         "url": "https://www.gstatic.com/generate_204", "interval": "30s",
         "tolerance": 250, "idle_timeout": "15m"},
        {"type": "selector", "tag": "select", "outbounds": ["auto", "cdn"],
         "default": "auto", "interrupt_exist_connections": True},
        log={"level": "warning"},
        dns={"servers": [{"type": "https", "tag": "dns-remote",
                          "server": "1.1.1.1", "detour": "select"}]},
        route={"rules": [{"ip_is_private": True, "action": "reject"}],
               "final": "select"},
    )

    profiles = vless.parse_singbox_json(config)

    assert len(profiles) == 1
    assert profiles[0]["name"] == "cdn"
    assert profiles[0]["vless"]["server"] == "example.com"
    assert profiles[0]["vless"]["path"] == "/ws"


def test_json_раскрывает_вложенные_группы():
    config = _json(
        {"type": "selector", "tag": "top", "outbounds": ["auto"]},
        {"type": "urltest", "tag": "auto", "outbounds": ["cdn"]},
        _outbound(),
    )

    profiles = vless.parse_singbox_json(config)

    assert [p["name"] for p in profiles] == ["cdn"]


def test_json_убирает_дубли_по_отпечатку():
    config = _json(
        _outbound(tag="one"),
        _outbound(tag="two"),
    )

    assert len(vless.parse_singbox_json(config)) == 1


def test_json_пропускает_direct_и_block_в_селекторе():
    config = _json(
        {"type": "selector", "tag": "select",
         "outbounds": ["direct", "cdn"]},
        {"type": "direct", "tag": "direct"},
        {"type": "block", "tag": "block"},
        _outbound(),
    )

    assert len(vless.parse_singbox_json(config)) == 1


def test_json_без_uTLS_оставляет_пустой_fp():
    config = _json(_outbound(tls={"enabled": True, "server_name": "example.com"}))

    assert vless.parse_singbox_json(config)[0]["vless"]["fp"] == ""


@pytest.mark.parametrize("top", [
    {"inbounds": [{"type": "tun", "tag": "tun-in"}]},
    {"endpoints": []},
    {"experimental": {}},
    {"clash_api": {}},
])
def test_json_отклоняет_полный_конфиг(top):
    with pytest.raises(ValueError):
        vless.parse_singbox_json(_json(_outbound(), **top))


@pytest.mark.parametrize("top", [
    {"dns": {"servers": [{"type": "https", "server": "1.1.1.1"}]}},
    {"route": {"rules": [{"ip_is_private": True, "action": "reject"}]}},
])
def test_json_не_применяет_dns_и_route(top):
    profiles = vless.parse_singbox_json(_json(_outbound(), **top))

    assert [p["name"] for p in profiles] == ["cdn"]


def test_json_отклоняет_неизвестное_поле_верхнего_уровня():
    with pytest.raises(ValueError):
        vless.parse_singbox_json(_json(_outbound(), ntp={}))


def test_json_отклоняет_reality():
    tls = {"enabled": True, "server_name": "example.com",
           "utls": {"enabled": True, "fingerprint": "chrome"},
           "reality": {"public_key": "x"}}

    with pytest.raises(ValueError, match="Reality"):
        vless.parse_singbox_json(_json(_outbound(tls=tls)))


@pytest.mark.parametrize("field", [{"detour": "other"},
                                   {"dialer": {}},
                                   {"flow": "xtls-rprx-vision"},
                                   {"multiplex": {"enabled": True}}])
def test_json_отклоняет_неподдерживаемые_поля_outbound(field):
    with pytest.raises(ValueError):
        vless.parse_singbox_json(_json(_outbound(**field)))


def test_json_отклоняет_insecure():
    tls = {"enabled": True, "server_name": "example.com", "insecure": True}

    with pytest.raises(ValueError, match="сертификат"):
        vless.parse_singbox_json(_json(_outbound(tls=tls)))


def test_json_отклоняет_tcp():
    with pytest.raises(ValueError, match="WebSocket"):
        vless.parse_singbox_json(
            _json(_outbound(transport={"type": "tcp"})))


def test_json_отклоняет_чужой_outbound():
    with pytest.raises(ValueError, match="только VLESS"):
        vless.parse_singbox_json(_json(
            _outbound(), {"type": "socks", "tag": "s", "server": "example.com",
                          "server_port": 1080}))


def test_json_отклоняет_ссылку_на_неизвестный_тег():
    with pytest.raises(ValueError, match="неизвестный"):
        vless.parse_singbox_json(_json(
            {"type": "selector", "tag": "select", "outbounds": ["missing"]}))


def test_json_отклоняет_цикл_групп():
    with pytest.raises(ValueError, match="сам на себя"):
        vless.parse_singbox_json(_json(
            {"type": "selector", "tag": "a", "outbounds": ["b"]},
            {"type": "selector", "tag": "b", "outbounds": ["a"]}))


def test_json_без_outbounds():
    with pytest.raises(ValueError, match="нет outbounds"):
        vless.parse_singbox_json(json.dumps({"outbounds": []}))


def test_json_не_объект():
    with pytest.raises(ValueError):
        vless.parse_singbox_json("[]")


def test_json_не_читается():
    with pytest.raises(ValueError, match="не JSON"):
        vless.parse_singbox_json("{")
