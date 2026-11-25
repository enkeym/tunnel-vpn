import base64
import json
import os
from urllib.parse import quote, urlencode

import pytest

from tunnelvpn import happ, netguard, paths, proxy, subscriptions, vless

UUID1 = "00000000-0000-4000-8000-000000000001"
UUID2 = "00000000-0000-4000-8000-000000000002"
UUID3 = "00000000-0000-4000-8000-000000000003"
UUID4 = "00000000-0000-4000-8000-000000000004"
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


def _reality_link(sid="a1b2"):
    key = base64.urlsafe_b64encode(bytes([1] * 32)).decode("ascii").rstrip("=")
    query = {"encryption": "none", "security": "reality", "type": "tcp",
             "sni": HOST, "fp": "chrome", "pbk": key, "sid": sid,
             "spx": "/random-path", "support-x25519mlkem768": "true"}
    return f"vless://{UUID2}@{HOST}:443?{urlencode(query)}#Reality"


def _hysteria2_link():
    query = {"security": "tls", "sni": HOST, "alpn": "h3", "fp": "chrome",
             "obfs": "salamander", "obfs-password": "obfs-secret"}
    return f"hysteria2://pass%40word@{HOST}:443?{urlencode(query)}#Hysteria2"


def test_смешанная_base64_подписка_сохраняет_оба_протокола(monkeypatch):
    body = _b64("\n".join((_reality_link(), _hysteria2_link())))
    _fake_open(monkeypatch, body)

    sub = subscriptions.add("https://sub.example.test/sub")
    profiles = subscriptions.load()["profiles"]

    assert sub["kind"] == "base64"
    assert [p["proto"] for p in profiles] == ["vless", "hysteria2"]
    assert profiles[0]["vless"]["security"] == "reality"
    assert profiles[1]["hysteria2"]["password"] == "pass@word"
    assert subscriptions.refresh(sub["id"])["total"] == 2
    assert [proxy.parse_link(proxy.canonical(p))["proto"] for p in profiles] == [
        "vless", "hysteria2"]


def test_неверный_reality_ключ_отклоняется():
    with pytest.raises(ValueError, match="public key"):
        subscriptions.parse_body("uri-list", _reality_link().replace("pbk=", "pbk=x"))


def _hysteria2_userpass_link(insecure="0"):
    query = {"sni": HOST, "insecure": insecure, "obfs": "salamander",
             "obfs-password": "obfs-secret"}
    return f"hysteria2://Peer:peer-secret@{HOST}:443/?{urlencode(query)}#Peer"


def test_подписка_vpn_new_hysteria2_логин_пароль():
    body = _b64(_hysteria2_userpass_link())
    profiles = subscriptions.parse_body("base64", body)

    assert [p["proto"] for p in profiles] == ["hysteria2"]
    assert profiles[0]["hysteria2"]["password"] == "Peer:peer-secret"
    assert profiles[0]["hysteria2"]["sni"] == HOST
    again = proxy.parse_link(proxy.canonical(profiles[0]))
    assert again["hysteria2"] == profiles[0]["hysteria2"]


def test_hysteria2_insecure_отклоняется():
    with pytest.raises(ValueError, match="проверкой сертификата"):
        subscriptions.parse_body("uri-list", _hysteria2_userpass_link("1"))


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


def test_happ_подписка_сохраняет_исходную_ссылку_и_обновляется(monkeypatch):
    link = "happ://crypt5/AbCd1234="
    decoded = "https://sub.example.test/sub"
    calls = _fake_open(monkeypatch, _link())
    decoded_links = []

    def decrypt(value):
        decoded_links.append(value)
        return decoded

    monkeypatch.setattr(happ, "decrypt", decrypt)

    sub = subscriptions.add(link)
    assert sub["url"] == link
    assert sub["name"] == "Happ"
    assert subscriptions.preview(link)["profiles"] == 1
    assert subscriptions.refresh(sub["id"])["total"] == 1
    assert decoded_links == [link, link, link]
    assert [url for url, _ in calls] == [decoded, decoded, decoded]
    with pytest.raises(ValueError, match="уже добавлена"):
        subscriptions.add(link)


def test_happ_расшифрованный_адрес_проверяется(monkeypatch):
    monkeypatch.setattr(happ, "decrypt", lambda _: "http://127.0.0.1/sub")

    with pytest.raises(ValueError, match="только по HTTPS"):
        subscriptions.add("happ://crypt5/AbCd1234=")

    assert subscriptions.load() == subscriptions.empty()


@pytest.mark.parametrize("value", [
    "happ://crypt5/", "happ://crypt6/AAAA", "happ://crypt5/AA BB",
    "happ://crypt5/" + "a" * (happ.MAX_LINK + 1),
])
def test_happ_неверная_ссылка_отклоняется(value):
    with pytest.raises(ValueError, match="ожидается ссылка"):
        happ.check_link(value)


def _raw_profile():
    profile = vless.parse_link(_link())
    return {"id": "p_0123456789abcdef", "name": profile["name"],
            "proto": "vless", "fingerprint": "ignored",
            "vless": profile["vless"], "source": {"manual": True},
            "missing": False}


def test_массовый_импорт_отклоняет_неверную_строку_целиком():
    text = _link() + "\nnot-a-vless-link"

    with pytest.raises(ValueError, match="строка 2"):
        subscriptions.add_profiles(text)

    assert subscriptions.list_profiles() == []


def test_массовый_импорт_пропускает_уже_известные_профили():
    first = subscriptions.add_profile(_link())

    records, added = subscriptions.add_profiles(
        _link() + "\n" + _link(uuid=UUID2, name="Second"))

    assert added == 1
    assert records[0]["id"] == first["id"]
    assert len(subscriptions.list_profiles()) == 2


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


def test_чужой_ответ_не_называет_base64():
    with pytest.raises(ValueError, match="это не подписка"):
        subscriptions.detect_kind("<!doctype html><title>403</title>")


def test_happ_ссылка_в_поле_ссылки_vpn():
    with pytest.raises(ValueError, match="Happ-ссылка"):
        subscriptions.detect_kind("happ://crypt5/AbCd1234=")


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


def _fake_bodies(monkeypatch, bodies):
    calls = []

    def fake(url, allow_private=False, timeout=None, max_bytes=None):
        calls.append(url)
        return bodies[url]

    monkeypatch.setattr(netguard, "open_https", fake)
    return calls


def test_новая_ссылка_с_теми_же_профилями_заменяет_подписку(monkeypatch):
    old = "\n".join((_link(name="Германия"),
                     _link(uuid=UUID2, path="/b", name="Польша")))
    new = "\n".join((_link(uuid=UUID3, name="Германия"),
                     _link(uuid=UUID2, path="/b2", name="Польша")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": old,
                               "https://sub.example.test/new": new})

    first = subscriptions.add("https://sub.example.test/old", name="Riot")
    before = {p["name"]: p["id"] for p in subscriptions.load()["profiles"]}
    again = subscriptions.add("https://sub.example.test/new")

    data = subscriptions.load()
    assert again["id"] == first["id"]
    assert again["url"] == "https://sub.example.test/new"
    assert again["name"] == "Riot"
    assert len(data["subscriptions"]) == 1
    assert len(data["profiles"]) == 2
    assert {p["name"]: p["id"] for p in data["profiles"]} == before
    assert all(p["source"] == {"subscription": first["id"]}
               for p in data["profiles"])


def test_половина_общих_имён_уже_обновление(monkeypatch):
    old = "\n".join((_link(name="Германия"), _link(uuid=UUID2, name="Польша")))
    new = "\n".join((_link(uuid=UUID3, name="Германия"), _link(uuid=UUID2, name="Чехия")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": old,
                               "https://sub.example.test/new": new})

    first = subscriptions.add("https://sub.example.test/old")
    again = subscriptions.add("https://sub.example.test/new")

    assert again["id"] == first["id"]
    assert len(subscriptions.load()["subscriptions"]) == 1


def test_мало_общих_имён_остаётся_новой_подпиской(monkeypatch):
    old = "\n".join(_link(name=n, path=f"/{i}")
                    for i, n in enumerate(("Германия", "Польша", "Чехия")))
    new = "\n".join(_link(uuid=UUID3, name=n, path=f"/{i}")
                    for i, n in enumerate(("Германия", "Швеция", "Норвегия")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": old,
                               "https://sub.example.test/new": new})

    subscriptions.add("https://sub.example.test/old")
    again = subscriptions.add("https://sub.example.test/new")

    data = subscriptions.load()
    assert len(data["subscriptions"]) == 2
    assert again["url"] == "https://sub.example.test/new"


def test_другой_хост_не_заменяет_подписку(monkeypatch):
    old = _link(name="Германия")
    new = _link(uuid=UUID3, name="Германия", host="other.example.test")
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": old,
                               "https://other.example.test/new": new})

    subscriptions.add("https://sub.example.test/old")
    subscriptions.add("https://other.example.test/new")

    assert len(subscriptions.load()["subscriptions"]) == 2


def test_выбирается_подписка_с_большим_совпадением(monkeypatch):
    first = "\n".join((_link(name="A", path="/a"),
                       _link(uuid=UUID2, name="B", path="/b")))
    second = "\n".join((_link(uuid=UUID3, name="C", path="/c"),
                        _link(uuid=UUID1, name="D", path="/d")))
    new = "\n".join((_link(uuid=UUID3, name="C", path="/c"),
                     _link(uuid=UUID1, name="D", path="/d"),
                     _link(uuid=UUID2, name="E", path="/e")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/one": first,
                               "https://sub.example.test/two": second,
                               "https://sub.example.test/new": new})

    one = subscriptions.add("https://sub.example.test/one")
    two = subscriptions.add("https://sub.example.test/two")
    again = subscriptions.add("https://sub.example.test/new")

    assert again["id"] == two["id"] != one["id"]
    assert len(subscriptions.load()["subscriptions"]) == 2


def _touch_sub(sid, updated_at):
    data = subscriptions.load()
    next(s for s in data["subscriptions"] if s["id"] == sid)["updated_at"] = updated_at
    subscriptions.save(data)


def _rename_profiles(sid, names):
    data = subscriptions.load()
    mine = [p for p in data["profiles"]
            if p["source"] == {"subscription": sid}]
    for record, name in zip(mine, names):
        record["name"] = name
    subscriptions.save(data)


def _clone_sub(sid, url, updated_at, uuid, serial):
    data = subscriptions.load()
    src = next(s for s in data["subscriptions"] if s["id"] == sid)
    mine = [p for p in data["profiles"]
            if p["source"] == {"subscription": sid}]
    new_id = f"s_{serial:016x}"
    data["subscriptions"].append(dict(src, id=new_id, url=url,
                                      updated_at=updated_at))
    for i, record in enumerate(mine):
        data["profiles"].append(
            dict(record, id=f"p_{(serial << 8) + i:016x}",
                 source={"subscription": new_id},
                 **{record["proto"]: dict(record[record["proto"]], uuid=uuid)}))
    subscriptions.save(data)
    return new_id


def test_дубли_подписок_оставляют_самую_свежую(monkeypatch):
    old = "\n".join((_link(name="Германия"),
                     _link(uuid=UUID2, name="Польша", path="/b")))
    other = "\n".join((_link(uuid=UUID3, name="США", path="/c"),
                       _link(uuid=UUID3, name="Канада", path="/d")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": old,
                               "https://sub.example.test/other": other})

    first = subscriptions.add("https://sub.example.test/old")
    kept_other = subscriptions.add("https://sub.example.test/other")
    fresh = _clone_sub(first["id"], "https://sub.example.test/old-new",
                       "2999-01-01T00:00:00+00:00", UUID4, 0xb0)

    result = subscriptions.dedupe()

    data = subscriptions.load()
    assert result["removed"] == [first["id"]]
    assert result["kept"] == [fresh]
    assert {s["id"] for s in data["subscriptions"]} == {fresh, kept_other["id"]}
    assert len(data["profiles"]) == 4
    assert all(p["source"] == {"subscription": fresh}
               for p in data["profiles"] if p["name"] in ("Германия", "Польша"))


def test_ровно_половина_общих_имён_это_дубль(monkeypatch):
    first = "\n".join(_link(name=n, path=f"/{i}")
                      for i, n in enumerate(("A1", "A2", "A3", "A4")))
    second = "\n".join(_link(uuid=UUID2, name=n, path=f"/b{i}")
                       for i, n in enumerate(("B1", "B2", "B3", "B4")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": first,
                               "https://sub.example.test/new": second})

    old = subscriptions.add("https://sub.example.test/old")
    new = subscriptions.add("https://sub.example.test/new")
    _rename_profiles(new["id"], ("A1", "A2", "B3", "B4"))
    _touch_sub(old["id"], "2026-01-01T00:00:00+00:00")
    _touch_sub(new["id"], "2026-02-01T00:00:00+00:00")

    result = subscriptions.dedupe()

    data = subscriptions.load()
    assert result["removed"] == [old["id"]]
    assert [s["id"] for s in data["subscriptions"]] == [new["id"]]


def test_меньше_половины_общих_имён_не_дубль(monkeypatch):
    first = "\n".join(_link(name=n, path=f"/{i}")
                      for i, n in enumerate(("A1", "A2", "A3", "A4")))
    second = "\n".join(_link(uuid=UUID2, name=n, path=f"/b{i}")
                       for i, n in enumerate(("B1", "B2", "B3", "B4")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": first,
                               "https://sub.example.test/new": second})

    subscriptions.add("https://sub.example.test/old")
    new = subscriptions.add("https://sub.example.test/new")
    _rename_profiles(new["id"], ("A1", "B2", "B3", "B4"))

    result = subscriptions.dedupe()

    assert result["removed"] == []
    assert len(subscriptions.load()["subscriptions"]) == 2


def test_дубли_на_разных_хостах_не_удаляются(monkeypatch):
    same = "\n".join((_link(name="Германия"),
                      _link(uuid=UUID2, name="Польша", path="/b")))
    other = "\n".join((_link(uuid=UUID3, name="Германия", host="other.example.test"),
                       _link(uuid=UUID2, name="Польша", path="/b",
                             host="other.example.test")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": same,
                               "https://other.example.test/new": other})

    first = subscriptions.add("https://sub.example.test/old")
    second = subscriptions.add("https://other.example.test/new")
    fresh = _clone_sub(first["id"], "https://sub.example.test/old-new",
                       "2999-01-01T00:00:00+00:00", UUID4, 0xb1)

    result = subscriptions.dedupe()

    assert result["removed"] == [first["id"]]
    assert {s["id"] for s in subscriptions.load()["subscriptions"]} == {
        fresh, second["id"]}


def test_повторная_чистка_ничего_не_удаляет(monkeypatch):
    body = "\n".join((_link(name="Германия"),
                      _link(uuid=UUID2, name="Польша", path="/b")))
    _fake_bodies(monkeypatch, {"https://sub.example.test/old": body})

    first = subscriptions.add("https://sub.example.test/old")
    _clone_sub(first["id"], "https://sub.example.test/old-new",
               "2999-01-01T00:00:00+00:00", UUID4, 0xb2)

    subscriptions.dedupe()
    before = subscriptions.load()
    again = subscriptions.dedupe()

    assert again == {"removed": [], "kept": []}
    assert subscriptions.load() == before


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


def test_riotvpn_подписка_явно_просит_base64(monkeypatch):
    calls = _fake_open(monkeypatch, _b64(_link()))
    url = "https://riotvpn.eu/sub/heP0enQfNn1P6vPV"

    subscriptions.add(url)

    assert calls == [(f"{url}?format=base64", False)]


def test_предпросмотр_riotvpn_явно_просит_base64(monkeypatch):
    calls = _fake_open(monkeypatch, _b64(_link()))
    url = "https://riotvpn.eu/sub/heP0enQfNn1P6vPV"

    result = subscriptions.preview(url)

    assert result["profiles"] == 1
    assert calls == [(f"{url}?format=base64", False)]


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


def test_refresh_обновляет_reality_при_смене_short_id(monkeypatch):
    sub = _add_subscription(monkeypatch, _reality_link("a1b2"))
    before = subscriptions.load()["profiles"]

    _fake_open(monkeypatch, _reality_link("c3d4"))
    result = subscriptions.refresh(sub["id"])

    after = subscriptions.load()["profiles"]
    assert result["added"] == 0
    assert result["updated"] == 1
    assert result["missing"] == 0
    assert [p["id"] for p in after] == [p["id"] for p in before]
    assert after[0]["vless"]["short_id"] == "c3d4"
    assert after[0]["missing"] is False


def test_refresh_убирает_неиспользуемые_дубли_reality(monkeypatch):
    sub = _add_subscription(monkeypatch, _reality_link("a1b2"))
    data = subscriptions.load()
    base = data["profiles"][0]
    base["missing"] = True
    for pid, sid in (("p_0000000000000001", "c3d4"),
                     ("p_0000000000000002", "e5f6")):
        duplicate = json.loads(json.dumps(base))
        duplicate["id"] = pid
        duplicate["vless"]["short_id"] = sid
        duplicate["missing"] = True
        duplicate["fingerprint"] = proxy.fingerprint(duplicate)
        data["profiles"].append(duplicate)
    subscriptions.save(data)

    _fake_open(monkeypatch, _reality_link("1122"))
    result = subscriptions.refresh(sub["id"], keep=[base["id"]])

    after = subscriptions.load()["profiles"]
    assert result["added"] == 0
    assert result["updated"] == 1
    assert result["missing"] == 2
    assert [p["id"] for p in after] == [base["id"]]
    assert after[0]["vless"]["short_id"] == "1122"
    assert after[0]["missing"] is False


def test_refresh_предпочитает_используемый_профиль_совпавшему_дублю(monkeypatch):
    sub = _add_subscription(monkeypatch, _reality_link("a1b2"))
    data = subscriptions.load()
    base = data["profiles"][0]
    base["missing"] = True
    duplicate = json.loads(json.dumps(base))
    duplicate["id"] = "p_0000000000000003"
    duplicate["vless"]["short_id"] = "c3d4"
    duplicate["missing"] = False
    duplicate["fingerprint"] = proxy.fingerprint(duplicate)
    data["profiles"].append(duplicate)
    subscriptions.save(data)

    _fake_open(monkeypatch, _reality_link("c3d4"))
    result = subscriptions.refresh(sub["id"], keep=[base["id"]])

    after = subscriptions.load()["profiles"]
    assert result["added"] == 0
    assert result["updated"] == 1
    assert [p["id"] for p in after] == [base["id"]]
    assert after[0]["vless"]["short_id"] == "c3d4"
    assert after[0]["missing"] is False


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


@pytest.mark.parametrize("manual", [True, False])
def test_refresh_не_дублирует_профиль_другого_источника(monkeypatch, manual):
    if manual:
        owner = subscriptions.add_profile(_link())
    else:
        _add_subscription(monkeypatch, _link())
        owner = subscriptions.load()["profiles"][0]
    _fake_open(monkeypatch, _link() + "\n" + _link(uuid=UUID2))
    sub = subscriptions.add("https://another.example.test/sub")
    before = subscriptions.load()["profiles"]

    result = subscriptions.refresh(sub["id"])

    after = subscriptions.load()["profiles"]
    assert result == {"id": sub["id"], "added": 0, "updated": 1,
                      "missing": 0, "total": 1}
    assert after == before
    assert after[0] == owner


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
