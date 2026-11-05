
import base64
import binascii
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from . import happ, netguard, paths, proxy, vless

VERSION = 1
KINDS = ("uri-list", "base64", "singbox")
ADAPTIVE_KINDS = ("auto",) + KINDS
NAME_MAX_LEN = 80

_SUB_ID = re.compile(r"^s_[0-9a-f]{16}$")
_PROFILE_ID = re.compile(r"^p_[0-9a-f]{16}$")
_BOT_SUB = re.compile(
    r"^/sub/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/?$",
    re.IGNORECASE)

LOCK = threading.RLock()


def empty():
    return {"version": VERSION, "subscriptions": [], "profiles": []}


def _check_sub_id(sid):
    if not isinstance(sid, str) or not _SUB_ID.match(sid):
        raise ValueError(f"недопустимый id подписки: {sid!r}")
    return sid


def check_profile_id(pid):
    if not isinstance(pid, str) or not _PROFILE_ID.match(pid):
        raise ValueError(f"недопустимый id профиля: {pid!r}")
    return pid


def _check_kind(kind):
    if kind not in KINDS:
        raise ValueError(f"вид подписки {kind!r}, ожидаю один из {', '.join(KINDS)}")
    return kind


def _name(value, fallback=""):
    value = " ".join(value.split()) if isinstance(value, str) else ""
    return value[:NAME_MAX_LEN] or fallback


def _url_key(url):
    if happ.is_link(url):
        return happ.check_link(url)
    info = netguard.parse_url(url)
    return f"{info['host']}:{info['port']}{info['path']}"


def _fetch_url(url):
    parts = urlsplit(url)
    if not _BOT_SUB.match(parts.path):
        return url
    for pair in parts.query.split("&"):
        if unquote_plus(pair.split("=", 1)[0]).lower() == "format":
            return url
    query = parts.query + ("&" if parts.query else "") + "format=base64"
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       query, parts.fragment))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_time(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} должен быть строкой")
    if not value:
        return ""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field}: неверная дата {value!r}") from None
    return value


def _check_sub(raw):
    if not isinstance(raw, dict):
        raise ValueError("подписка должна быть объектом")
    sid = _check_sub_id(raw.get("id"))
    url = raw.get("url")
    if happ.is_link(url):
        url = happ.check_link(url)
        host = "Happ"
    else:
        info = netguard.parse_url(url)
        url = info["url"]
        host = info["host"]
    kind = _check_kind(raw.get("kind", "uri-list"))
    allow_private = raw.get("allow_private", False)
    if not isinstance(allow_private, bool):
        raise ValueError(f"подписка {sid}: allow_private должен быть true или false")
    last_error = raw.get("last_error", "")
    if not isinstance(last_error, str):
        raise ValueError(f"подписка {sid}: last_error должен быть строкой")
    return {"id": sid, "name": _name(raw.get("name"), host),
            "url": url, "kind": kind,
            "allow_private": allow_private,
            "updated_at": _check_time(raw.get("updated_at", ""), "updated_at"),
            "last_error": last_error}


def _source(raw, sub_ids, pid):
    source = raw.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"профиль {pid}: нет источника")
    if source == {"manual": True}:
        return {"manual": True}
    if set(source) == {"subscription"}:
        sid = source["subscription"]
        if sid not in sub_ids:
            raise ValueError(f"профиль {pid}: ссылается на неизвестную "
                             f"подписку {sid!r}")
        return {"subscription": sid}
    raise ValueError(f"профиль {pid}: неверный источник")


def _check_profile(raw, sub_ids):
    if not isinstance(raw, dict):
        raise ValueError("профиль должен быть объектом")
    pid = check_profile_id(raw.get("id"))
    profile = proxy.normalize(raw)
    missing = raw.get("missing", False)
    if not isinstance(missing, bool):
        raise ValueError(f"профиль {pid}: missing должен быть true или false")
    return {"id": pid, "name": profile["name"], "proto": profile["proto"],
            "fingerprint": proxy.fingerprint(profile),
            profile["proto"]: proxy.fields(profile),
            "source": _source(raw, sub_ids, pid), "missing": missing}


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("subscriptions.json: ожидаю объект")
    if data.get("version", VERSION) != VERSION:
        raise ValueError(f"subscriptions.json: версия {data.get('version')!r} "
                         f"не поддерживается")
    raw_subs = data.get("subscriptions", [])
    raw_profiles = data.get("profiles", [])
    if not isinstance(raw_subs, list):
        raise ValueError("subscriptions.json: subscriptions должен быть списком")
    if not isinstance(raw_profiles, list):
        raise ValueError("subscriptions.json: profiles должен быть списком")
    subs = [_check_sub(item) for item in raw_subs]
    sub_ids = {s["id"] for s in subs}
    if len(sub_ids) != len(subs):
        raise ValueError("subscriptions.json: повторяются id подписок")
    profiles = [_check_profile(item, sub_ids) for item in raw_profiles]
    if len({p["id"] for p in profiles}) != len(profiles):
        raise ValueError("subscriptions.json: повторяются id профилей")
    marks = [p["fingerprint"] for p in profiles]
    if len(set(marks)) != len(marks):
        raise ValueError("subscriptions.json: повторяются отпечатки профилей")
    return {"version": VERSION, "subscriptions": subs, "profiles": profiles}


def load():
    try:
        with LOCK, open(paths.SUBSCRIPTIONS_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return empty()
    except ValueError as exc:
        raise ValueError(f"subscriptions.json не читается: {exc}") from None
    return validate(data)


def save(data):
    data = validate(data)
    os.makedirs(paths.CONF, exist_ok=True)
    tmp = paths.SUBSCRIPTIONS_JSON + ".tmp"
    with LOCK:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, paths.SUBSCRIPTIONS_JSON)
    return data


def find(data, sid):
    return next((s for s in data["subscriptions"] if s["id"] == sid), None)


def find_profile(data, pid):
    return next((p for p in data["profiles"] if p["id"] == pid), None)


def _new_id(prefix, taken):
    for _ in range(100):
        value = prefix + secrets.token_hex(8)
        if value not in taken:
            taken.add(value)
            return value
    raise ValueError("не удалось выдать уникальный id")


def _decode(body):
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("подписка не в UTF-8") from None
    elif isinstance(body, str):
        text = body.lstrip("\ufeff")
    else:
        raise ValueError("тело подписки: ожидаю строку или байты")
    return text


def _decode_base64(text):
    compact = "".join(text.split())
    if not compact:
        raise ValueError("пустая base64-подписка")
    padded = compact + "=" * (-len(compact) % 4)
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("подписка не декодируется из base64") from None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("base64-подписка не в UTF-8") from None


def detect_kind(body):
    text = _decode(body)
    stripped = text.strip()
    if not stripped:
        raise ValueError("пустая подписка")
    if stripped.startswith("{"):
        return "singbox"
    if any(f"{proto}://" in stripped.lower() for proto in proxy.PROTOS):
        return "uri-list"
    decoded = _decode_base64(stripped)
    if decoded.strip().startswith("{") or any(
            f"{proto}://" in decoded.lower() for proto in proxy.PROTOS):
        return "base64"
    raise ValueError("неизвестный формат подписки")


def _parse_uri(text):
    profiles = []
    seen = set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            profile = proxy.parse_link(line)
        except ValueError as exc:
            raise ValueError(f"строка {number}: {exc}") from None
        mark = proxy.fingerprint(profile)
        if mark in seen:
            continue
        seen.add(mark)
        profiles.append(profile)
    if not profiles:
        raise ValueError("в подписке нет ссылок vless:// или hysteria2://")
    return profiles


def parse_body(kind, body):
    text = _decode(body)
    if kind == "auto":
        kind = detect_kind(text)
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид подписки: {kind!r}")
    if kind == "base64":
        text = _decode_base64(text)
        kind = "singbox" if text.strip().startswith("{") else "uri-list"
    if kind == "singbox":
        return vless.parse_singbox_json(text)
    profiles = _parse_uri(text)
    if len(profiles) > vless.MAX_PROFILES:
        raise ValueError(f"профилей больше {vless.MAX_PROFILES}")
    return profiles


def _record(profile, source, taken):
    return {"id": _new_id("p_", taken), "name": profile["name"],
            "proto": profile["proto"], "fingerprint": proxy.fingerprint(profile),
            profile["proto"]: proxy.fields(profile), "source": source,
            "missing": False}


def add_profile(link):
    return add_profiles(link)[0][0]


def add_profiles(text):
    profiles = parse_body("auto", text)
    data = load()
    taken = {p["id"] for p in data["profiles"]}
    known = {p["fingerprint"]: p for p in data["profiles"]}
    records = []
    added = 0
    changed = False
    for profile in profiles:
        mark = proxy.fingerprint(profile)
        record = known.get(mark)
        if record is None:
            record = _record(profile, {"manual": True}, taken)
            data["profiles"].append(record)
            known[mark] = record
            added += 1
            changed = True
        elif record["missing"]:
            record["missing"] = False
            changed = True
        records.append(record)
    if changed:
        save(data)
    return records, added


def _check_full_url(info):
    path = info["path"].split("?", 1)[0].rstrip("/")
    if path.rsplit("/", 1)[-1].lower() == "full":
        raise ValueError("адрес /full — это полная конфигурация sing-box; "
                         "импортируются только VLESS outbounds")


def add(url, name="", allow_private=False, kind="auto"):
    info, detected, profiles = fetch(url, allow_private, kind)
    data = load()
    source = url.strip()
    key = _url_key(source)
    if any(_url_key(item["url"]) == key for item in data["subscriptions"]):
        raise ValueError("такая подписка уже добавлена")
    taken = {s["id"] for s in data["subscriptions"]}
    sid = _new_id("s_", taken)
    profile_ids = {p["id"] for p in data["profiles"]}
    known = {p["fingerprint"] for p in data["profiles"]}
    for profile in profiles:
        mark = proxy.fingerprint(profile)
        if mark in known:
            continue
        known.add(mark)
        data["profiles"].append(
            _record(profile, {"subscription": sid}, profile_ids))
    sub = {"id": sid, "name": _name(name, "Happ" if happ.is_link(source) else info["host"]),
           "url": source,
           "kind": detected, "allow_private": allow_private,
           "updated_at": _now(), "last_error": ""}
    data["subscriptions"].append(sub)
    save(data)
    return sub


def fetch(url, allow_private=False, kind="auto"):
    if kind not in ADAPTIVE_KINDS:
        raise ValueError(f"вид подписки {kind!r}, ожидаю один из "
                         f"{', '.join(ADAPTIVE_KINDS)}")
    if not isinstance(allow_private, bool):
        raise ValueError("allow_private должен быть true или false")
    source = happ.decrypt(url) if happ.is_link(url) else url
    info = netguard.parse_url(source)
    _check_full_url(info)
    body = netguard.open_https(_fetch_url(info["url"]),
                               allow_private=allow_private)
    detected = detect_kind(body) if kind == "auto" else kind
    return info, detected, parse_body(detected, body)


def preview(url, name="", allow_private=False, kind="auto"):
    info, detected, profiles = fetch(url, allow_private, kind)
    return {"kind": detected, "name": _name(name, "Happ" if happ.is_link(url) else info["host"]),
            "host": info["host"], "port": info["port"],
            "profiles": len(profiles)}


def _alias_mark(profile):
    normalized = proxy.normalize(profile)
    fields = dict(proxy.fields(normalized))
    if normalized["proto"] != vless.PROTO or fields.get("security") != "reality":
        return None
    fields["short_id"] = ""
    return proxy.fingerprint({"proto": normalized["proto"],
                              normalized["proto"]: fields})


def refresh(sub_id, keep=()):
    sub_id = _check_sub_id(sub_id)
    keep = set(keep)
    data = load()
    sub = find(data, sub_id)
    if sub is None:
        raise ValueError(f"нет подписки {sub_id}")
    try:
        _, _, profiles = fetch(sub["url"], sub["allow_private"], sub["kind"])
    except ValueError as exc:
        sub["last_error"] = str(exc)
        save(data)
        raise
    sub["last_error"] = ""
    sub["updated_at"] = _now()

    existing = [p for p in data["profiles"]
                if p["source"] == {"subscription": sub_id}]
    by_mark = {p["fingerprint"]: p for p in existing}
    by_alias = {}
    for record in existing:
        alias = _alias_mark(record)
        if alias is not None:
            by_alias.setdefault(alias, []).append(record)
    other_marks = {p["fingerprint"] for p in data["profiles"]
                   if p["source"] != {"subscription": sub_id}}
    taken = {p["id"] for p in data["profiles"]}
    ordered = []
    seen = set()
    live_aliases = set()
    used = set()
    added = updated = 0
    for profile in profiles:
        mark = proxy.fingerprint(profile)
        if mark in seen or mark in other_marks:
            continue
        seen.add(mark)
        alias = _alias_mark(profile)
        if alias is not None:
            live_aliases.add(alias)
        same = []
        record = None
        if alias is not None:
            same = [p for p in by_alias.get(alias, [])
                    if p["id"] not in used]
            record = next((p for p in same if p["id"] in keep), None)
        if record is None:
            record = by_mark.get(mark)
            if record is not None and record["id"] in used:
                record = None
        if record is None and same:
            record = (next((p for p in same if not p["missing"]), None)
                      or same[0])
        if record is None:
            record = _record(profile, {"subscription": sub_id}, taken)
            added += 1
        else:
            record["fingerprint"] = mark
            record["name"] = profile["name"]
            record[profile["proto"]] = proxy.fields(profile)
            record["missing"] = False
            updated += 1
        used.add(record["id"])
        ordered.append(record)

    missing = 0
    for record in existing:
        if record["id"] in used:
            continue
        alias = _alias_mark(record)
        if alias in live_aliases and record["id"] not in keep:
            missing += 1
            continue
        record["missing"] = True
        missing += 1
        ordered.append(record)

    others = [p for p in data["profiles"]
              if p["source"] != {"subscription": sub_id}]
    data["profiles"] = others + ordered
    save(data)
    return {"id": sub_id, "added": added, "updated": updated,
            "missing": missing, "total": len(ordered)}


def delete(sub_id):
    sub_id = _check_sub_id(sub_id)
    data = load()
    if find(data, sub_id) is None:
        return False
    data["subscriptions"] = [s for s in data["subscriptions"]
                             if s["id"] != sub_id]
    data["profiles"] = [p for p in data["profiles"]
                        if p["source"] != {"subscription": sub_id}]
    save(data)
    return True


def remove_profile(pid):
    pid = check_profile_id(pid)
    data = load()
    record = find_profile(data, pid)
    if record is None:
        raise ValueError(f"нет профиля {pid}")
    data["profiles"].remove(record)
    save(data)
    return True


def set_allow_private(sub_id, value):
    sub_id = _check_sub_id(sub_id)
    if not isinstance(value, bool):
        raise ValueError("allow_private должен быть true или false")
    data = load()
    sub = find(data, sub_id)
    if sub is None:
        raise ValueError(f"нет подписки {sub_id}")
    sub["allow_private"] = value
    save(data)
    return sub


def list_subscriptions():
    return load()["subscriptions"]


def list_profiles(sub_id=None):
    data = load()
    if sub_id is None:
        return list(data["profiles"])
    sub_id = _check_sub_id(sub_id)
    if find(data, sub_id) is None:
        raise ValueError(f"нет подписки {sub_id}")
    return [p for p in data["profiles"]
            if p["source"] == {"subscription": sub_id}]
