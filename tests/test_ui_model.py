import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "lib", "tunnelvpn", "ui", "js")
NODE = shutil.which("node") or shutil.which("node.exe")

pytestmark = pytest.mark.skipif(not NODE, reason="для model.js нужен node")


def _model(statuses):
    with open(os.path.join(JS, "model.js"), encoding="utf-8") as fh:
        source = fh.read()
    script = "\n".join([
        "const PENDING_MS = 6000;",
        "const confOf = t => t.active || ((t.confs || []).length === 1 ? t.confs[0] : '');",
        source,
        f"const CASES = {json.dumps(statuses, ensure_ascii=False)};",
        """const out = {};
for (const [name, s] of Object.entries(CASES)) {
  const m = model(s);
  out[name] = {link: m.link, head: m.head.title, worst: m.worst,
               problems: m.problems.map(p => [p.kind, p.text]),
               ids: m.problems.map(p => p.id),
               acts: m.problems.map(p => p.act)};
}
process.stdout.write(JSON.stringify(out));""",
    ])
    done = subprocess.run([NODE, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


TUNNEL = {"id": "home", "name": "Личный", "active": "nl.conf",
          "names": {"nl.conf": "nl-1"}, "confs": ["nl.conf"],
          "mode": "all", "enabled": True, "check": "ok", "carries": True}


def _status(**over):
    status = {"up": True, "loading": False, "no_service": False,
              "tunnels": [dict(TUNNEL)], "out": "", "exit_state": "",
              "v6_leak": ""}
    status.update(over)
    return status


def _card(status, tunnel):
    with open(os.path.join(JS, "model.js"), encoding="utf-8") as fh:
        model = fh.read()
    with open(os.path.join(JS, "cards.js"), encoding="utf-8") as fh:
        cards = fh.read()
    script = "\n".join([
        "const PENDING_MS = 6000;",
        "const el = () => ({append() {}, addEventListener() {}, classList: {add() {}, remove() {}, toggle() {}}, dataset: {}});",
        "const $ = () => el();",
        "const icon = () => el();",
        "const onRender = () => {};",
        model,
        cards,
        f"const state = {json.dumps(status, ensure_ascii=False)};",
        f"const tun = {json.dumps(tunnel, ensure_ascii=False)};",
        "const m = model(state);",
        "process.stdout.write(JSON.stringify(cardState(tun, state, m)));",
    ])
    done = subprocess.run([NODE, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("kind, want", [
    ("blocked", ["err", "не работает · kill-switch закрыл интернет"]),
    ("fallback", ["warn", "не работает · интернет идёт напрямую"]),
    ("leak", ["err", "трафик идёт мимо туннеля"]),
])
def test_карточка_показывает_аварию_раньше_проверки(kind, want):
    tun = {**TUNNEL, "tests": {"nl.conf": {"checking": True}},
           "check": "up", "answer": "12 мс"}
    status = _status(link={"kind": kind}, tunnels=[tun])

    assert _card(status, tun) == want


def test_повторная_проверка_держит_прежний_пинг_а_таймаут_его_снимает():
    tun = {**TUNNEL, "check": "up", "answer": "12 мс", "checking": True}
    assert _card(_status(tunnels=[tun]), tun) == [
        "busy", "через туннель · 12 мс"]

    tun = {**TUNNEL, "check": "up", "answer": "12 мс"}
    status = _status(link={"kind": "checking"}, tunnels=[tun])
    assert _card(status, tun) == ["busy", "через туннель · 12 мс"]

    stale = {**TUNNEL, "check": "error", "answer": "12 мс"}
    assert _card(_status(tunnels=[stale]), stale) == ["err", "не работает"]


def test_блокировка_без_основного_не_падает():
    off = {**TUNNEL, "enabled": False}
    out = _model({"blocked": _status(link={"kind": "blocked"},
                                     out="block", tunnels=[off])})

    assert out["blocked"]["problems"] == [
        ["err", "не работает — kill-switch закрыл интернет"]]
    assert out["blocked"]["ids"] == [""]
    assert out["blocked"]["acts"] == ["restart"]


def test_link_kind_сильнее_сырых_полей():
    waiting = {**TUNNEL}
    waiting.pop("check")
    out = _model({
        "blocked": _status(link={"kind": "blocked"}, out="direct",
                           exit_state="direct"),
        "blocked_waiting": _status(link={"kind": "blocked"}, out="direct",
                                   exit_state="direct", busy="переподключаю",
                                   tunnels=[waiting]),
        "fallback": _status(link={"kind": "fallback"}, out="block",
                            exit_state="blocked"),
        "leak": _status(link={"kind": "leak"}, exit_state="tunnel"),
    })

    assert out["blocked"]["link"] == "blocked"
    assert out["blocked"]["head"] == "Интернет закрыт"
    assert ["err", "не работает — kill-switch закрыл интернет"] in \
        out["blocked"]["problems"]
    assert out["blocked_waiting"]["head"] == "Интернет закрыт"
    assert out["blocked_waiting"]["problems"] == out["blocked"]["problems"]

    assert out["fallback"]["head"] != "Интернет закрыт"
    assert ["warn", "не работает — интернет идёт напрямую"] in \
        out["fallback"]["problems"]
    assert not any("kill-switch" in text for _, text in out["fallback"]["problems"])

    assert out["leak"]["head"] == "Трафик мимо туннеля"
    assert ["err", "трафик идёт мимо туннеля"] in out["leak"]["problems"]


def test_без_link_запасной_путь_как_раньше():
    out = _model({
        "blocked": _status(out="block", exit_state="blocked"),
        "fallback": _status(out="direct", exit_state="direct"),
        "leak": _status(exit_state="leak"),
    })

    assert out["blocked"]["link"] == "blocked"
    assert out["blocked"]["head"] == "Интернет закрыт"
    assert out["fallback"]["link"] == "fallback"
    assert out["fallback"]["head"] != "Интернет закрыт"
    assert out["leak"]["link"] == "leak"
    assert out["leak"]["head"] == "Трафик мимо туннеля"
