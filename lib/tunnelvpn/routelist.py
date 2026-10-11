"""Списки «Пускать через туннель» и «Не пускать»: разбор того, что ввёл человек.

В поле вставляют что угодно: адрес из письма, ссылку из браузера, строку из
чужого конфига. Поэтому разбор терпимый к форме записи и строгий к сути:

    1.2.3.4             -> 1.2.3.4/32
    10.0.0.5/8          -> 10.0.0.0/8
    git.x.su            -> git.x.su       (вместе со всеми поддоменами)
    *.x.su              -> *.x.su         (только поддомены)
    https://git.x.su/a  -> git.x.su
    git.x.su:22         -> git.x.su       (порт маршрутизации не нужен)
    пример.рф           -> xn--e1afmkfd.xn--p1ai

Разделители — пробел, перевод строки, «,» и «;». Двоеточие разделителем не
считается: оно входит и в IPv6, и в host:port. Всё, что не удалось понять,
возвращается отдельно: окно показывает это человеку, молча ничего не пропадает.
"""

import ipaddress
import re
import urllib.parse

_SPLIT = re.compile(r"[\s,;]+")
# Метка домена по RFC 1123: буквы, цифры и дефис не по краям.
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Записей в одном списке правил через канал. Запрос канала — одно чтение
# ipc._BUF (64 КБ), а каждая запись — правило в конфиге sing-box: тысячи —
# скорее вставленный по ошибке файл, чем замысел. Руками в tunnels.json — сколько угодно.
MAX_RULES = 500


def _ip(token):
    """IPv4/IPv6 адрес или подсеть в каноническом виде, иначе None."""
    try:
        return str(ipaddress.ip_network(token, strict=False))
    except ValueError:
        return None


def _domain(token):
    """Домен в нижнем регистре и punycode, иначе None.

    Нужна хотя бы одна точка: слово без неё — скорее опечатка, чем имя,
    а domain_suffix из одной метки забрал бы в туннель целую зону.
    """
    token = token.rstrip(".")
    if not token or "." not in token:
        return None
    try:
        token = token.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    token = token.lower()
    labels = token.split(".")
    if len(token) > 253 or not all(_LABEL.match(x) for x in labels):
        return None
    # «1.2.3» — не домен, а недописанный адрес: у имени зона не из цифр.
    if labels[-1].isdigit():
        return None
    return token


def _host(token):
    """Хост из URL или host:port; токен без них — как есть."""
    if "://" in token:
        try:
            return urllib.parse.urlsplit(token).hostname or ""
        except ValueError:
            return ""
    if token.startswith("["):
        # [2001:db8::1]:443
        return token[1:].partition("]")[0]
    host, sep, port = token.rpartition(":")
    if sep and port.isdigit() and ":" not in host:
        return host
    return token


def _one(token):
    """Одна запись в нормальном виде, иначе None."""
    token = token.strip().lower()
    if not token:
        return None
    found = _ip(token)
    if found:
        return found
    host = _host(token)
    found = _ip(host)
    if found:
        return found
    if host.startswith("*."):
        found = _domain(host[2:])
        return f"*.{found}" if found else None
    return _domain(host)


def tokens(text):
    """Поле по строкам: [(номер строки с 1, токен как есть, запись или None)].

    Строки — по «\\n», как у textarea: по номеру и токену окно выделяет
    запись в поле.
    """
    out = []
    for n, line in enumerate((text or "").split("\n"), 1):
        for token in _SPLIT.split(line):
            if token:
                out.append((n, token, _one(token)))
    return out


def parse(text):
    """Разбирает поле списка: (записи, непонятые).

    Записи нормализованы и без повторов, порядок — как ввёл человек.
    Непонятые — токены как есть, тоже без повторов.
    """
    entries, rejected = [], []
    for _line, token, found in tokens(text):
        if found is None:
            if token not in rejected:
                rejected.append(token)
        elif found not in entries:
            entries.append(found)
    return entries, rejected


def parse_list(items):
    """То же для готового списка строк — например, прочитанного из файла."""
    return parse(" ".join(str(x) for x in items or ()))


def check(text):
    """Сводка поля для листа окна: {count, domains, ips, nets, problems}.

    problems — [{line, token, text}]: непонятое — по разу на токен, с
    номером строки; больше MAX_RULES — одной строкой без номера (line None).
    """
    entries, known, problems, seen = [], set(), [], set()
    for line, token, found in tokens(text):
        if found is None:
            if token not in seen:
                seen.add(token)
                problems.append({"line": line, "token": token, "text": explain(token)})
        elif found not in known:
            known.add(found)
            entries.append(found)
    nets = [ipaddress.ip_network(e) for e in entries if "/" in e]
    ips = sum(1 for n in nets if n.num_addresses == 1)
    if len(entries) > MAX_RULES:
        problems.append({"line": None, "token": "",
                         "text": f"Больше {MAX_RULES} записей → раздели список "
                                 f"или правь tunnels.json вручную"})
    return {"count": len(entries), "domains": len(entries) - len(nets),
            "ips": ips, "nets": len(nets) - ips, "problems": problems}


def explain(token):
    """Почему токен не стал записью: «что не так → как исправить»."""
    host = _host(token.strip().lower())
    shown = f"«{token}»"
    if not host:
        return f"{shown} — в ссылке нет сайта → вставь домен, например git.company.ru"
    if _V4ISH.match(host) or _V6ISH.match(host):
        return f"{shown} — {_explain_ip(host)}"
    name = (host[2:] if host.startswith("*.") else host).rstrip(".")
    if not name:
        return f"{shown} — после «*.» нужен домен → например *.company.ru"
    bad = "".join(sorted({c for c in name if not (c.isalnum() or c in "-.")}))
    if bad:
        return f"{shown} — в домене не бывает «{bad}» → только буквы, цифры, дефис и точка"
    if "." not in name:
        return (f"{shown} — короткое имя не сработает → укажи полный домен, "
                f"например {name}.company.ru")
    labels = name.split(".")
    if not all(labels):
        return f"{shown} — две точки подряд → убери лишнюю"
    if any(x.startswith("-") or x.endswith("-") for x in labels):
        return f"{shown} — часть домена начинается или кончается дефисом → убери его"
    if labels[-1].isdigit():
        return (f"{shown} — домен не кончается цифрами → адрес — четыре числа "
                f"через точку, у домена зона из букв")
    if len(name) > 253 or any(len(x) > 63 for x in labels):
        return f"{shown} — слишком длинный домен → до 63 символов между точками и 253 всего"
    return (f"{shown} — не похоже ни на домен, ни на адрес → пример: git.company.ru, "
            f"10.0.0.5, 10.0.0.0/8")


# Похоже на адрес, а не на домен: цифры и точки (IPv4), шестнадцатеричное с
# двоеточием (IPv6), и, может быть, маска.
_V4ISH = re.compile(r"^[\d.]+(?:/\d*)?$")
_V6ISH = re.compile(r"^[0-9a-f.]*:[0-9a-f:.]*(?:/\d*)?$")


def _explain_ip(host):
    addr, slash, mask = host.partition("/")
    v6 = ":" in addr
    limit = 128 if v6 else 32
    if slash and not mask:
        return "после «/» нет маски → например 10.0.0.0/8"
    if mask and int(mask) > limit:
        return f"маска больше {limit} → для IPv{6 if v6 else 4} маска от 0 до {limit}"
    if not v6:
        parts = addr.split(".")
        if len(parts) != 4 or not all(parts):
            return "в IPv4-адресе четыре числа через точку → например 10.0.0.5"
        if any(int(p) > 255 for p in parts):
            return "число в адресе больше 255 → каждое от 0 до 255"
    return "адрес не разобрать → пример: 10.0.0.5, 10.0.0.0/8, 2001:db8::/32"
