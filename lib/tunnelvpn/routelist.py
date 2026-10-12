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
import ntpath
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
                         "text": f"Больше {MAX_RULES} записей → разделите список "
                                 f"или измените tunnels.json вручную"})
    return {"count": len(entries), "domains": len(entries) - len(nets),
            "ips": ips, "nets": len(nets) - ips, "problems": problems}


def explain(token):
    """Почему токен не стал записью: «что не так → как исправить»."""
    host = _host(token.strip().lower())
    shown = f"«{token}»"
    if not host:
        return f"{shown} — в ссылке нет сайта → вставьте домен, например git.company.ru"
    if _V4ISH.match(host) or _V6ISH.match(host):
        return f"{shown} — {_explain_ip(host)}"
    name = (host[2:] if host.startswith("*.") else host).rstrip(".")
    if not name:
        return f"{shown} — после «*.» нужен домен → например *.company.ru"
    bad = "".join(sorted({c for c in name if not (c.isalnum() or c in "-.")}))
    if bad:
        return f"{shown} — в домене не бывает «{bad}» → только буквы, цифры, дефис и точка"
    if "." not in name:
        return (f"{shown} — короткое имя не сработает → укажите полный домен, "
                f"например {name}.company.ru")
    labels = name.split(".")
    if not all(labels):
        return f"{shown} — две точки подряд → уберите лишнюю"
    if any(x.startswith("-") or x.endswith("-") for x in labels):
        return f"{shown} — часть домена начинается или кончается дефисом → уберите его"
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


# «Программы мимо VPN» основного: полный путь к .exe, по одному на строку —
# в пути бывают пробелы, поэтому разделитель только перевод строки. Повторы
# — без регистра, как пути Windows. Потолок — как у MAX_RULES: каждая запись
# — регулярка, которую sing-box сверяет на каждом новом соединении.
MAX_APPS = 100
# Чего не бывает в пути Windows. «:» — только после буквы диска.
_BAD_PATH = re.compile(r'[<>"|?*\x00-\x1f]')


def app(line):
    """Путь к программе в нормальном виде, иначе None.

    Кавычки по краям снимаем: их ставит «Копировать как путь» проводника.
    Путь нужен полный — с диском или \\\\сервер\\папка: процесс sing-box
    узнаёт по полному пути, относительный ни с чем не совпадёт.
    """
    path = line.strip().strip('"').strip()
    if not path or _BAD_PATH.search(path):
        return None
    path = ntpath.normpath(path)
    drive, rest = ntpath.splitdrive(path)
    if (not drive or not ntpath.isabs(path) or ":" in rest
            or not path.lower().endswith(".exe") or ntpath.basename(path).lower() == ".exe"):
        return None
    return path


def app_lines(text):
    """Поле программ: [(номер строки с 1, строка как есть, путь или None)],
    пустые строки пропущены."""
    return [(n, line.strip(), app(line))
            for n, line in enumerate((text or "").split("\n"), 1) if line.strip()]


def parse_apps(text):
    """Поле программ: (пути, непонятые строки), без повторов, порядок — как ввёл
    человек."""
    entries, seen, rejected = [], set(), []
    for _line, raw, found in app_lines(text):
        if found is None:
            if raw not in rejected:
                rejected.append(raw)
        elif found.lower() not in seen:
            seen.add(found.lower())
            entries.append(found)
    return entries, rejected


def parse_app_list(items):
    """То же для готового списка строк — например, из tunnels.json."""
    return parse_apps("\n".join(str(x) for x in items or ()))


def check_apps(text):
    """Сводка поля программ для листа окна: {count, problems} — как у check."""
    entries, problems = parse_apps(text)[0], []
    seen = set()
    for line, raw, found in app_lines(text):
        if found is None and raw not in seen:
            seen.add(raw)
            problems.append({"line": line, "token": raw, "text": explain_app(raw)})
    if len(entries) > MAX_APPS:
        problems.append({"line": None, "token": "",
                         "text": f"Больше {MAX_APPS} программ → оставьте нужные"})
    return {"count": len(entries), "problems": problems}


def explain_app(raw):
    """Почему строка не стала путём к программе: «что не так → как исправить»."""
    shown = f"«{raw}»"
    path = raw.strip().strip('"').strip()
    bad = "".join(sorted(set(_BAD_PATH.findall(path))))
    if bad.strip():
        return f"{shown} — в пути не бывает «{bad}» → выберите файл кнопкой «Добавить программу…»"
    if not path.lower().endswith(".exe"):
        return f"{shown} — это не программа → нужен путь к файлу .exe"
    return (f"{shown} — нужен полный путь → например C:\\Program Files\\App\\app.exe "
            f"или кнопка «Добавить программу…»")
