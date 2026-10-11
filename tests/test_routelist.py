"""Списки «Пускать» и «Не пускать»: что человек ввёл и что из этого вышло.

Поле принимает текст из любого источника: адрес из письма, ссылку из
браузера, строку из чужого конфига. Ошибка разбора не видна сразу — туннель
просто не заберёт адрес. Поэтому каждая форма записи проверена отдельно, а
непонятое обязано вернуться, а не исчезнуть.
"""

import pytest

from tunnelvpn import routelist


@pytest.mark.parametrize("text", [
    "a.ru 1.2.3.4",
    "a.ru\n1.2.3.4",
    "a.ru\r\n1.2.3.4",
    "a.ru\t1.2.3.4",
    "a.ru,1.2.3.4",
    "a.ru;1.2.3.4",
    "  a.ru ,; \n 1.2.3.4 ;",
])
def test_каждый_разделитель_делит_записи(text):
    assert routelist.parse(text) == (["a.ru", "1.2.3.4/32"], [])


def test_двоеточие_не_разделитель():
    """Иначе «host:port» стал бы двумя записями, а IPv6 — кашей."""
    assert routelist.parse("git.x.su:22") == (["git.x.su"], [])
    assert routelist.parse("2001:db8::1") == (["2001:db8::1/128"], [])


@pytest.mark.parametrize("given, expected", [
    ("1.2.3.4", "1.2.3.4/32"),
    ("10.0.0.5/8", "10.0.0.0/8"),
    ("192.168.1.0/24", "192.168.1.0/24"),
    ("1.2.3.4:443", "1.2.3.4/32"),
    ("2001:db8::/32", "2001:db8::/32"),
    ("[2001:db8::1]:443", "2001:db8::1/128"),
    ("git.x.su", "git.x.su"),
    ("GIT.X.SU", "git.x.su"),
    ("git.x.su.", "git.x.su"),
    ("*.x.su", "*.x.su"),
    ("*.X.SU.", "*.x.su"),
    ("https://git.x.su/a/b?c=1", "git.x.su"),
    ("http://git.x.su:8080/", "git.x.su"),
    ("https://user@git.x.su", "git.x.su"),
    ("https://1.2.3.4/x", "1.2.3.4/32"),
    ("git.x.su:22", "git.x.su"),
    ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
    ("ПРИМЕР.РФ", "xn--e1afmkfd.xn--p1ai"),
    ("*.пример.рф", "*.xn--e1afmkfd.xn--p1ai"),
    ("xn--e1afmkfd.xn--p1ai", "xn--e1afmkfd.xn--p1ai"),
    ("a-b.c-d.ru", "a-b.c-d.ru"),
])
def test_форма_записи_нормализуется(given, expected):
    assert routelist.parse(given) == ([expected], [])


@pytest.mark.parametrize("token", [
    "foo_bar",       # подчёркивание в имени хоста
    "localhost",     # без точки — скорее опечатка, чем домен
    "1.2.3",         # недописанный адрес
    "300.1.1.1",     # не адрес, а зона из цифр
    "1.2.3.4/33",
    "-a.ru",
    "a-.ru",
    "a..ru",
    "*.",
    "*.1.2.3.4",
    "**.a.ru",
    "a.*.ru",
    "https://",
    "git.x.su:port",
    "a" * 64 + ".ru",
])
def test_непонятное_возвращается_как_есть(token):
    assert routelist.parse(token) == ([], [token])


def test_дубли_схлопываются_после_нормализации():
    text = "a.ru A.RU a.ru. https://a.ru/x 1.2.3.4 1.2.3.4/32 1.2.3.4:80"
    assert routelist.parse(text) == (["a.ru", "1.2.3.4/32"], [])


def test_непонятые_тоже_без_повторов_и_в_порядке_ввода():
    assert routelist.parse("x_1 a.ru x_2 x_1") == (["a.ru"], ["x_1", "x_2"])


def test_домен_и_его_поддомены_разные_записи():
    """a.ru — с поддоменами, *.a.ru — только поддомены: смысл разный."""
    assert routelist.parse("a.ru *.a.ru") == (["a.ru", "*.a.ru"], [])


def test_пример_из_окна():
    text = "a.ru, 1.2.3.4; *.b.ru  foo_bar"
    assert routelist.parse(text) == (["a.ru", "1.2.3.4/32", "*.b.ru"], ["foo_bar"])


@pytest.mark.parametrize("text", ["", "   ", "\n,;\n", None])
def test_пустое_поле_даёт_пустой_список(text):
    assert routelist.parse(text) == ([], [])


def test_готовый_список_разбирается_так_же():
    assert routelist.parse_list(["a.ru", "1.2.3.4", "x_y"]) == (
        ["a.ru", "1.2.3.4/32"], ["x_y"])
    assert routelist.parse_list(None) == ([], [])


# ------------------------------------------- сводка и причины для листа окна

def test_токены_с_номером_строки_как_у_textarea():
    assert routelist.tokens("a.ru, x_y\n\n1.2.3.4") == [
        (1, "a.ru", "a.ru"), (1, "x_y", None), (3, "1.2.3.4", "1.2.3.4/32")]


def test_сводка_считает_домены_адреса_и_подсети():
    r = routelist.check("a.ru *.b.ru 1.2.3.4 2001:db8::1 10.0.0.0/8 2001:db8::/32 a.ru")

    assert (r["count"], r["domains"], r["ips"], r["nets"]) == (6, 2, 2, 2)
    assert r["problems"] == []


def test_непонятое_с_номером_строки_по_разу():
    r = routelist.check("a.ru\ngit\ngit 10.0.0.0/33")

    assert [(p["line"], p["token"]) for p in r["problems"]] == [(2, "git"), (3, "10.0.0.0/33")]
    assert r["count"] == 1


def test_больше_предела_одной_строкой_без_номера(monkeypatch):
    monkeypatch.setattr(routelist, "MAX_RULES", 2)

    r = routelist.check("a.ru b.ru c.ru")

    assert r["problems"] == [{"line": None, "token": "",
                              "text": "Больше 2 записей → раздели список или правь "
                                      "tunnels.json вручную"}]


@pytest.mark.parametrize("token, part", [
    ("git", "короткое имя не сработает → укажи полный домен, например git.company.ru"),
    ("10.0.0.0/33", "маска больше 32 → для IPv4 маска от 0 до 32"),
    ("2001:db8::/129", "маска больше 128"),
    ("10.0.0.0/", "после «/» нет маски"),
    ("1.2.3", "четыре числа через точку"),
    ("300.1.1.1", "больше 255"),
    ("foo_bar", "не бывает «_»"),
    ("a..ru", "две точки подряд"),
    ("-a.ru", "дефисом"),
    ("a.123", "зона из букв"),
    ("*.", "после «*.» нужен домен"),
    ("https://", "в ссылке нет сайта"),
    ("a" * 64 + ".ru", "слишком длинный домен"),
])
def test_причина_говорит_что_не_так_и_как_исправить(token, part):
    text = routelist.explain(token)

    assert text.startswith(f"«{token}» — ") and part in text and "→" in text


def test_у_каждого_непонятого_есть_причина():
    """Всё, что parse отверг, explain объясняет — без пустых строк."""
    for token in ["::::", "a:b:c:z", "x.y:abc", "ý.ru-"]:
        assert routelist.parse(token)[1] == [token]
        assert "→" in routelist.explain(token)
