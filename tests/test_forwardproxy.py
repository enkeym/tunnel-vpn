import base64
import contextlib
import json
import os
import queue
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
        assert request._tunnel_host in ("cp.cloudflare.com",
                                        "connectivitycheck.gstatic.com")
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


def _core():
    core = service.Core.__new__(service.Core)
    core.log = lambda text: None
    core.tunnel = tunnel.Tunnel(core.log)
    core.prober = probe.Prober()
    core._jobs = {}
    core._jobs_lock = threading.Lock()
    core._jobs_done = {}
    core._job_seq = 0
    core._job_queue = queue.Queue()
    core._job_worker = None
    return core


def test_страница_вместо_ответа_проверки_отклоняется(proxy_network):
    link = buildconfig.new_link()
    proxy_network.statuses[link["port"]] = 200

    assert not probe.proxy_works(link)


def test_проверка_прокси_висячий_адрес_не_держит_ответивший(proxy_network, monkeypatch):
    link = buildconfig.new_link()
    proxy_network.statuses[link["port"]] = 204
    open_response = urllib.request.OpenerDirector.open
    release = threading.Event()

    def hanging(opener, request, timeout):
        if request._tunnel_host == "cp.cloudflare.com":
            release.wait(30)
            raise urllib.error.URLError("dead")
        return open_response(opener, request, timeout)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", hanging)
    got = []
    t = threading.Thread(target=lambda: got.append(probe.proxy_works(link)), daemon=True)
    t.start()
    try:
        t.join(5)
        assert not t.is_alive() and got == [True]
    finally:
        release.set()


def test_сбой_проверки_одной_ссылки_не_роняет_остальные(proxy_network, monkeypatch):
    core = _core()
    asked = []

    def flaky(link):
        asked.append(link["port"])
        if len(asked) == 1:
            raise RuntimeError("сбой")
        return True

    monkeypatch.setattr(probe, "proxy_works", flaky)
    profiles = [proxy.parse_link(f"socks5://203.0.113.1:{port}") for port in (8081, 8082, 8083)]

    working = core.tunnel.check_proxies(profiles)

    assert len(working) == 2 and len(asked) == 3


def test_импорт_сохраняет_сразу_и_убирает_нерабочие_в_фоне(proxy_network, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "SUBSCRIPTIONS_JSON", str(tmp_path / "subscriptions.json"))
    monkeypatch.setattr(paths, "TUNNELS_JSON", str(tmp_path / "tunnels.json"))
    core = _core()
    links = "\n".join(f"socks5://203.0.113.1:{port}" for port in range(1, 20))

    reply = core._add_link(links + "\nbad\nsocks5://203.0.113.1:1")

    assert reply["ok"] and reply["checking"] and reply["job"]
    assert reply["added"] == 19
    assert reply["invalid"] == reply["duplicates"] == 1
    job = core._job_wait(reply["job"], timeout=10)
    assert job["state"] == "done" and job["added"] == 10 and job["failed"] == 9
    assert len(job["problems"]) == 9
    assert len(subscriptions.list_profiles()) == 10
    assert tunnels.load()["tunnels"][0]["active"] == reply["profile"]
    assert [len(c["inbounds"]) for c in proxy_network.configs] == [16, 3]
    assert len(proxy_network.stopped) == 2
    assert not core.tunnel._trials
    assert not any(name.startswith("trial-proxy-") and name.endswith(".json") for name in os.listdir(paths.RUN))


def test_нерабочая_пачка_не_оставляет_ни_профилей_ни_туннелей(proxy_network, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "SUBSCRIPTIONS_JSON", str(tmp_path / "subscriptions.json"))
    monkeypatch.setattr(paths, "TUNNELS_JSON", str(tmp_path / "tunnels.json"))
    core = _core()

    reply = core._add_link("http://203.0.113.1:80")

    assert reply["ok"] and reply["checking"]
    job = core._job_wait(reply["job"], timeout=10)
    assert job["state"] == "done" and job["failed"] == 1 and job["added"] == 0
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

    reply = core.handle("add-link", {"link": "socks5://203.0.113.1:1081"}, True)

    assert reply["ok"] and reply["checking"] and reply["job"]
    assert entered.wait(5)
    status = core.handle("status", {}, False)
    assert status["ok"]
    release.set()
    job = core._job_wait(reply["job"], timeout=10)
    assert job["state"] == "done" and not job["failed"]
    assert len(subscriptions.list_profiles()) == 1
