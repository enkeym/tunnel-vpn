import base64
import contextlib
import concurrent.futures
import json
import os
import ssl
import threading
import types
import urllib.error
import urllib.request

import pytest

from tunnelvpn import buildconfig, paths, probe, proxy, service, subscriptions, tunnel, tunnels


@pytest.mark.parametrize("scheme,kind,version", [
    ("http", "http", None), ("socks4", "socks", "4"), ("socks5", "socks", "5")])
def test_ссылка_экспортируется_и_собирается(scheme, kind, version):
    profile = proxy.parse_link(f"{scheme}://203.0.113.8:1080#Test")

    outbound = proxy.to_outbound(profile, "Test")

    assert outbound["type"] == kind
    assert outbound.get("version") == version
    assert ("network" in outbound) == (kind == "socks")
    assert proxy.parse_link(proxy.link(profile)) == profile
    assert proxy.from_outbound(outbound) == profile
    cfg = buildconfig.side_config_vless(profile, buildconfig.new_link())
    assert buildconfig.side_host(cfg) == "203.0.113.8"
    assert buildconfig.set_side_host(cfg, "203.0.113.9")
    assert buildconfig.side_host(cfg) == "203.0.113.9"
    assert cfg["route"]["default_domain_resolver"] == buildconfig.VLESS_DNS_TAG


def test_логин_пароль_ipv6_и_имя():
    profile = proxy.parse_link("socks5://user%40name:p%3Ass@[2001:db8::1]:65535#%D0%98%D0%BC%D1%8F")

    assert profile["name"] == "Имя"
    assert proxy.fields(profile)["username"] == "user@name"
    assert proxy.fields(profile)["password"] == "p:ss"
    assert proxy.canonical(profile) == "socks5://user%40name:p%3Ass@[2001:db8::1]:65535"
    assert proxy.parse_link(proxy.link(profile)) == profile


@pytest.mark.parametrize("link", [
    "http://host", "http://host:0", "http://host:65536", "socks5://host:abc",
    "http://host:80/path", "socks5://host:80?extra=1", "socks4://user:pass@host:1080",
    "http://user%0a@host:80", "http://user%xx@host:80", "http://:pass@host:80",
    "socks5://host:1080\n", "https://host:443"])
def test_неверная_ссылка_отклоняется(link):
    with pytest.raises(ValueError):
        proxy.parse_link(link)


def test_пачка_пропускает_ошибки_и_дубли():
    text = "http://203.0.113.1:80\nbad\nsocks4://203.0.113.1:80\nhttp://203.0.113.1:80#Other"

    parsed = subscriptions.parse_import(text)
    encoded = subscriptions.parse_import(base64.b64encode(text.encode()).decode())

    assert parsed["invalid"] == 1
    assert parsed["duplicates"] == 1
    assert [p["proto"] for p in parsed["profiles"]] == ["http", "socks4"]
    assert encoded == parsed


def test_пачка_больше_500_и_предел():
    links = [f"socks5://203.0.113.1:{port}" for port in range(1, 2001)]

    assert len(subscriptions.parse_import("\n".join(links))["profiles"]) == 2000
    with pytest.raises(ValueError, match="2000"):
        subscriptions.parse_import("\n".join(links + [links[0]]))


@pytest.fixture
def proxy_network(monkeypatch, tmp_path):
    state = types.SimpleNamespace(configs=[], stopped=[], statuses={})
    binary = tmp_path / "sing-box.exe"
    binary.touch()
    monkeypatch.setattr(paths, "SINGBOX", str(binary))
    monkeypatch.setenv("NO_PROXY", "*")

    class Process:
        pid = 987654

        def __init__(self, args, **kwargs):
            with open(args[args.index("-c") + 1], encoding="utf-8") as fh:
                cfg = json.load(fh)
            state.configs.append(cfg)
            for incoming, outgoing in zip(cfg["inbounds"], cfg["outbounds"]):
                state.statuses[incoming["listen_port"]] = 204 if outgoing["server_port"] % 2 else 502

        def poll(self):
            return None

        def terminate(self):
            state.stopped.append(self.pid)

        def wait(self, timeout):
            return 0

    def open_response(opener, request, timeout):
        assert request.type == "https"
        assert request._tunnel_host == "cp.cloudflare.com"
        assert request.host.startswith("127.0.0.1:")
        assert request.get_header("Proxy-authorization").startswith("Basic ")
        handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
        assert handler._context.verify_mode == ssl.CERT_REQUIRED
        port = int(request.host.rsplit(":", 1)[1])
        if state.statuses[port] == 502:
            raise urllib.error.URLError("unreachable")
        return contextlib.nullcontext(types.SimpleNamespace(status=state.statuses[port]))

    monkeypatch.setattr(tunnel.subprocess, "Popen", Process)
    monkeypatch.setattr(tunnel.socket, "create_connection", lambda *a, **k: types.SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", open_response)
    return state


def test_страница_вместо_ответа_проверки_отклоняется(proxy_network):
    link = buildconfig.new_link()
    proxy_network.statuses[link["port"]] = 200

    assert not probe.proxy_works(link)


def test_импорт_сохраняет_только_рабочие_и_убирает_процессы(proxy_network, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "SUBSCRIPTIONS_JSON", str(tmp_path / "subscriptions.json"))
    monkeypatch.setattr(paths, "TUNNELS_JSON", str(tmp_path / "tunnels.json"))
    core = service.Core.__new__(service.Core)
    core.log = lambda text: None
    core.tunnel = tunnel.Tunnel(core.log)
    links = "\n".join(f"socks5://203.0.113.1:{port}" for port in range(1, 20))

    reply = core._add_link(links + "\nbad\nsocks5://203.0.113.1:1")

    assert reply["ok"]
    assert reply["added"] == 10
    assert reply["failed"] == 9
    assert reply["invalid"] == reply["duplicates"] == 1
    assert len(subscriptions.list_profiles()) == 10
    assert tunnels.load()["tunnels"][0]["active"] == reply["profile"]
    assert [len(c["inbounds"]) for c in proxy_network.configs] == [16, 3]
    assert len(proxy_network.stopped) == 2
    assert not core.tunnel._trials
    assert not any(name.startswith("trial-proxy-") and name.endswith(".json") for name in os.listdir(paths.RUN))


def test_нерабочая_пачка_не_создаёт_профилей(proxy_network, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "SUBSCRIPTIONS_JSON", str(tmp_path / "subscriptions.json"))
    monkeypatch.setattr(paths, "TUNNELS_JSON", str(tmp_path / "tunnels.json"))
    core = service.Core.__new__(service.Core)
    core.log = lambda text: None
    core.tunnel = tunnel.Tunnel(core.log)

    reply = core._add_link("http://203.0.113.1:80")

    assert not reply["ok"]
    assert not subscriptions.list_profiles()
    assert not tunnels.load()["tunnels"]
    assert len(proxy_network.stopped) == 1


def test_долгая_проверка_прокси_не_блокирует_статус(proxy_network, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "SUBSCRIPTIONS_JSON", str(tmp_path / "subscriptions.json"))
    monkeypatch.setattr(paths, "TUNNELS_JSON", str(tmp_path / "tunnels.json"))
    monkeypatch.setattr(service.Core, "_singbox_cached", "test")
    core = service.Core()
    core.log = lambda text: None
    core.tunnel.log = core.log
    entered = threading.Event()
    release = threading.Event()
    open_response = urllib.request.OpenerDirector.open

    def slow_response(opener, request, timeout):
        entered.set()
        assert release.wait(10)
        return open_response(opener, request, timeout)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", slow_response)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        imported = pool.submit(core.handle, "add-link", {"link": "socks5://203.0.113.1:1081"}, True)
        try:
            assert entered.wait(5)
            status = pool.submit(core.handle, "status", {}, False).result(timeout=2)
            assert status["ok"]
            assert not imported.done()
        finally:
            release.set()
        assert imported.result(timeout=5)["ok"]
