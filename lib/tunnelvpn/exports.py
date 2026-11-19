from . import ipc, proxy, subscriptions, tunnels

VERSION = 1


def entry(path, text):
    return {"path": path, "text": text}


def check_path(path):
    if path in ("manifest.json", "links.txt"):
        return True
    parts = str(path or "").split("/")
    if len(parts) != 3 or parts[0] != "configs" or not parts[2].endswith(".conf"):
        return False
    try:
        tunnels.check_id(parts[1])
        tunnels.check_name(parts[2][:-5])
    except ValueError:
        return False
    return True


def conf_text(tid, name):
    try:
        with open(tunnels.conf_path(tid, name), encoding="utf-8",
                  errors="replace") as fh:
            return fh.read()
    except OSError:
        raise ValueError(f"конфиг «{name}» пропал или не читается")


class _Links:
    def __init__(self):
        self.lines = []
        self._by_key = {}

    def add(self, key, text):
        if key in self._by_key:
            return self._by_key[key]
        self.lines.append(text)
        self._by_key[key] = f"L{len(self.lines)}"
        return self._by_key[key]

    def __len__(self):
        return len(self.lines)


def _tunnel(data, tid):
    t = next((x for x in data["tunnels"] if x["id"] == tid), None)
    if t is None:
        raise ValueError(f"нет туннеля {tid}")
    return t


def _profile(subs, pid):
    profile = subscriptions.find_profile(subs, pid)
    if profile is None:
        raise ValueError(f"нет профиля {pid}")
    return profile


def _confs(t):
    return tunnels.list_confs(tunnels.check_id(t["id"]),
                              "vless" if t.get("proto") == "vless" else "wg")


def _vless_group(t, subs, links, confs):
    profiles = []
    for pid in confs:
        profile = _profile(subs, pid)
        profiles.append({"name": profile["name"],
                         "link": links.add(proxy.fingerprint(profile),
                                           proxy.link(profile))})
    active = next((p["link"] for p, pid in zip(profiles, confs)
                   if pid == t["active"]),
                  profiles[0]["link"] if len(profiles) == 1 else "")
    return {"id": t["id"], "name": t["name"], "proto": "vless",
            "active": active, "configs": [], "profiles": profiles}


def _wg_group(t, confs):
    paths = []
    entries = []
    for name in confs:
        rel = f"configs/{t['id']}/{name}.conf"
        entries.append(entry(rel, conf_text(t["id"], name)))
        paths.append(rel)
    chosen = t["active"] or (confs[0] if len(confs) == 1 else "")
    return {"id": t["id"], "name": t["name"], "proto": t.get("proto", "wg"),
            "active": paths[confs.index(chosen)] if chosen in confs else "",
            "configs": paths, "profiles": []}, entries


def _single(t, subs, links, name):
    if t.get("proto") == "vless":
        return _vless_group(t, subs, links,
                            [subscriptions.check_profile_id(name)]), []
    want = tunnels.check_name(name)
    if want not in _confs(t):
        raise ValueError(f"нет конфига «{want}» в туннеле {t['name']}")
    return _wg_group(t, [want])


def _manifest(groups, unattached=()):
    return {"version": VERSION, "tunnels": groups,
            "unattached": list(unattached)}


def _snapshot(scope, tunnel, name):
    data = tunnels.load()
    subs = subscriptions.load()
    links = _Links()
    if scope == "config":
        if not tunnel or not name:
            raise ValueError("для экспорта одного конфига нужны туннель и имя")
        group, entries = _single(_tunnel(data, tunnels.check_id(tunnel)),
                                 subs, links, name)
        return entries, links.lines, _manifest([group])
    if scope not in ("tunnel", "all"):
        raise ValueError("неизвестная область экспорта")
    tid = tunnels.check_id(tunnel) if scope == "tunnel" else ""
    chosen = [_tunnel(data, tid)] if tid else data["tunnels"]
    groups = []
    entries = []
    covered = set()
    for t in chosen:
        confs = _confs(t)
        if t.get("proto") == "vless":
            covered.update(confs)
            groups.append(_vless_group(t, subs, links, confs))
        else:
            group, conf_entries = _wg_group(t, confs)
            groups.append(group)
            entries.extend(conf_entries)
    unattached = []
    if scope == "all":
        for p in subs["profiles"]:
            if p["id"] in covered:
                continue
            unattached.append({"name": p["name"],
                               "link": links.add(proxy.fingerprint(p),
                                                 proxy.link(p))})
    if not entries and not len(links):
        raise ValueError("в выбранной области нечего экспортировать")
    if len(entries) + len(links) > ipc.MAX_EXPORT_ITEMS:
        raise ValueError("экспорт слишком велик для одного файла — "
                         "сохраните по одной группе")
    return entries, links.lines, _manifest(groups, unattached)


def snapshot(scope, tunnel="", name=""):
    with tunnels.LOCK:
        with subscriptions.LOCK:
            return _snapshot(scope, tunnel, name)


def links(scope, tunnel="", name=""):
    entries, lines, _ = snapshot(scope, tunnel, name)
    if lines:
        return lines
    if scope == "config":
        raise ValueError("у конфига WireGuard/AmneziaWG переносимой прокси-"
                         "ссылки нет: сохраните сам файл .conf")
    raise ValueError(f"в группе только конфиги WireGuard/AmneziaWG "
                     f"({len(entries)}) — переносимых ссылок нет, "
                     f"сохраните архив конфигов")
