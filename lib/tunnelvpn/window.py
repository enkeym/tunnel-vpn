"""Окно приложения: та же вёрстка, что была на macOS, но на WebView2.

Вёрстка (ui/index.html) осталась прежней, поменялся только её мост: наружу
идёт `window.pywebview.api.send(name, arg)`, внутрь — `call(fn, arg)` и
`callN(fn, [args])`, которые дёргают `window[fn]`. Разметка, стили и вся
логика отрисовки — те же, что были на macOS.

Само окно систему не трогает: всё, что требует прав, уходит в службу через
именованный канал. Поэтому окно запускается от обычного пользователя, и UAC
на нём не появляется — кроме одного случая: запись конфигов (см. _admin_call).
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

from . import instance, ipc, paths, portable, routelist, settings, tunnels, update

POLL_EVERY = 2.0
# Ход скачивания обновления — не чаще: куски по 256 КБ идут десятками в
# секунду, а каждый вызов страницы — evaluate_js.
PROGRESS_EVERY = 0.2
# Тост при сбое скачивания: причина (тайм-аут, DNS, обрыв) — в журнал.
DOWNLOAD_FAILED = "Не удалось скачать обновление → проверьте интернет и повторите"
# Сколько ждём отчёта страницы о готовности. Не дождались — WebView2 не
# поднялся, и окно так и висело бы серым: выходим с READY_LOST, трей
# перезапустит окно.
READY_WAIT = 20.0
READY_LOST = 3
# Сколько холодное окно ждёт готовую страницу, прежде чем показаться как
# есть: дольше без окна кажется, что ярлык не сработал.
COLD_SHOW_WAIT = 4.0
# В paths.FOCUS_FILE вместо id туннеля: пункт трея «Доступно обновление» —
# открыть «Настройки → О программе». Двоеточия в id не бывает (tunnels.check_id).
FOCUS_ABOUT = ":about"
# Порт DevTools окна из исходников — к нему цепляется MCP-сервер playwright-cdp
# (installer/dev/cdp-window.ps1). WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS порт не
# открывает: pywebview задаёт свои AdditionalBrowserArguments.
DEVTOOLS_PORT_ENV = "TUNNELVPN_DEVTOOLS_PORT"

# Фильтр для create_file_dialog. pywebview проверяет каждую строку регуляркой
# вида ^([\w ]+)\(\*\.\w+...\)$ : в описании допустимы только буквы и пробелы,
# без точек, а кириллица на части сборок под \w не подходит. Из-за «site.env
# (*.env)» и «Все файлы (*.*)» диалог падал с ValueError ещё до открытия —
# держим строки латиницей и без точек в описании.
_FILE_TYPES = ("Config files (*.conf)", "All files (*.*)")
# Списки правил туннеля: JSON экспорта или текст списка.
_RULES_TYPES = ("Rules (*.json;*.txt)", "All files (*.*)")
# «Добавить программу…» в листе основного.
_APP_TYPES = ("Programs (*.exe)",)
# Поля листа «Туннелирование»: списки туннеля в tunnels.json. exclude_apps — только у
# основного, пути по одному на строку (routelist.parse_apps).
_RULE_KEYS = ("include", "exclude", "exclude_apps")


def _is_admin():
    """Уже ли этот процесс с правами администратора.

    В портативной версии — да всегда, весь процесс поднят с правами при
    первом запуске. В установленной — нет: окно намеренно живёт от обычного
    пользователя, иначе UAC спрашивал бы себя на каждое открытие окна.
    """
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_elevated(exe, args, show=False):
    """Запускает exe с правами (UAC) и ждёт, пока он завершится.

    show — окно процесса видно: admin-op прячем, Блокнот с конфигом — нет.

    Раньше это делал powershell Start-Process -Verb RunAs: тот же
    ShellExecuteEx внутри, но сначала секунда-другая на запуск самого
    PowerShell — до появления окна UAC. Отказ в UAC приходит исключением;
    наружу он не выходит: вызывающий судит по файлу ответа.
    """
    import pywintypes
    import win32api
    import win32con
    import win32event
    from win32com.shell import shell, shellcon

    try:
        info = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
            lpFile=exe, lpParameters=subprocess.list2cmdline(args),
            nShow=win32con.SW_SHOWNORMAL if show else win32con.SW_HIDE)
    except pywintypes.error:
        return
    proc = info.get("hProcess")
    if not proc:
        return
    try:
        win32event.WaitForSingleObject(proc, win32event.INFINITE)
    finally:
        win32api.CloseHandle(proc)


def _service_installed():
    """Стоит ли служба: канал молчит и у остановленной, а «Нужна установка»
    положено показывать только у непоставленной. None — узнать нечем.
    """
    try:
        import pywintypes
        import win32serviceutil
    except ImportError:
        return None
    try:
        win32serviceutil.QueryServiceStatus(paths.SERVICE_NAME)
        return True
    except pywintypes.error:
        return False


class Api:
    """То, что вёрстка зовёт через мост. Имена сообщений заданы в index.html."""

    def __init__(self, window_holder):
        self.holder = window_holder
        self.ready = threading.Event()
        # Прогретое окно, спрятанное до первого показа или крестиком.
        self.hidden = False
        # Холодное окно: создано спрятанным и покажется на ready, уже
        # отрисованным, — без него виден весь старт WebView2 с рывками.
        self.cold = False
        # Где окну встать при первом показе; до него оно за краем экрана.
        # С подчёркиванием: публичные атрибуты js_api pywebview обходит, и .NET
        # Point уводил его в рекурсию home.Empty.Empty… на каждой загрузке.
        self._home = None
        # Показ уже просили. Сигнал трея и запасной таймер бывают раньше
        # before_show: 9 октября двойной клик сразу после входа застал форму
        # несозданной, _unpark вернуть было нечего, а _park потом увёл окно за
        # край — открыто, в панели задач есть, на экране нет.
        self._show_wanted = False
        self._park_lock = threading.Lock()
        # Статус берут и отдают странице под одним замком: опрос, кнопки и
        # проверка идут из разных потоков, и статус, взятый раньше, не ляжет
        # поверх более свежего.
        self._render_lock = threading.Lock()
        # ⏻ туннеля: мост зовёт send каждый раз в новом потоке, и запись старого
        # нажатия могла лечь поверх нового — туннель оставался не таким, как на
        # кнопке. Страница нумерует нажатия (seq), {id: последний записанный};
        # сверка и запись — под одним замком.
        self._flip_lock = threading.Lock()
        self._flip_seq = {}
        # Файл, про место которого служба спросила (ask): (имя, текст). Ответ
        # человека приходит через place_config — диалог выбора второй раз не нужен.
        self._asked = None
        # Обновление из «О программе»: версия, которую предложила проверка, и
        # проверенный установщик (путь, версия). Страница их не присылает —
        # скачивается и запускается только то, что запомнено здесь.
        self._offer = None
        self._setup = None
        self._upd_cancel = threading.Event()
        self._upd_thread = None

    # ------------------------------------------------------- приём из JS

    def send(self, name, arg=None):
        try:
            return self._dispatch(name, arg)
        except ipc.NotRunning as exc:
            self.js("failed", str(exc))
        except Exception as exc:                       # noqa: BLE001
            self.js("failed", f"{name}: {exc}")
        return None

    def _dispatch(self, name, arg):
        if name == "ready":
            self.ready.set()
            # Перезагруженная страница считает нажатия заново.
            with self._flip_lock:
                self._flip_seq.clear()
            self.refresh(full=True)
            self.reveal()
            if not self.hidden:
                self._focus()
            # Статус туннелей сразу при открытии: сама служба меряет сеть
            # только на подъёме туннеля, и прежний ответ мог устареть на часы.
            # Спрятанному окну проверка ни к чему — её сделает показ.
            if not self.hidden:
                threading.Thread(target=self._check, daemon=True).start()
        elif name == "check":
            threading.Thread(target=self._check, daemon=True).start()
        elif name == "test_config":
            threading.Thread(target=self._test_config, args=(arg,), daemon=True).start()
        elif name in ("start", "stop", "restart"):
            # Статус — сразу по ответу службы: без этого «работает» ждало
            # следующего опроса, и включение казалось дольше на POLL_EVERY.
            try:
                if name != "start":
                    reply = ipc.call("stop")
                if name != "stop":
                    reply = ipc.call("start", profile="")
                self._guard(reply)
            finally:
                self.refresh()
        elif name == "start_only":
            # ⏻ плитки при выключенном VPN: поднимает только этот туннель —
            # остальные выключаются, и общая кнопка дальше включает этот набор.
            # Флаги и start — одна команда службы под её замком: не поднялся VPN —
            # флаги как были, и между записью и стартом никто не вклинится.
            try:
                self._guard(ipc.call("start-only", tunnel=arg["tunnel"]))
            finally:
                self.refresh(full=True)
        elif name == "set_autostart":
            # Флажок лежит в каталоге службы — команда под администратором,
            # как и правка конфигов. Чем бы ни кончился UAC, флажок на
            # странице должен вернуться к настоящему состоянию.
            try:
                self._guard(self._admin_call("set-autostart", on=bool(arg)))
            finally:
                self.js("autostartDone")
                self.refresh()
        elif name == "use_config":
            # Выбор среди лежащих конфигов — без прав, как set-profile.
            self._guard(ipc.call("set-active", tunnel=arg["tunnel"], name=arg["name"]))
            self._apply()
        elif name == "set_enabled":
            # Вкл/выкл туннеля, основного тоже, — без прав, как выбор конфига; на
            # живой VPN переносит тот же _apply, что и сохранение правил. Те же
            # адреса у включённого — служба ничего не записала, замену решает человек.
            reply = self._flip(arg)
            if reply is None:
                return None
            if reply.get("clash"):
                self.js("askSwap", {"tunnel": arg["tunnel"], "clash": reply["clash"]})
                return None
            self._guard(reply)
            self._apply()
        elif name == "apply_full":
            # Подтверждённый в askFull полный перезапуск: правка уже записана.
            try:
                self._guard(ipc.call("apply"))
            finally:
                self.refresh(full=True)
        elif name == "del_config":
            self._guard(self._admin_call("remove-config", tunnel=arg["tunnel"],
                                         name=arg["name"],
                                         drop_tunnel=bool(arg.get("drop_tunnel"))))
            self.js("closeSheet")
            self._apply()
        elif name == "del_tunnel":
            self._guard(self._admin_call("remove-tunnel", tunnel=arg))
            self._apply()
        elif name == "add_config":
            self._add_config(arg or {})
        elif name == "place_config":
            self._place_config(arg or {})
        elif name == "show_config":
            self._show_conf(arg["tunnel"], arg["name"])
        elif name == "conf_details":
            # Ответ — возвратом send: страница ждёт его промисом (request).
            return self._conf_details(arg["tunnel"], arg["name"])
        elif name == "edit_config":
            # Блокнот держит поток, пока его не закроют, — мост не ждёт.
            threading.Thread(target=self._edit_conf,
                             args=(arg["tunnel"], arg["name"]), daemon=True).start()
        elif name == "move_config":
            self._guard(self._admin_call("move-config", tunnel=arg["tunnel"],
                                         name=arg["name"], to=arg["to"],
                                         drop_tunnel=bool(arg.get("drop_tunnel"))))
            self.js("closeSheet")
            self._apply()
        elif name == "set_mode":
            # «Всё остальное через этот туннель» (all) или напрямую (list у
            # основного): прежний основной служба сама переводит в list.
            self._guard(self._admin_call("set-tunnel", tunnel=arg["tunnel"],
                                         mode=arg["mode"]))
            self._apply()
        elif name == "tunnel_rules":
            self._tunnel_rules(arg)
        elif name == "save_rules":
            self._save_rules(arg)
        elif name == "validate_rules":
            # Ответ — возвратом send (request): лист проверяет поля на лету.
            return self._validate_rules(arg)
        elif name == "load_rules":
            self._load_rules()
        elif name == "pick_app":
            self._pick_app()
        elif name == "export_rules":
            self._export_rules(arg)
        elif name == "log_level":
            # Как флажок автозапуска: чем бы ни кончился UAC, галочка на
            # странице вернётся к настоящему уровню. Но только после _apply:
            # он перезапускает процессы туннелей по одному, и второй клик
            # посреди него служба отклонила бы «уже идёт».
            try:
                self._guard(self._admin_call("set-log-level",
                                             level="debug" if arg else "info"))
                self._apply()
            finally:
                self.js("logLevelDone")
        elif name == "kill_switch":
            # Служба переключает выход сама (выбор selector) — apply не нужен.
            # Галочка, как у журнала, возвращается к настоящему значению при любом UAC.
            try:
                self._guard(self._admin_call("set-kill-switch", on=bool(arg)))
            finally:
                self.refresh(full=True)
                self.js("killSwitchDone")
        elif name == "logs":
            self._push_logs()
        # Вкладка «Настройки»: ответы — возвратом send (request).
        elif name == "settings_info":
            return self._settings_info()
        elif name == "set_tray_autostart":
            return self._set_tray_autostart(bool(arg))
        elif name == "diagnostics":
            return self._diagnostics()
        elif name == "open_logs":
            return self._open_logs()
        elif name == "check_update":
            return self._check_update()
        elif name == "update_download":
            return self._update_download()
        elif name == "update_cancel":
            self._upd_cancel.set()
        elif name == "update_install":
            return self._update_install()
        elif name == "update_outcome":
            return {"ok": True, "pending": update.pending()}
        elif name == "update_seen":
            # Итог показан. Пока setup работает, метку не трогаем: по ней трей
            # после обновления откроет окно.
            p = update.pending()
            if p and p["state"] != "installing":
                update.clear()
        elif name == "open_url":
            # Адрес приходит со страницы: наружу — только свой репозиторий.
            if not settings.repo_url(arg):
                return {"ok": False, "error": "чужая ссылка → окно открывает только страницу TunnelVPN на GitHub"}
            return self._shell_open(arg)
        elif name in ("rendered", "jserror"):
            # Диагностика страницы. jserror приходит, когда упала отрисовка, —
            # без него исключение внутри WebView2 не видно вообще нигде.
            if name == "jserror":
                self._log(f"js: {arg}")
        return None

    def reveal(self):
        """Показывает холодное окно — один раз: зовут и ready, и запасной
        таймер open_window через COLD_SHOW_WAIT."""
        if not self.cold:
            return
        self.cold = False
        w = self.holder.get("window")
        if w is not None:
            _unpark(w, self)
            w.show()
            _to_front(w)

    def _focus(self):
        """Карточка туннеля, по которому щёлкнули в меню трея: id из
        paths.FOCUS_FILE, файл забираем — следующий показ без клика по
        туннелю прокручивать не должен. Зовётся, когда карточки уже нарисованы.
        FOCUS_ABOUT — не карточка, а «О программе» (settings.js:showAbout)."""
        try:
            with open(paths.FOCUS_FILE, encoding="utf-8") as fh:
                tid = fh.read().strip()
            os.remove(paths.FOCUS_FILE)
        except OSError:
            return
        if tid == FOCUS_ABOUT:
            self.js("showAbout")
        elif tid:
            self.js("focusCard", tid)

    def _flip(self, arg):
        """set-enabled по нажатию ⏻. None — нажатие устарело: по этому туннелю
        уже записано более позднее, и на VPN его переносит его же _apply."""
        tid, seq = arg["tunnel"], arg.get("seq")
        with self._flip_lock:
            if seq is not None:
                if seq <= self._flip_seq.get(tid, 0):
                    return None
                self._flip_seq[tid] = seq
            return ipc.call("set-enabled", tunnel=tid, on=bool(arg["on"]),
                            replace=bool(arg.get("replace")))

    @staticmethod
    def _guard(reply):
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error") or "служба отказала")

    def _apply(self):
        """Правка туннелей — на живой VPN через службу (решение #14): она
        перезапускает только процесс туннеля со сменившимся конфигом. Сменилось
        то, что держит основной процесс, — служба не перезапускает сама (ask),
        а страница спрашивает человека (askFull): сеть упадёт для всех приложений.
        Выключенный не включает."""
        try:
            try:
                reply = ipc.call("apply", ask=True)
                if reply.get("applied") == "ask":
                    self.js("askFull")
                self._guard(reply)
            except ipc.NotRunning:
                pass
        finally:
            self.refresh(full=True)

    # ------------------------------------------------------------- права

    def _admin_call(self, op, **payload):
        """Команда, которая пишет или читает conf\\ — там приватные ключи.

        Канал (ipc.Server) требует для таких команд администратора: обычный
        пользователь на многопользовательской машине не должен доставать чужие
        ключи через именованный канал, даже если сам он умеет к нему постучаться.

        Окно намеренно не элевировано целиком — иначе UAC спрашивал бы себя
        при каждом открытии окна, хотя конфиги правят редко. Поэтому права
        просим точечно, ровно на это одно действие: один короткий процесс,
        одно окно UAC, готовый результат — и он сразу же завершается.
        """
        if _is_admin():
            # Портативная версия элевирована с самого первого запуска —
            # выпрашивать права ещё раз значит просто мучить пользователя
            # лишним UAC-окном без всякой пользы.
            return ipc.call(op, **payload)

        with tempfile.TemporaryDirectory() as td:
            payload_file = os.path.join(td, "payload.json")
            result_file = os.path.join(td, "result.json")
            with open(payload_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)

            if getattr(sys, "frozen", False):
                exe, base_args = sys.executable, []
            else:
                exe, base_args = sys.executable, ["-m", "tunnelvpn.cli"]
            _run_elevated(exe, base_args + ["admin-op", op, payload_file, result_file])

            if os.path.isfile(result_file):
                with open(result_file, encoding="utf-8") as fh:
                    return json.load(fh)
            # UAC отменили или дочерний процесс не успел записать ответ —
            # оба исхода снаружи выглядят одинаково: действие не выполнено.
            return {"ok": False, "error": "запрос прав отменён или не выполнился"}

    # ------------------------------------------------------- вызовы в JS

    def js(self, fn, arg=None):
        w = self.holder.get("window")
        if w is None:
            return
        payload = json.dumps(arg, ensure_ascii=False, default=str)
        try:
            w.evaluate_js(f"call({json.dumps(fn)}, {payload})")
        except Exception:
            # Окно могли закрыть между проверкой и вызовом — это не ошибка.
            pass

    def jsn(self, fn, args):
        """Вызов функции страницы с несколькими аргументами."""
        w = self.holder.get("window")
        if w is None:
            return
        payload = json.dumps(list(args), ensure_ascii=False, default=str)
        try:
            w.evaluate_js(f"callN({json.dumps(fn)}, {payload})")
        except Exception:
            pass

    # ---------------------------------------------------------- обновление

    def refresh(self, full=False):
        """Гонит в страницу свежее состояние. Зовётся из потока опроса."""
        with self._render_lock:
            try:
                st = ipc.call("status").get("status", {})
            except ipc.NotRunning:
                # У портативной службы нет и не будет: канал держит её трей, и
                # «Нужна установка» звала бы ставить то, что ей не нужно.
                if portable.is_portable():
                    self.js("render", {"up": False, "no_service": True, "portable": True})
                else:
                    self.js("render", {"up": False, "no_service": True,
                                        "daemon": _service_installed()})
                return
            self.js("render", st)
        if full:
            self.js("renderVersion", {"app": st.get("version", paths.version()),
                                      "singbox": st.get("singbox", "")})
            self.js("renderHowto", self._howto())
            self._push_logs()

    def _check(self):
        """Проверка туннелей службой сейчас; страница на это время пишет
        «проверяю…» — у каждой плитки, пока не проверен её туннель."""
        self.js("checkStart")

        def on_side(st, pending):
            self.jsn("checkSides", (st, sorted(pending)))

        # Итог — свежим статусом под замком, а не статусом из ответа check:
        # тот собран раньше и мог лечь поверх статуса опроса.
        try:
            ipc.check_by_side(on_side, lock=self._render_lock)
        except ipc.NotRunning:
            pass
        finally:
            self.refresh()
            self.js("checkDone")

    def _test_config(self, arg):
        """↻ на плитке: служба проверяет один конфиг и держит ответ до итога —
        секунды, поэтому поток, а не мост. Итог страница берёт из статуса
        (tunnels[].tests); статус — раньше testDone, иначе мигнёт прошлый ответ."""
        try:
            self._guard(ipc.call("test-config", tunnel=arg["tunnel"], name=arg["name"]))
        except Exception as exc:                       # noqa: BLE001
            self.js("failed", str(exc))
        finally:
            self.refresh()
            self.js("testDone", arg)

    @staticmethod
    def _howto():
        """Сводка «Сейчас подключено» для renderHowto. Собранные конфиги в
        state\\run\\ закрыты — читает служба; окну без прав она не отдаёт подсети и
        домены туннелей."""
        try:
            reply = ipc.call("howto")
        except ipc.NotRunning:
            reply = {}
        return reply.get("howto") or {"endpoints": []}

    def _push_logs(self):
        self.js("renderLogs", ipc.call("log", lines=400).get("lines", []))

    # ---------------------------------------------------------- настройки

    @staticmethod
    def _settings_info():
        """Что вкладке «Настройки» не узнать из статуса. У портативной нет ни
        службы, ни задачи трея — None, группа «Запуск» их не показывает.
        Задачи у установленной нет (галочку сняли при установке) — трей при
        входе выключен: включение поставит задачу."""
        port = portable.is_portable()
        return {"ok": True, "portable": port, "admin": _is_admin(),
                "service": None if port else settings.service_state(),
                "tray_autostart": None if port else bool(settings.tray_task()),
                "data_dir": paths.DATA, "logs_dir": paths.LOGS,
                "repo": settings.REPO_URL}

    def _check_update(self):
        """Проверка по кнопке. install — скачать и поставить можно отсюда:
        подписанная установленная сборка (update.can_install) и окно с правами —
        писать в state\\update\\ может только администратор. Иначе страница даёт
        только ссылку на релиз. downtime — сколько VPN пролежит, оценка."""
        r = settings.check_update(paths.version())
        install = r.get("state") == "update" and _is_admin() and update.can_install()
        self._offer = r["latest"] if install else None
        return {"ok": True, **r, "install": install, "downtime": update.DOWNTIME}

    def _update_download(self):
        """«Скачать и установить»: версия из последней проверки. Качает поток;
        ход и итог страница получает вызовами updateProgress, updateStage,
        updateReady, updateFailed."""
        version = self._offer
        if not version:
            return {"ok": False, "error": "обновление не предложено → сначала проверьте обновления"}
        if self._upd_thread is not None and self._upd_thread.is_alive():
            return {"ok": True}
        self._setup = None
        # Свой Event на каждое скачивание: «Отмена» прошлого не задевает новое.
        self._upd_cancel = threading.Event()
        self._upd_thread = threading.Thread(target=self._download,
                                            args=(version, self._upd_cancel), daemon=True)
        self._upd_thread.start()
        return {"ok": True}

    def _download(self, version, cancel):
        last = [0.0]

        def progress(got, total):
            now = time.monotonic()
            if now - last[0] >= PROGRESS_EVERY or got == total:
                last[0] = now
                self.js("updateProgress", {"got": got, "total": total})

        try:
            path = update.prepare(version, progress, cancel,
                                  stage=lambda name: self.js("updateStage", name))
        except update.Cancelled:
            self.js("updateFailed", {"stage": "cancel", "text": ""})
            return
        except Exception as exc:                       # noqa: BLE001
            # stage verify — файл не прошёл проверку и уже удалён (update.prepare),
            # страница пишет об этом сама. Подробность — только в журнал.
            self._log(f"обновление {version}: {exc}")
            stage = getattr(exc, "stage", "download")
            text = str(exc) if stage == "verify" else DOWNLOAD_FAILED
            self.js("updateFailed", {"stage": stage, "text": text})
            return
        self._setup = (path, version)
        self.js("updateReady", {"version": version})

    def _update_install(self):
        """«Установить сейчас»: только файл, проверенный в этом окне. Дальше
        setup снимет и окно, и трей; до того страница держит «Обновляю TunnelVPN…»."""
        if not self._setup or not os.path.isfile(self._setup[0]):
            self._setup = None
            return {"ok": False, "error": "файла обновления нет → скачайте его снова"}
        path, version = self._setup
        try:
            update.launch(path, version)
        except update.UpdateError as exc:
            self._log(f"обновление {version}: {exc}")
            return {"ok": False, "error": "Установщик не запустился → скачайте обновление снова"}
        self._setup = None
        return {"ok": True}

    @staticmethod
    def _set_tray_autostart(on):
        """Трей при входе — задача планировщика с наивысшими правами: менять
        её может только администратор, UAC — на одно это действие. Итог судим
        по задаче после команды: отказ в UAC снаружи не виден иначе."""
        exists = settings.tray_task() is not None
        if not exists and not on:
            return {"ok": True, "tray_autostart": False}
        exe = os.path.join(os.path.dirname(sys.executable), settings.TRAY_EXE)
        if not exists and not getattr(sys, "frozen", False):
            return {"ok": False, "error": "задачи трея нет → в версии из исходников её ставит установщик"}
        ps = settings.system32("WindowsPowerShell", "v1.0", "powershell.exe")
        args = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-EncodedCommand", settings.tray_task_command(on, exists, exe)]
        if _is_admin():
            subprocess.run([ps] + args, capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            _run_elevated(ps, args)
        now = bool(settings.tray_task())
        if now != on:
            return {"ok": False, "tray_autostart": now,
                    "error": "запрос прав отменён или не выполнился"}
        return {"ok": True, "tray_autostart": now}

    def _diagnostics(self):
        try:
            st = ipc.call("status").get("status") or {}
        except ipc.NotRunning:
            st = {}
        return {"ok": True, "text": settings.diagnostics(st, self._settings_info())}

    def _open_logs(self):
        if not os.path.isdir(paths.LOGS):
            return {"ok": False, "error": "папки журналов ещё нет → она появится после первого включения VPN"}
        return self._shell_open(paths.LOGS)

    @staticmethod
    def _shell_open(target):
        """Папка или ссылка — через explorer.exe: он передаёт её уже
        работающей оболочке пользователя. os.startfile из окна с правами
        администратора поднял бы браузер тоже с правами."""
        try:
            subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                           "explorer.exe"), target])
        except OSError as exc:
            return {"ok": False, "error": f"не открылось: {exc}"}
        return {"ok": True}

    # ------------------------------------------------------------ конфиги

    def _show_conf(self, tunnel, name):
        """Показывает содержимое конфига. Читает служба — каталог закрыт."""
        reply = self._admin_call("read-config", tunnel=tunnel, name=name)
        if reply.get("ok"):
            self.jsn("showConf", [tunnel, name, reply.get("text", "")])
        else:
            self.js("failed", reply.get("error") or "не прочитать конфиг")

    def _edit_conf(self, tunnel, name):
        """Открывает конфиг в Блокноте с правами: каталог conf\\ закрыт для
        пользователя. Свой редактор в окне не делаем, как и на macOS.

        Путь собирается только из id и имени, которые служба сама отдала в
        статусе: они приходят со страницы, и «..\\» вывел бы Блокнот с правами
        за пределы conf\\.
        """
        try:
            st = ipc.call("status").get("status", {})
        except ipc.NotRunning as exc:
            self.js("failed", str(exc))
            return
        t = next((x for x in st.get("tunnels") or [] if x["id"] == tunnel), None)
        if t is None or name not in (t.get("confs") or []):
            self.js("failed", f"нет конфига «{name}»")
            return
        # Полный путь: с правами запускается то, что найдётся по имени, а
        # PATH и текущий каталог пишет и обычный пользователь.
        notepad = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                               "System32", "notepad.exe")
        _run_elevated(notepad, [tunnels.conf_path(t["id"], name)], show=True)
        # Был ли файл изменён, окну не узнать — каталог закрыт. Перезапуск
        # нужен только выбранному: запасной в сборку не идёт.
        try:
            if name == t.get("active"):
                self._apply()
            else:
                self.refresh(full=True)
        except Exception as exc:                       # noqa: BLE001
            self.js("failed", f"edit_config: {exc}")

    def _pick(self, file_types):
        """Путь из диалога открытия файла; None — передумали."""
        import webview
        picked = self.holder.get("window").create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False, file_types=file_types)
        return picked[0] if picked else None

    def _add_config(self, arg):
        """Диалог выбора файла и передача его службе.

        arg пустой — место по AllowedIPs выбирает служба (_place); {tunnel} —
        в этот туннель; {tunnel, place: "replace"} — «Заменить файл…» у плитки.
        Файл читаем здесь, от пользователя: у него есть доступ к своим папкам,
        а у службы под SYSTEM его может не быть — сетевой диск или профиль
        другого пользователя ей просто не видны.
        """
        path = self._pick(_FILE_TYPES)
        if path:
            self._add_file(path, arg.get("tunnel", ""), arg.get("place", ""))

    def _add_file(self, path, tunnel="", place=""):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        # Имя чистит служба (tunnels.safe_name).
        name = os.path.splitext(os.path.basename(path))[0]
        self._send_config(name, text, tunnel, place)

    def _drop(self, event):
        """Файл, брошенный на окно: полный путь pywebview кладёт в
        pywebviewFullPath (WebView2 отдаёт его отдельным сообщением). Дальше —
        как «Добавить конфиг…» без туннеля: место выбирает служба. Из
        нескольких .conf берём первый — вопрос о месте задаётся по одному."""
        files = (event.get("dataTransfer") or {}).get("files") or []
        confs = [f["pywebviewFullPath"] for f in files
                 if (f.get("pywebviewFullPath") or "").lower().endswith(".conf")]
        try:
            if not confs:
                raise RuntimeError("Нужен файл .conf WireGuard или AmneziaWG")
            self._add_file(confs[0])
        except Exception as exc:                       # noqa: BLE001
            # Поток pywebview: исключение отсюда никто бы не увидел.
            self.js("failed", str(exc))

    def _conf_details(self, tunnel, name):
        """«Подробности» конфига: поля файла без ключей. Читает служба, как и
        «Показать», — каталог закрыт."""
        reply = self._admin_call("read-config", tunnel=tunnel, name=name)
        if not reply.get("ok"):
            return {"ok": False, "error": reply.get("error") or "не прочитать конфиг"}
        try:
            facts = _conf_summary(reply.get("text", ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": tunnels.conf_path(tunnel, name), **facts}

    def _place_config(self, arg):
        """Ответ на вопрос службы о месте: place "replace" (с tunnel) или "new"."""
        asked, self._asked = self._asked, None
        if asked is None or arg.get("place") not in ("replace", "new"):
            return
        self._send_config(*asked, arg.get("tunnel", ""), arg["place"])

    def _send_config(self, name, text, tunnel, place):
        reply = self._admin_call("add-config", name=name, text=text,
                                 tunnel=tunnel, place=place)
        if reply.get("ask"):
            # Похоже на конфиг туннеля «по списку»: заменить его или отдельным
            # туннелем — решает человек; служба при ask ничего не записала.
            self._asked = (name, text)
            self.js("askPlace", {**reply["ask"], "file": name})
            return
        if not reply.get("ok"):
            self.js("failed", reply.get("error") or "не удалось добавить")
            return
        self._apply()

    # ------------------------------------------------------ туннелирование

    def _tunnel_rules(self, tunnel):
        """Лист «Туннелирование»: списки — только у администратора (get-tunnels),
        в них адреса и домены рабочей сети."""
        reply = self._admin_call("get-tunnels")
        self._guard(reply)
        t = next((x for x in reply.get("tunnels") or [] if x["id"] == tunnel), None)
        if t is None:
            raise RuntimeError(f"нет туннеля {tunnel}")
        self.js("showRules", {k: t[k] for k in ("id", "name", "mode", "active",
                                                *_RULE_KEYS)})

    def _save_rules(self, arg):
        """Поля листа — текстом, как их ввёл человек: разбирает служба
        (routelist.parse), непонятое возвращается в rejected и стоит под полем."""
        lists = {k: arg[k] for k in _RULE_KEYS if k in arg}
        reply = self._admin_call("set-tunnel", tunnel=arg["tunnel"], **lists)
        if not reply.get("ok"):
            # Строка состояния — под листом: отказ («a.ru уже в «Работа»»)
            # стоит под полями — по problems у своего поля, лист остаётся
            # открытым с набранным.
            self.js("rulesFailed", {"error": reply.get("error") or "служба отказала",
                                    "problems": _locate(reply.get("problems", []), lists)})
            return
        if reply.get("moved"):
            # Без «пускать» ушёл запасным к основному: туннеля, чей это лист, нет.
            self.js("rulesMoved", reply["moved"])
            self._apply()
            return
        # mode — туннель без «пускать» мог стать основным: у листа одно поле.
        self.js("rulesSaved", {"include": reply.get("include", []),
                               "exclude": reply.get("exclude", []),
                               "exclude_apps": reply.get("exclude_apps", []),
                               "rejected": reply.get("rejected", {}),
                               "mode": reply.get("mode", "")})
        self._apply()

    def _validate_rules(self, arg):
        """Поля листа на лету: разбор — здесь же, тем же routelist, что у службы,
        без канала и прав. Дубли с другими туннелями (check-rules) — только когда
        окно уже с правами и разбор чистый: иначе UAC спрашивал бы на каждой
        паузе ввода; без прав их покажет «Сохранить». fate — куда туннель уйдёт
        без «пускать» (Core._check_rules)."""
        texts = {k: str(arg.get(k) or "") for k in ("include", "exclude") if k in arg}
        fields = {k: routelist.check(v) for k, v in texts.items()}
        if "exclude_apps" in arg:
            # Программы с другими туннелями не сверяются: check-rules им не нужен.
            fields["exclude_apps"] = routelist.check_apps(str(arg["exclude_apps"] or ""))
        out = {"ok": True, "fields": fields, "max": routelist.MAX_RULES,
               "max_apps": routelist.MAX_APPS, "fate": {}}
        if not texts or not _is_admin() or any(f["problems"] for f in fields.values()):
            return out
        try:
            reply = ipc.call("check-rules", tunnel=arg["tunnel"], **texts)
        except ipc.NotRunning:
            return out
        # Чего нет в листе (поле «пускать» у основного) — под первым полем.
        first = next(iter(fields))
        if not reply.get("ok"):
            # Нет туннеля, больше MAX_RULES — то же скажет «Сохранить»; лист не
            # говорит, что всё чисто.
            fields[first]["problems"].append({"line": None, "token": "",
                                              "text": reply.get("error") or "служба отказала"})
            return out
        for p in _locate(reply.get("problems", []), texts):
            field = p.pop("field")
            fields[field if field in fields else first]["problems"].append(p)
        out["fate"] = reply.get("fate") or {}
        return out

    def _pick_app(self):
        """«Добавить программу…»: путь к .exe из проводника — в поле листа.
        Записывает не он, а «Сохранить»: выбор — такая же правка поля, как ввод."""
        path = self._pick(_APP_TYPES)
        if path:
            self.js("appPicked", routelist.app(path) or path)

    def _load_rules(self):
        path = self._pick(_RULES_TYPES)
        if not path:
            return
        with open(path, encoding="utf-8", errors="replace") as fh:
            self.js("fillRules", _rules_from_file(fh.read()))

    def _export_rules(self, arg):
        """Сохранённые списки туннеля — в JSON, который «Загрузить из файла…»
        примет обратно, в том числе у другого туннеля."""
        import webview
        picked = self.holder.get("window").create_file_dialog(
            webview.FileDialog.SAVE, file_types=_RULES_TYPES,
            save_filename=f"{tunnels.safe_name(arg.get('name', ''))}.json")
        if not picked:
            return
        out = {k: [str(x) for x in arg.get(k) or []] for k in _RULE_KEYS if k in arg}
        with open(picked[0], "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    def _log(self, line):
        try:
            paths.ensure_dirs()
            with open(os.path.join(paths.LOGS, "window.log"), "a",
                      encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        except OSError:
            pass


def _locate(problems, texts):
    """Отказы службы [{field, text, entry}] — с номером строки и токеном записи
    entry в тексте своего поля, как у непонятого (routelist.check): лист по ним
    выделяет запись. Не нашлась или entry нет — line None."""
    out = []
    for p in problems:
        field, entry = p.get("field", ""), p.get("entry")
        hit = next(((n, tok) for n, tok, found in routelist.tokens(texts.get(field, ""))
                    if entry and found == entry), (None, ""))
        out.append({"field": field, "text": p.get("text", ""),
                    "line": hit[0], "token": hit[1]})
    return out


def _conf_summary(text):
    """Поля конфига для «Подробностей»: всё, кроме ключей. mtu — как его
    соберёт служба: без MTU в файле — 1280, у AWG с junk-параметрами — не
    больше 1280 (buildconfig.endpoint); cut — срезан. problem — почему
    конфиг не соберётся. Не конфиг — ValueError."""
    from . import buildconfig
    conf = buildconfig.parse_text(text)
    iface, peer = conf["interface"], conf["peer"]
    # Имена полей — как в файле: parse_text приводит их к нижнему регистру.
    spelled = {}
    for raw in text.splitlines():
        key = raw.split("#", 1)[0].split("=", 1)[0].strip()
        spelled.setdefault(key.lower(), key)

    def num(key):
        try:
            return int(iface.get(key, ""))
        except ValueError:
            return None
    mtu = num("mtu")
    built = mtu or buildconfig.AWG_MTU_MAX
    cut = built > buildconfig.AWG_MTU_MAX and any(num(k) for k in buildconfig.AWG_JUNK)
    return {
        "awg": buildconfig.is_awg(conf),
        "endpoint": peer.get("endpoint", ""),
        "address": buildconfig.split_list(iface.get("address", "")),
        "dns": buildconfig.split_list(iface.get("dns", "")),
        "allowed": buildconfig.split_list(peer.get("allowedips", "")),
        "keepalive": peer.get("persistentkeepalive", ""),
        "mtu": buildconfig.AWG_MTU_MAX if cut else built,
        "mtu_file": mtu,
        "cut": cut,
        # HeaderProtectionKey (AWG 3.x) — ключ, как PrivateKey: не отдаём.
        "mask": [[spelled.get(k, k), iface[k]] for k in buildconfig.AWG_KEYS
                 if k in iface and k not in buildconfig.AWG3_STR],
        "problem": buildconfig.check_conf_text(text) or "",
    }


def _rules_from_file(text):
    """Поля листа «Туннелирование» из файла: JSON экспорта {include, exclude}
    раскладывается по полям, любой другой текст — {text}: страница кладёт его
    в первое поле («пускать», у основного — «мимо VPN»). exclude_apps — только
    если есть в файле: экспорт туннеля «по списку» его не пишет."""
    try:
        data = json.loads(text.lstrip("﻿"))
    except ValueError:
        data = None
    if not isinstance(data, dict) or not ({"include", "exclude"} & data.keys()):
        return {"text": text}
    out = {}
    for key in ("include", "exclude", *(("exclude_apps",) if "exclude_apps" in data else ())):
        val = data.get(key)
        out[key] = ("\n".join(str(x) for x in val) if isinstance(val, list)
                    else val if isinstance(val, str) else "")
    return out


# ------------------------------------------------------------ site.env

def _parse_env(text):
    return paths.parse_env(text)


def _format_env(values):
    """Собирает site.env обратно. Пустые поля не пишем — они значат «не задано»."""
    lines = ["# Настройки рабочей сети. Правится окном, но можно и руками.", ""]
    for key in ("CORP_DOMAINS", "CORP_PROBE", "CORP_HOSTS", "SB_CORP_EXCLUDE",
                "SB_LOG_LEVEL"):
        val = (values.get(key) or "").strip()
        if val:
            lines.append(f'{key}="{val}"')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- запуск

def _dark_title(window):
    """Тёмный заголовок окна независимо от темы Windows.

    pywebview красит заголовок по AppsUseLightTheme, а страница всегда
    тёмная: в светлой теме над тёмным окном висела бы белая полоса.
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20, с Windows 10 20H1. При смене темы
    Windows pywebview вернёт светлый — до следующего открытия окна.
    """
    try:
        import ctypes
        hwnd = window.native.Handle.ToInt32()
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:
        pass


def _park(window, api):
    """Форма — за край экрана до первого настоящего показа.

    Спрятанное окно pywebview создаёт так: Opacity=0, Show, Hide. Невидимым
    оно не выходит: после UAC сначала мелькало белое пред-окно, потом само
    окно. За краем экрана этот показ никто не видит. Зовётся в before_show:
    оно идёт в потоке окна до этого Show. Место в центре рабочего стола
    запоминаем — его вернёт _unpark. Показ уже просили — не уводим: вернуть
    окно было бы некому.
    """
    with api._park_lock:
        if api._show_wanted:
            return
        try:
            from System.Drawing import Point
            from System.Windows.Forms import FormStartPosition, Screen
            form = window.native
            area = Screen.PrimaryScreen.WorkingArea
            api._home = Point(area.X + max(0, area.Width - form.Width) // 2,
                              area.Y + max(0, area.Height - form.Height) // 2)
            form.StartPosition = FormStartPosition.Manual
            form.Location = Point(-32000, -32000)
        except Exception:
            api._home = None


def _unpark(window, api):
    """Перед первым показом — на место из _park. Дальше окно стоит там,
    куда его передвинули. Замок — только на флаг и место: Invoke под ним
    ждал бы поток окна, а тот может ждать замок в _park."""
    with api._park_lock:
        api._show_wanted = True
        home, api._home = api._home, None
    if home is None:
        return
    try:
        from System import Action
        form = window.native
        form.Invoke(Action(lambda: setattr(form, "Location", home)))
    except Exception:
        pass


def _devtools_port():
    """Порт DevTools из DEVTOOLS_PORT_ENV; None — порт не открываем.

    Только из исходников: в сборке переменную не читаем — через открытый порт
    любой локальный процесс управлял бы окном.
    """
    if getattr(sys, "frozen", False):
        return None
    try:
        return int(os.environ.get(DEVTOOLS_PORT_ENV, ""))
    except ValueError:
        return None


def _source_icon():
    """Значок окна, запущенного из исходников. None — остаётся значок exe.

    pywebview берёт значок окна из sys.executable. В сборке это tunnelvpn.exe
    со своим ico, а из исходников — python.exe, и окно с кнопкой в панели
    задач показывали значок Python. Свой AppUserModelID нужен затем же: без
    него панель задач складывает окно в одну группу с python.exe и берёт
    значок оттуда. В сборке ID не задаём: ярлык из установщика его не знает,
    и закреплённый значок разошёлся бы с окном.
    """
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Enkeym.TunnelVPN")
    except Exception:
        pass
    try:
        from . import icon
        return icon.write_ico(os.path.join(tempfile.gettempdir(), "TunnelVPN-window.ico"))
    except Exception:
        return None


def _page_url(index):
    """Путь страницы с меткой её версии: обновлённая сборка — новый адрес.

    pywebview отдаёт страницу своим сервером на постоянном порту (без
    private_mode), и его no-store теряется: bottle.static_file возвращает
    свой ответ с одним Last-Modified. WebView2 по эвристике брал index.html
    из дискового кэша storage_path — после обновления окно было прежним.
    Метка — по самому свежему файлу вёрстки: страница передаёт её app.css и
    js/*.js, иначе из кэша пришли бы они.
    """
    stamps = []
    for dirpath, dirnames, filenames in os.walk(os.path.dirname(index)):
        dirnames[:] = [d for d in dirnames if d != "dev"]
        for f in filenames:
            try:
                stamps.append(os.stat(os.path.join(dirpath, f)).st_mtime_ns)
            except OSError:
                pass
    return f"{index}?v={max(stamps)}" if stamps else index


def _storage_path():
    """Своя папка данных WebView2, отдельная для прав администратора.

    По умолчанию pywebview кладёт её в общий %APPDATA%\\pywebview. Папку,
    которую уже занял браузер с другим уровнем прав или другими опциями,
    WebView2 открыть не может: инициализация кончается IsSuccess=False,
    pywebview это лишь пишет в лог, и окно остаётся серым и пустым.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "TunnelVPN",
                        "WebView2-" + ("admin" if _is_admin() else "user"))


def _hide_on_close(window, api):
    """Крестик прячет окно, а не закрывает процесс: следующий показ мгновенный.

    Только для закрытия пользователем: выход из системы и выключение
    отменять нельзя, иначе Windows покажет «приложение мешает завершению».
    Зовётся в потоке окна (before_show), туда же приходит FormClosing.
    """
    from System.Windows.Forms import CloseReason

    def closing(_sender, args):
        if args.CloseReason != CloseReason.UserClosing:
            return
        args.Cancel = True
        api.hidden = True
        window.native.Hide()

    window.native.FormClosing += closing


def _listen_drop(window, api):
    """Перетаскивание .conf на окно. Подписка — из Python: только его
    обработчик получает полный путь файла. Оверлей и dragover страница
    держит сама (js/sheets.js)."""
    try:
        from webview.dom import DOMEventHandler
        window.dom.document.events.drop += DOMEventHandler(api._drop, prevent_default=True)
    except Exception as exc:                               # noqa: BLE001
        api._log(f"перетаскивание недоступно: {exc}")


SW_RESTORE = 9
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
# SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
SWP_KEEP = 0x0001 | 0x0002 | 0x0010 | 0x0040


def _user32():
    """user32 и kernel32 с типами аргументов: HWND_TOPMOST (-1) без них ушёл
    бы 32-битным int, и на x64 SetWindowPos получил бы не тот дескриптор."""
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32")
    for name, args in (("IsIconic", [wintypes.HWND]),
                       ("ShowWindow", [wintypes.HWND, ctypes.c_int]),
                       ("SetForegroundWindow", [wintypes.HWND]),
                       ("BringWindowToTop", [wintypes.HWND]),
                       ("SetWindowPos", [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         wintypes.UINT]),
                       ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.c_void_p]),
                       ("AttachThreadInput", [wintypes.DWORD, wintypes.DWORD,
                                              wintypes.BOOL])):
        getattr(u, name).argtypes = args
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    return u, ctypes.WinDLL("kernel32")


def _raise(hwnd, user32, kernel32):
    """Свёрнутое — развернуть, не первое на экране — вывести вперёд. Только в
    потоке окна: AttachThreadInput сцепляет ввод именно этого потока.

    SW_RESTORE, а не restore() pywebview: тот ставит Normal и уменьшил бы
    окно, развёрнутое до сворачивания на весь экран.

    Activate в window.show() — это SetForegroundWindow, и Windows его молча
    отклоняет, если право на передний план от трея не дошло: щелчок или
    клавиша между кликом по значку и показом его сбрасывают. 11 октября
    прогретое окно так открылось под редактором. Поднять по z-порядку можно
    и без права — TOPMOST и обратно; фокус — сцепив ввод с потоком окна,
    которое сейчас впереди.
    """
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.GetForegroundWindow() == hwnd:
        return
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_KEEP)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_KEEP)
    if user32.SetForegroundWindow(hwnd):
        return
    fg = user32.GetForegroundWindow()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    own = kernel32.GetCurrentThreadId()
    if not other or other == own or not user32.AttachThreadInput(own, other, True):
        return
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(own, other, False)


def _to_front(window):
    """_raise в потоке окна, после window.show()."""
    try:
        from System import Action
        form = window.native
        hwnd = form.Handle.ToInt32()
        user32, kernel32 = _user32()
        form.Invoke(Action(lambda: _raise(hwnd, user32, kernel32)))
    except Exception:                                      # noqa: BLE001
        pass


def _show(window, api):
    """Показ прогретого окна по сигналу трея: свежий статус и проверка
    туннелей — как при первом открытии.

    Уже открытое (в том числе свёрнутое) только выходит вперёд: его статус
    держит опрос, а перерисовка с проверкой на повторный двойной клик по
    значку выглядела как открытие заново — кружки снова рыжие.
    """
    on_screen = not api.hidden and not api.cold
    api.hidden = False
    api.cold = False
    _unpark(window, api)
    window.show()
    _to_front(window)
    # Страница ещё грузится — всё это сделает её ready.
    if not on_screen and api.ready.is_set():
        api.refresh(full=True)
        threading.Thread(target=api._check, daemon=True).start()
    if api.ready.is_set():
        api._focus()


def open_window(resident=False, hidden=False):
    """Открывает окно; когда его закроют, завершает процесс — не возвращается.

    resident — окно трея: крестик прячет его, процесс живёт до выхода из трея,
    а показывает окно снова сигнал instance.WINDOW_SHOW. hidden — запуск
    спрятанным, прогрев: WebView2 и страница поднимаются заранее.
    """
    import webview

    holder = {}
    api = Api(holder)
    api.hidden = hidden
    api.cold = not hidden
    # paths.UI_DIR, а не __file__: в собранном виде __file__ у модуля внутри
    # PyInstaller-архива не указывает на реальный файл на диске, и index.html
    # не находился — окно падало ещё до показа.
    index = os.path.join(paths.UI_DIR, "index.html")

    holder["window"] = webview.create_window(
        f"TunnelVPN {paths.version()}", _page_url(index),
        # Спрятанным создаётся и видимое окно: покажет его Api.reveal.
        # 720×520: карточки в одну колонку, кнопки шапки — значками с подсказкой.
        js_api=api, width=1040, height=720, min_size=(720, 520), hidden=True,
        # Тот же фон, что --bg в index.html: иначе до загрузки страницы окно
        # белое и мигает на открытии.
        background_color="#101012")
    holder["window"].events.shown += lambda: _dark_title(holder.get("window"))
    holder["window"].events.before_show += lambda window: _park(window, api)
    holder["window"].events.loaded += lambda window: _listen_drop(window, api)
    if resident:
        holder["window"].events.before_show += lambda window: _hide_on_close(window, api)
        # Слушаем до webview.start: сигнал, пришедший, пока WebView2 ещё
        # стартует, дождётся окна в window.show().
        try:
            instance.listen(instance.WINDOW_SHOW,
                            lambda: _show(holder["window"], api))
        except OSError as exc:
            # Окно всё равно нужно; не показавшееся по сигналу трей заменит.
            api._log(f"сигнал показа недоступен: {exc}")
    icon = None if getattr(sys, "frozen", False) else _source_icon()
    port = _devtools_port()
    if port:
        webview.settings["REMOTE_DEBUGGING_PORT"] = port

    def poll():
        # Ждём, пока страница отчитается о готовности: до этого window[fn]
        # ещё не определены, и любой вызов ушёл бы в пустоту.
        if not api.ready.wait(timeout=READY_WAIT):
            api._log(f"страница не ответила за {READY_WAIT:.0f} с — WebView2 "
                     "не поднялся, окно перезапускается")
            os._exit(READY_LOST)
        while holder.get("window") is not None:
            # Спрятанному окну опрос не нужен — свежее состояние даст показ.
            if not api.hidden:
                api.refresh()
            time.sleep(POLL_EVERY)

    threading.Thread(target=poll, daemon=True).start()
    if api.cold:
        # Запасной показ: страница не ответила или refresh на ready ждёт службу
        # (ipc.call без таймаута). Невидимое окно было бы нечем закрыть.
        timer = threading.Timer(COLD_SHOW_WAIT, api.reveal)
        timer.daemon = True
        timer.start()
    # gui='edgechromium' — WebView2, он есть в Windows 10/11 из коробки.
    webview.start(gui="edgechromium", private_mode=False, icon=icon,
                  storage_path=_storage_path())
    holder["window"] = None
    # Окно закрыто — процесс тоже. WebView2 ходит через pythonnet, и потоки
    # .NET способны удержать tunnelvpn.exe после закрытия окна: тогда он висел
    # бы в диспетчере задач без окна. Ничего несохранённого здесь нет.
    os._exit(0)
