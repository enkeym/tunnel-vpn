import base64
import json
import os
from urllib.parse import quote, urlencode

import pytest

from tunnelvpn import netguard, paths, subscriptions, vless

UUID1 = "00000000-0000-4000-8000-000000000001"
UUID2 = "00000000-0000-4000-8000-000000000002"
UUID3 = "00000000-0000-4000-8000-000000000003"
HOST = "vpn.example.test"


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "SUBSCRIPTIONS_JSON",
                        str(tmp_path / "subscriptions.json"))


def _link(uuid=UUID1, name="Node", path="/ws", host=HOST):
    query = {"type": "ws", "security": "tls", "encryption": "none",
             "path": path, "host": host, "sni": host, "fp": "chrome"}
    return (f"vless://{uuid}@{host}:443?{urlencode(query)}"
            f"#{quote(name)}")


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _b64url(text):
    return (base64.urlsafe_b64encode(text.encode("utf-8"))
            .decode("ascii").rstrip("="))


def _singbox(name="Node", uuid=UUID1, path="/ws"):
    return json.dumps({"outbounds": [{
        "type": "vless", "tag": name, "server": HOST, "server_port": 443,
        "uuid": uuid,
        "tls": {"enabled": True, "server_name": HOST,
                "utls": {"enabled": True, "fingerprint": "chrome"}},
        "transport": {"type": "ws", "path": path,
                      "headers": {"Host": HOST}},
    }]})


def _fake_open(monkeypatch, body):
    calls = []

    def fake(url, allow_private=False, timeout=None, max_bytes=None):
        calls.append((url, allow_private))
        return body

    monkeypatch.setattr(netguard, "open_https", fake)
    return calls


def _add_subscription(monkeypatch, body, **kwargs):
    _fake_open(monkeypatch, body)
    return subscriptions.add("https://sub.example.test/sub", **kwargs)


def _raw_sub():
    return {"id": "s_0123456789abcdef", "name": "S",
            "url": "https://sub.example.test/sub", "kind": "uri-list",
            "allow_private": False, "updated_at": "", "last_error": ""}


def _raw_profile():
    profile = vless.parse_link(_link())
    return {"id": "p_0123456789abcdef", "name": profile["name"],
            "proto": "vless", "fingerprint": "ignored",
            "vless": profile["vless"], "source": {"manual": True},
            "missing": False}


def test_пустое_хранилище():
    assert subscriptions.load() == subscriptions.empty()


def test_сохранение_и_чтение():
    data = subscriptions.empty()

    subscriptions.save(data)

    assert subscriptions.load() == data
    assert not os.path.exists(paths.SUBSCRIPTIONS_JSON + ".tmp")
    with open(paths.SUBSCRIPTIONS_JSON, encoding="utf-8") as fh:
        assert json.load(fh) == data


def test_битый_файл():
    os.makedirs(paths.CONF, exist_ok=True)
    with open(paths.SUBSCRIPTIONS_JSON, "w", encoding="utf-8") as fh:
        fh.write("{")

    with pytest.raises(ValueError, match="не читается"):
        subscriptions.load()


@pytest.mark.parametrize("body,kind", [
    (_link(), "uri-list"),
    (_link() + "\n" + _link(uuid=UUID2, path="/b"), "uri-list"),
    (_b64(_link()), "base64"),
    (_b64url(_link()), "base64"),
    (_b64(_singbox()), "base64"),
    (_singbox(), "singbox"),
])
def test_определение_формата(body, kind):
    assert subscriptions.detect_kind(body) == kind


@pytest.mark.parametrize("body", ["", "   ", "hello", b"\xff\xfe"])
def test_неизвестный_формат(body):
    with pytest.raises(ValueError):
        subscriptions.detect_kind(body)


def test_uri_list_пропускает_комментарии_и_дубли():
    body = "\n".join(["# comment", "", _link(name="One"),
                      _link(name="Two"),
                      _link(uuid=UUID2, path="/b", name="Other")])

    profiles = subscriptions.parse_body("uri-list", body)

    assert [item["name"] for item in profiles] == ["One", "Other"]


def test_uri_list_отклоняет_чужую_ссылку():
    body = _link() + "\nss://something"

    with pytest.raises(ValueError, match="строка 2"):
        subscriptions.parse_body("uri-list", body)


def test_uri_list_больше_потолка_профилей_отклоняется(monkeypatch):
    """Потолок общий для форматов: иначе 1 МиБ тела дал бы тысячи профилей."""
    monkeypatch.setattr(vless, "MAX_PROFILES", 2)
    body = "\n".join(_link(name=f"N{i}", path=f"/{i}") for i in range(3))

    with pytest.raises(ValueError, match="профилей больше 2"):
        subscriptions.parse_body("uri-list", body)


def test_base64_с_переносами():
    body = _b64(_link())
    spaced = body[:10] + "\n" + body[10:]

    assert len(subscriptions.parse_body("base64", spaced)) == 1


def test_base64_json():
    profiles = subscriptions.parse_body("base64", _b64(_singbox(name="JSON")))

    assert profiles[0]["name"] == "JSON"


def test_ссылка_и_json_бота_дают_одинаковые_поля():
    from_link = subscriptions.parse_body("base64", _b64(_link()))[0]
    from_json = subscriptions.parse_body("singbox", _singbox())[0]

    assert from_link["vless"] == from_json["vless"]


def test_имя_профиля_с_кириллицей_сохраняется():
    name = "Токио · Cloudflare"
    profile = subscriptions.add_profile(_link(name=name))

    assert subscriptions.find_profile(subscriptions.load(),
                                      profile["id"])["name"] == name


def test_singbox_тело():
    profiles = subscriptions.parse_body("singbox", _singbox(name="JSON"))

    assert profiles[0]["vless"]["server"] == HOST


def test_auto_формат():
    profiles = subscriptions.parse_body("auto", _b64(_link()))

    assert profiles[0]["name"] == "Node"


def test_неизвестный_формат_тела():
    with pytest.raises(ValueError):
        subscriptions.parse_body("plain", _link())


def test_добавление_профиля_и_дубли():
    first = subscriptions.add_profile(_link(name="One"))
    second = subscriptions.add_profile(_link(name="Two"))
    data = subscriptions.load()

    assert second["id"] == first["id"]
    assert len(data["profiles"]) == 1
    assert data["profiles"][0]["source"] == {"manual": True}
    assert data["profiles"][0]["proto"] == "vless"
    assert data["profiles"][0]["fingerprint"] == vless.fingerprint(
        vless.parse_link(_link()))


def test_добавление_профиля_снимает_missing():
    subscriptions.add_profile(_link())
    data = subscriptions.load()
    data["profiles"][0]["missing"] = True
    subscriptions.save(data)

    again = subscriptions.add_profile(_link())

    assert again["missing"] is False


def test_добавление_подписки(monkeypatch):
    body = _link(name="A") + "\n" + _link(uuid=UUID2, path="/b", name="B")
    calls = _fake_open(monkeypatch, body)

    sub = subscriptions.add("https://sub.example.test/sub", name="Моя",
                            allow_private=True)

    data = subscriptions.load()
    assert sub["kind"] == "uri-list"
    assert sub["name"] == "Моя"
    assert sub["allow_private"] is True
    assert sub["last_error"] == ""
    assert calls == [("https://sub.example.test/sub", True)]
    assert len(data["subscriptions"]) == 1
    assert {item["source"]["subscription"]
            for item in data["profiles"]} == {sub["id"]}


def test_добавление_base64_подписки(monkeypatch):
    _fake_open(monkeypatch, _b64(_link()))

    sub = subscriptions.add("https://sub.example.test/b64")

    assert sub["kind"] == "base64"
    assert len(subscriptions.load()["profiles"]) == 1


def test_добавление_singbox_подписки(monkeypatch):
    _fake_open(monkeypatch, _singbox(name="JSON"))

    sub = subscriptions.add("https://sub.example.test/json")

    assert sub["kind"] == "singbox"
    assert subscriptions.load()["profiles"][0]["name"] == "JSON"


def test_full_не_скачивается(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("не должен скачивать")

    monkeypatch.setattr(netguard, "open_https", fail)

    with pytest.raises(ValueError, match="full"):
        subscriptions.add("https://sub.example.test/full?token=1")


def test_неизвестный_вид_подписки():
    with pytest.raises(ValueError):
        subscriptions.add("https://sub.example.test/x", kind="plain")


def test_allow_private_должен_быть_bool():
    with pytest.raises(ValueError):
        subscriptions.add("https://sub.example.test/x", allow_private="yes")


def test_свой_бот_явно_просит_base64(monkeypatch):
    calls = _fake_open(monkeypatch, _b64(_link()))
    url = f"https://vpn.example.test/sub/{UUID1}?token=abc"

    sub = subscriptions.add(url)

    assert calls == [(f"{url}&format=base64", False)]
    assert sub["url"] == url


def test_явный_формат_и_чужой_путь_не_переписываются(monkeypatch):
    calls = _fake_open(monkeypatch, _b64(_link()))

    subscriptions.add(f"https://vpn.example.test/sub/{UUID1}?format=singbox")
    subscriptions.add("https://sub.example.test/sub/not-a-uuid")

    assert calls == [(f"https://vpn.example.test/sub/{UUID1}?format=singbox", False),
                     ("https://sub.example.test/sub/not-a-uuid", False)]


def test_обновление_своего_бота_просит_base64_и_помнит_адрес(monkeypatch):
    url = f"https://vpn.example.test/sub/{UUID1}"
    _fake_open(monkeypatch, _b64(_link()))
    sub = subscriptions.add(url)
    calls = _fake_open(monkeypatch, _b64(_link()))

    subscriptions.refresh(sub["id"])

    assert calls == [(f"{url}?format=base64", False)]
    assert subscriptions.load()["subscriptions"][0]["url"] == url


def test_предпросмотр_подписки_ничего_не_пишет(monkeypatch):
    body = _b64(_link(name="A") + "\n" + _link(uuid=UUID2, path="/b", name="B"))
    _fake_open(monkeypatch, body)

    got = subscriptions.preview("https://sub.example.test/sub", name="Моя")

    assert got == {"kind": "base64", "name": "Моя",
                   "host": "sub.example.test", "port": 443, "profiles": 2}
    assert subscriptions.load() == subscriptions.empty()


def test_предпросмотр_не_скачивает_full(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("не должен скачивать")

    monkeypatch.setattr(netguard, "open_https", fail)

    with pytest.raises(ValueError, match="full"):
        subscriptions.preview("https://sub.example.test/sub/x/full")


def test_refresh_добавляет_обновляет_и_помечает_пропавшие(monkeypatch):
    first = _link(name="A") + "\n" + _link(uuid=UUID2, path="/b", name="B")
    sub = _add_subscription(monkeypatch, first)

    second = _link(name="A new") + "\n" + _link(uuid=UUID3, path="/c",
                                                name="C")
    _fake_open(monkeypatch, second)
    result = subscriptions.refresh(sub["id"])

    assert result["added"] == 1
    assert result["updated"] == 1
    assert result["missing"] == 1
    by_name = {item["name"]: item for item in subscriptions.load()["profiles"]}
    assert by_name["A new"]["missing"] is False
    assert by_name["B"]["missing"] is True
    assert by_name["C"]["missing"] is False


def test_refresh_возвращает_профиль(monkeypatch):
    first = _link(name="A") + "\n" + _link(uuid=UUID2, path="/b", name="B")
    sub = _add_subscription(monkeypatch, first)
    _fake_open(monkeypatch, _link(name="A"))
    subscriptions.refresh(sub["id"])

    _fake_open(monkeypatch, first)
    result = subscriptions.refresh(sub["id"])

    assert result["added"] == 0
    assert result["missing"] == 0
    by_name = {item["name"]: item for item in subscriptions.load()["profiles"]}
    assert by_name["B"]["missing"] is False


def test_refresh_запоминает_ошибку(monkeypatch):
    sub = _add_subscription(monkeypatch, _link())

    def fail(*args, **kwargs):
        raise ValueError("сеть недоступна")

    monkeypatch.setattr(netguard, "open_https", fail)

    with pytest.raises(ValueError, match="сеть недоступна"):
        subscriptions.refresh(sub["id"])

    data = subscriptions.load()
    assert subscriptions.find(data, sub["id"])["last_error"] == (
        "сеть недоступна")
    assert len(data["profiles"]) == 1


def test_refresh_неизвестной_подписки():
    with pytest.raises(ValueError, match="нет подписки"):
        subscriptions.refresh("s_0123456789abcdef")


def test_delete_удаляет_профили_подписки(monkeypatch):
    manual = subscriptions.add_profile(_link(uuid=UUID2, path="/m",
                                             name="M"))
    sub = _add_subscription(monkeypatch, _link(name="A"))

    assert subscriptions.delete(sub["id"]) is True

    data = subscriptions.load()
    assert data["subscriptions"] == []
    assert [item["id"] for item in data["profiles"]] == [manual["id"]]


def test_remove_profile():
    profile = subscriptions.add_profile(_link())

    assert subscriptions.remove_profile(profile["id"]) is True
    assert subscriptions.load()["profiles"] == []
    with pytest.raises(ValueError, match="нет профиля"):
        subscriptions.remove_profile(profile["id"])


def test_set_allow_private(monkeypatch):
    sub = _add_subscription(monkeypatch, _link())

    assert subscriptions.set_allow_private(
        sub["id"], True)["allow_private"] is True
    assert subscriptions.load()["subscriptions"][0]["allow_private"] is True
    with pytest.raises(ValueError):
        subscriptions.set_allow_private(sub["id"], 1)
    with pytest.raises(ValueError, match="нет подписки"):
        subscriptions.set_allow_private("s_0123456789abcdef", True)


def test_list_profiles(monkeypatch):
    manual = subscriptions.add_profile(_link(uuid=UUID2, path="/m",
                                             name="M"))
    sub = _add_subscription(monkeypatch, _link(name="A"))

    inside = subscriptions.list_profiles(sub["id"])

    assert [item["id"] for item in inside] != [manual["id"]]
    assert len(subscriptions.list_profiles()) == 2
    with pytest.raises(ValueError, match="нет подписки"):
        subscriptions.list_profiles("s_0123456789abcdef")


def test_validate_отклоняет_повтор_отпечатка():
    profile = subscriptions.add_profile(_link())
    data = subscriptions.load()
    data["profiles"].append(dict(profile, id="p_0123456789abcdef"))

    with pytest.raises(ValueError, match="отпечат"):
        subscriptions.validate(data)


def test_validate_отклоняет_неизвестную_подписку():
    subscriptions.add_profile(_link())
    data = subscriptions.load()
    data["profiles"][0]["source"] = {"subscription": "s_0123456789abcdef"}

    with pytest.raises(ValueError, match="неизвестную"):
        subscriptions.validate(data)


@pytest.mark.parametrize("data", [
    [],
    {"version": 2},
    {"version": 1, "subscriptions": "нет"},
    {"version": 1, "profiles": "нет"},
])
def test_validate_отклоняет_структуру(data):
    with pytest.raises(ValueError):
        subscriptions.validate(data)


@pytest.mark.parametrize("change", [
    {"id": "x"},
    {"url": "http://sub.example.test/sub"},
    {"kind": "plain"},
    {"allow_private": "yes"},
    {"updated_at": "не дата"},
    {"last_error": 5},
])
def test_validate_отклоняет_подписку(change):
    sub = _raw_sub()
    sub.update(change)

    with pytest.raises(ValueError):
        subscriptions.validate({"version": 1, "subscriptions": [sub],
                                "profiles": []})


def test_validate_отклоняет_повтор_подписки():
    sub = _raw_sub()

    with pytest.raises(ValueError, match="повторяются"):
        subscriptions.validate({"version": 1,
                                "subscriptions": [sub, dict(sub)],
                                "profiles": []})


@pytest.mark.parametrize("change", [
    {"id": "x"},
    {"proto": "wg"},
    {"vless": None},
    {"missing": "no"},
    {"source": {}},
])
def test_validate_отклоняет_профиль(change):
    profile = _raw_profile()
    profile.update(change)

    with pytest.raises(ValueError):
        subscriptions.validate({"version": 1, "subscriptions": [],
                                "profiles": [profile]})
