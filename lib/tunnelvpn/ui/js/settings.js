'use strict';
/* Вкладка «Настройки»: группы «Сейчас подключено», «Запуск», «Отладка»,
   «Справка», «О программе».

   Что знает служба, приходит опросом (render) и полным обновлением
   (renderVersion, renderHowto). Остальное — служба Windows, задача трея,
   портативная ли версия — окно узнаёт само (settings_info), только когда
   вкладку открыли: без неё периодических запросов нет — кроме итога
   обновления, пока работает установщик (watchUpdate). Разметка строится
   один раз, опрос меняет текст и атрибуты. */

const SET_AWG_MTU = 1280;   // buildconfig.AWG_MTU_MAX: потолок MTU AmneziaWG с мусором
const SET_START = {auto: 'автоматически', delayed: 'автоматически, отложенно',
                   manual: 'вручную', disabled: 'отключена'};

// info — ответ settings_info (null — ещё не спрошен); past — туннели
// последнего включения: показываем их, помеченными, пока VPN выключен;
// *Want — значение, ушедшее в Python, пока не пришёл итог. upd — ответ
// check_update и ход обновления поверх него: state download (got, total),
// verify, ready, bad — файл не прошёл проверку; «Обновляю…» — UPD (model.js).
const SET = {info: null, version: {app: '', singbox: ''}, howto: [], past: [], upd: {state: ''},
             autoWant: null, logWant: null, trayWant: null};

// ---------------------------------------------------------------- разметка
function setSwitch(label, hint, flip) {
  const sw = el('button', {class: 'switch', role: 'switch', 'aria-checked': 'false', 'aria-label': label,
                           onclick: () => flip(sw.getAttribute('aria-checked') !== 'true')});
  const row = el('div', {class: 'set-row'},
                 el('div', {class: 'set-text'}, el('div', {class: 'set-label', text: label}),
                    el('p', {class: 'set-hint', text: hint})),
                 sw);
  return {row, sw};
}

// Свёрнутый блок: кнопка с шевроном и ящик (тот же .drawer, что у запасных).
// open(on) — раскрыть или свернуть не кликом («Что нового» из уведомления).
function setFold(label, ...body) {
  const text = el('span', {text: label});
  const drawer = el('div', {class: 'drawer'}, el('div', {class: 'drawer-in'}, ...body));
  const open = on => {
    setAttr(btn, 'aria-expanded', String(on));
    drawer.classList.toggle('open', on);
  };
  const btn = el('button', {class: 'fold-tgl', 'aria-expanded': 'false',
                            onclick: () => open(btn.getAttribute('aria-expanded') !== 'true')},
                 icon('chev', 'chev'), text);
  return {node: el('div', {class: 'fold'}, btn, drawer), text, open};
}

// Строка «подпись — значение» в <dl>; возвращает dd.
function setKv(dl, label) {
  const dd = el('dd', {class: 'val'});
  dl.append(el('dt', {text: label}), dd);
  return dd;
}

function setGroup(title, lead, ...kids) {
  return el('section', {class: 'set-group', 'aria-label': title},
            el('h2', {text: title}), lead ? el('p', {class: 'set-lead', text: lead}) : null, ...kids);
}

const SETV = (() => {
  const v = {};
  // 1. Сейчас подключено
  v.nowState = el('p', {class: 'set-state'});
  v.eps = el('div', {class: 'set-eps'});
  v.common = el('dl', {class: 'kv'});
  v.exit = setKv(v.common, 'Выход');
  v.v6 = setKv(v.common, 'IPv6');
  v.spare = setKv(v.common, 'Запасной выход');
  v.now = setGroup('Сейчас подключено', null, v.nowState, v.eps, v.common);

  // 2. Запуск
  v.auto = setSwitch('Включать VPN при запуске Windows',
                     'Служба поднимает туннели сама, как только стартует, — ещё до входа в систему.',
                     on => flipAutostart(on));
  v.tray = setSwitch('Значок в трее при входе в систему',
                     'Задача планировщика «TunnelVPN Tray». Нужны права администратора — Windows спросит разрешение.',
                     on => flipTray(on));
  v.svcOrb = orb('busy');
  v.svcText = el('span', {class: 'val'});
  v.svc = el('div', {class: 'set-line'}, v.svcOrb, el('span', {class: 'set-dim', text: 'Служба Windows:'}), v.svcText);
  v.launchWait = el('p', {class: 'set-hint', text: 'Читаю состояние службы и автозапуска…'});
  v.portable = el('p', {class: 'set-hint', text: 'Портативная версия: VPN держит процесс в трее, службы и автозапуска у неё нет.'});
  v.launch = setGroup('Запуск', null, v.launchWait, v.portable, v.auto.row, v.tray.row, v.svc);

  // 3. Отладка
  v.log = setSwitch('Подробный журнал sing-box',
                    'Уровень debug, пока ищешь причину сбоя: журнал растёт в разы. ' +
                    'Туннели переходят на него сразу, маршрутизация и DNS — со следующего включения.',
                    on => flipLog(on));
  v.openLogs = el('button', {class: 'btn', onclick: () => openLogs()}, icon('folder'), 'Открыть папку журналов');
  v.diag = el('button', {class: 'btn', onclick: () => copyDiag()}, icon('copy'), 'Скопировать диагностику');
  v.debug = setGroup('Отладка', null, v.log.row,
                     el('div', {class: 'set-acts'}, v.openLogs, v.diag),
                     el('p', {class: 'set-hint', text: 'Диагностика — вывод «tunnelvpn status» и версии для багрепорта. ' +
                                                       'Адрес выхода и адрес утечки IPv6 в ней скрыты.'}));

  // 4. Справка
  const ul = items => el('ul', {}, items.map(t => el('li', {text: t})));
  const ol = items => el('ol', {}, items.map(t => el('li', {text: t})));
  const howto = setFold('Как подключиться',
    el('div', {class: 'set-help'},
       el('p', {text: 'Нужен файл .conf WireGuard или AmneziaWG от своего VPN. Нажми «Добавить конфиг» на вкладке ' +
                      '«Туннели» или перетащи файл в окно. Место конфиг выбирает сам, по своему AllowedIPs:'}),
       ul(['есть 0.0.0.0/0 — основной туннель: через него идёт весь остальной трафик. ' +
           'Основной уже есть — новый конфиг ложится к нему запасным;',
           'тот же DNS, что у туннеля по списку, — заменяет его конфиг, туннелирование остаётся;',
           'отдельные сети — новый туннель по списку: через него идут только его сети и записи туннелирования.']),
       el('p', {text: 'Потом — «Включить». Домены и адреса туннелю по списку добавляются в «Туннелировании» ' +
                      '(⇄ на карточке).'})));
  const works = setFold('Как это работает',
    el('div', {class: 'set-help'},
       el('p', {text: 'Каждое соединение проходит правила по порядку, до первого совпадения:'}),
       ol(['туннели по списку — сети из AllowedIPs конфига и записи «Пускать через туннель», кроме «Не пускать». ' +
           'Одна запись — у одного туннеля;',
           'локальные адреса (10/8, 172.16/12, 192.168/16) — напрямую: принтер и роутер не уходят в туннель;',
           '«Мимо VPN» основного — напрямую;',
           'всё остальное — через основной; без него или пока он не работает — напрямую (запасной выход).']),
       el('p', {text: 'DNS: домены туннеля по списку резолвит DNS из его конфига через сам туннель, ' +
                      'остальные имена — публичный DNS тем же выходом, что и трафик.'}),
       el('p', {text: 'IPv6 на время работы закрывает брандмауэр, если основной сервер не выдаёт IPv6-адрес: ' +
                      'иначе он шёл бы мимо туннеля.'})));
  v.help = setGroup('Справка', null, howto.node, works.node);

  // 5. О программе
  v.about = el('dl', {class: 'kv'});
  v.app = setKv(v.about, 'TunnelVPN');
  v.sb = setKv(v.about, 'sing-box');
  v.updBtn = el('button', {class: 'btn', onclick: () => checkUpdate()},
                icon('busy', 'spin'), icon('refresh'), 'Проверить обновления');
  v.updOrb = orb('ok');
  v.updText = el('span');
  v.updState = el('span', {class: 'set-line', 'aria-live': 'polite'}, v.updOrb, v.updText);
  v.get = el('button', {class: 'btn primary', hidden: true, onclick: () => downloadUpdate()},
             icon('down'), 'Скачать и установить');
  v.cancel = el('button', {class: 'btn', hidden: true, onclick: () => cancelUpdate()}, 'Отмена');
  v.install = el('button', {class: 'btn primary', hidden: true, onclick: () => installUpdate()},
                 icon('busy', 'spin'), icon('down'), 'Установить сейчас');
  v.release = el('button', {class: 'btn primary', hidden: true, onclick: () => openUrl(SET.upd.url)},
                 icon('ext'), 'Открыть страницу релиза');
  v.bar = el('progress', {class: 'upd-bar', hidden: true, 'aria-label': 'Скачивание обновления'});
  v.downtime = el('p', {class: 'set-hint', hidden: true});
  v.notesText = el('div', {class: 'set-help set-notes val'});
  v.notes = setFold('Что нового', v.notesText);
  v.repo = el('button', {class: 'link', onclick: () => openUrl((SET.info && SET.info.repo) || '')}, 'GitHub');
  v.aboutGroup = setGroup('О программе', null, v.about,
                          el('div', {class: 'set-acts'}, v.updBtn, v.updState, v.get, v.cancel, v.install, v.release),
                          v.bar, v.downtime, v.notes.node,
                          el('div', {}, v.repo));

  $('pane-settings').append(el('div', {class: 'set'}, v.now, v.launch, v.debug, v.help, v.aboutGroup));
  return v;
})();

// ------------------------------------------------------- из Python и моста
function renderVersion(v) {
  SET.version = {app: (v && v.app) || '', singbox: (v && v.singbox) || ''};
  paintAbout();
}

// Сводка туннелей последнего включения (window.Api._howto). Пустая приходит
// и у выключенного VPN — прошлую тогда не затираем.
function renderHowto(h) {
  SET.howto = (h && h.endpoints) || [];
  if (SET.howto.length) SET.past = SET.howto;
  paintSettings(LAST, model(LAST));
}

function autostartDone() {
  SET.autoWant = null;
  paintSettings(LAST, model(LAST));
}

function logLevelDone() {
  SET.logWant = null;
  paintSettings(LAST, model(LAST));
}

async function loadSettingsInfo() {
  const r = await request('settings_info');
  if (!r.ok) { toast(humanError(r.error), 'err'); return; }
  SET.info = r;
  paintSettings(LAST, model(LAST));
}
TAB_HOOKS.push(tab => { if (tab === 'settings') loadSettingsInfo(); });

// ---------------------------------------------------------------- действия
function flipAutostart(on) {
  SET.autoWant = on;
  send('set_autostart', on);
  paintSettings(LAST, model(LAST));
}

function flipLog(on) {
  SET.logWant = on;
  send('log_level', on);
  paintSettings(LAST, model(LAST));
}

// Итог — по задаче, перечитанной после команды: отказ в UAC иначе не виден.
async function flipTray(on) {
  SET.trayWant = on;
  paintSettings(LAST, model(LAST));
  const r = await request('set_tray_autostart', on);
  SET.trayWant = null;
  if (SET.info && typeof r.tray_autostart === 'boolean') SET.info.tray_autostart = r.tray_autostart;
  if (!r.ok) toast(humanError(r.error), 'err');
  else toast(on ? 'Значок появится в трее при следующем входе в систему' : 'При входе в систему значок больше не появится');
  paintSettings(LAST, model(LAST));
}

async function openLogs() {
  const r = await request('open_logs');
  if (!r.ok) toast(humanError(r.error), 'err');
}

async function copyDiag() {
  setDisabled(SETV.diag, true);
  const r = await request('diagnostics');
  setDisabled(SETV.diag, false);
  if (!r.ok) { toast(humanError(r.error), 'err'); return; }
  if (await copyText(r.text)) toast('Диагностика скопирована → вставь её в issue на GitHub');
  else toast('Буфер обмена занят → повтори через пару секунд', 'err');
}

async function openUrl(url) {
  const r = await request('open_url', url);
  if (!r.ok) toast(humanError(r.error), 'err');
}

// Без фона и таймера: только по кнопке, ответ — {state, latest, url, notes,
// install, downtime}; install — окно может скачать и поставить само.
async function checkUpdate() {
  SET.upd = {state: 'busy'};
  paintAbout();
  const r = await request('check_update');
  SET.upd = r.ok ? r : {state: 'error'};
  paintAbout();
}

// ------------------------------------------------- обновление из окна
const UPD_POLL_MS = 2000;   // как часто спрашивать итог, пока работает установщик
const SET_MB = 2 ** 20;

function setUpd(patch) {
  SET.upd = {...SET.upd, ...patch};
  paintAbout();
}
// Обновление уже идёт: проверка и «Что нового» его не перебивают.
const updFlow = () => UPD.state === 'installing' || ['download', 'verify', 'ready'].includes(SET.upd.state);

// Ход и итог скачивания приходят из потока window.Api._download:
// updateProgress, updateStage, updateReady, updateFailed.
async function downloadUpdate() {
  setUpd({state: 'download', got: 0, total: 0, cancelling: false});
  const r = await request('update_download');
  if (!r.ok) {
    setUpd({state: 'update'});
    toast(humanError(r.error), 'err');
  }
}

function cancelUpdate() {
  setUpd({cancelling: true});
  send('update_cancel');
}

function updateProgress({got, total}) {
  if (SET.upd.state === 'download') setUpd({got, total});
}

function updateStage(name) {
  if (name === 'verify') setUpd({state: 'verify'});
}

function updateReady() {
  setUpd({state: 'ready'});
}

// verify — файл удалён, ставить нечего: только страница релиза. Отмена и
// сбой скачивания возвращают к «Скачать и установить».
function updateFailed({stage, text}) {
  setUpd({state: stage === 'verify' ? 'bad' : 'update'});
  if (stage !== 'verify' && stage !== 'cancel') toast(humanError(text), 'err');
}

// Установщик закроет и окно, и трей; до того — «Обновляю TunnelVPN…» здесь и
// в топ-баре. Не запустился — файла больше нет (update.launch), снова качать.
async function installUpdate() {
  const V = SETV;
  V.install.classList.add('working');
  setDisabled(V.install, true);
  const r = await request('update_install');
  V.install.classList.remove('working');
  setDisabled(V.install, false);
  if (!r.ok) {
    setUpd({state: 'update'});
    toast(humanError(r.error), 'err');
    return;
  }
  UPD = {state: 'installing', version: SET.upd.latest};
  render(LAST);
  watchUpdate();
}

// Итог обновления из окна (update.pending): при запуске страницы и после
// «Установить сейчас». installing — установщик ещё работает, спрашиваем
// снова; остальное показываем один раз и забываем (update_seen).
async function watchUpdate() {
  const r = await request('update_outcome');
  const p = (r.ok && r.pending) || null;
  if (p && p.state === 'installing') {
    if (UPD.state !== 'installing') {
      UPD = {state: 'installing', version: p.version};
      render(LAST);
    }
    setTimeout(watchUpdate, UPD_POLL_MS);
    return;
  }
  const was = UPD.state === 'installing';
  UPD = p && p.state === 'tunnel' ? {state: 'tunnel', version: p.version} : {state: '', version: ''};
  // Установщик этого окна кончился, а окно живо: setup он уже удалил.
  if (was && SET.upd.state === 'ready') SET.upd = {...SET.upd, state: 'update'};
  if (was || p) render(LAST);
  if (!p) return;
  if (p.state === 'updated') toast(`Обновлено до ${p.version}`, 'ok', null, ['Что нового', () => showAbout(true)]);
  else if (p.state === 'error') toast(p.text, 'err');
  send('update_seen');
}

// Вкладка «Настройки» на «О программе»: из уведомления (notes — раскрыть
// «Что нового») и из трея. Текст релиза — свежей проверкой: она же скажет,
// не вышло ли что-то новее.
async function showAbout(notes) {
  const reveal = () => SETV.aboutGroup.scrollIntoView({block: 'nearest', behavior: REDUCED.matches ? 'auto' : 'smooth'});
  showTab('settings');
  reveal();
  if (updFlow()) return;
  await checkUpdate();
  if (notes && SET.upd.notes) SETV.notes.open(true);
  reveal();
}

// ---------------------------------------------------------------- отрисовка
const setTunnelOf = tag => (LAST.tunnels || []).find(t => `wg-${t.id}` === tag);   // buildconfig.ep_tag

function makeEp() {
  const r = {};
  r.name = el('span', {class: 'set-ep-name ell'});
  r.role = el('span', {class: 'tag'});
  r.awg = el('span', {class: 'badge awg', text: 'с маскировкой', 'data-tip': 'AmneziaWG'});
  r.kv = el('dl', {class: 'kv'});
  r.addr = setKv(r.kv, 'Адрес');
  r.peer = setKv(r.kv, 'Сервер');
  r.mtu = setKv(r.kv, 'MTU');
  r.more = el('dl', {class: 'kv'});
  r.dns = setKv(r.more, 'DNS');
  r.nets = setKv(r.more, 'Сети');
  r.domains = setKv(r.more, 'Домены');
  r.fold = setFold('Сети, домены и DNS', r.more);
  const n = el('div', {class: 'set-ep'},
               el('div', {class: 'set-ep-head'}, r.name, r.awg, el('span', {class: 'grow'}), r.role),
               r.kv, r.fold.node);
  n._r = r;
  return n;
}

function paintEp(n, ep) {
  const r = n._r;
  const t = setTunnelOf(ep.tag);
  const name = t ? tname(t) : ep.tag;
  if (setText(r.name, name)) setTip(r.name, name);
  setText(r.role, !t ? '' : t.mode === 'all' ? 'основной' : 'по списку');
  show(r.awg, !!ep.awg);
  setText(r.addr, ep.address || '—');
  setText(r.peer, ep.peer || '—');
  const cut = ep.awg && ep.mtu === SET_AWG_MTU;
  setText(r.mtu, ep.mtu ? `${ep.mtu}${cut ? ' · снижен для маскировки' : ''}` : '—');
  setTip(r.mtu, cut ? 'AmneziaWG с мусорными пакетами: MTU выше 1280 служба снижает, иначе пакеты не пролезают' : '');
  // Сети и домены служба отдаёт только окну с правами: в них рабочая сеть.
  show(r.fold.node, !t || t.mode === 'list');
  const nets = ep.nets || [], domains = ep.domains || [];
  const hidden = SET.info && SET.info.admin === false;
  setText(r.dns, ep.dns || 'нет — домены резолвит публичный DNS');
  setText(r.nets, nets.length ? nets.join('\n') : hidden ? 'видны окну с правами администратора' : '—');
  setText(r.domains, domains.length ? domains.join('\n') : hidden ? 'видны окну с правами администратора' : '—');
  setText(r.fold.text, ['Сети, домены и DNS', nets.length && plural(nets.length, ['сеть', 'сети', 'сетей']),
                        domains.length && plural(domains.length, ['домен', 'домена', 'доменов'])]
    .filter(Boolean).join(' · '));
}

function setValue(dd, text, kind = '') {
  setText(dd, text);
  setKind(dd, kind);
}

function paintNow(s, m) {
  const V = SETV;
  const live = !!s.up && !m.noService;
  const list = live ? SET.howto : SET.past;
  setText(V.nowState, m.noService ? 'Служба не отвечает — сводки нет'
    : !s.up ? (list.length ? 'VPN выключен. Ниже — последнее включение' : 'VPN выключен')
    : list.length ? '' : 'Туннели ещё поднимаются');
  show(V.nowState, !!V.nowState.textContent);
  V.now.classList.toggle('past', !live);
  reconcile(V.eps, m.noService ? [] : list, ep => ep.tag, makeEp, paintEp);

  show(V.common, live);
  if (!live) return;
  const ip = s.exit_ip || '';
  const place = [s.exit_country, s.exit_city].filter(Boolean).join(', ');
  const how = s.exit_state === 'leak' ? ['мимо туннеля, адрес провайдера → перезапусти VPN', 'err']
    : m.direct ? ['напрямую: запасной выход', 'warn']
    : !m.main ? ['напрямую: основного туннеля нет', '']
    : s.exit_state === 'tunnel' ? ['через туннель', 'ok']
    : ['не удалось проверить', ''];
  setValue(V.exit, ip ? `${ip}${place ? ` ${place}` : ''} — ${how[0]}` : `— ${how[0]}`, how[1]);
  setValue(V.v6, s.v6_leak ? `утечка: ${s.v6_leak} → перезапусти VPN`
    : s.v6_off ? 'заблокирован брандмауэром: основной сервер не выдаёт IPv6'
    : m.main ? 'через туннель' : 'напрямую', s.v6_leak ? 'err' : '');
  setValue(V.spare, m.direct ? `активен: «${tname(m.main)}» не работает, интернет идёт напрямую`
    : m.main ? 'не нужен: основной работает' : 'не используется: основного туннеля нет', m.direct ? 'warn' : '');
}

function paintSwitch(sw, want, now, off) {
  setAttr(sw, 'aria-checked', String(want ?? now));
  sw.classList.toggle('pending', want != null);
  setDisabled(sw, want != null || off);
}

function paintLaunch(s, m) {
  const V = SETV, i = SET.info;
  const installed = !!i && !i.portable;
  show(V.launchWait, !i);
  show(V.portable, !!i && i.portable);
  show(V.auto.row, installed);
  show(V.tray.row, installed && i.tray_autostart != null);
  show(V.svc, installed);
  paintSwitch(V.auto.sw, SET.autoWant, !!s.autostart, m.noService);
  if (installed) paintSwitch(V.tray.sw, SET.trayWant, !!i.tray_autostart, false);
  if (!installed) return;
  const svc = i.service;
  const [kind, text] = !svc ? ['warn', 'состояние не прочитать']
    : !svc.installed ? ['err', 'не установлена → переустанови TunnelVPN или выполни «tunnelvpn service install» от администратора']
    : [svc.running ? 'ok' : 'warn',
       `установлена · ${svc.running ? 'работает' : 'остановлена'} · запуск: ${SET_START[svc.start] || 'неизвестно'}`];
  setOrb(V.svcOrb, kind);
  setText(V.svcText, text);
}

function paintDebug(s, m) {
  paintSwitch(SETV.log.sw, SET.logWant, s.log_level === 'debug', m.noService);
}

// «4,1 / 18 МБ»: до 10 МБ — с десятыми.
function setMb(n) {
  const mb = n / SET_MB;
  return mb.toLocaleString('ru-RU', {maximumFractionDigits: mb < 10 ? 1 : 0});
}

function paintAbout() {
  const V = SETV;
  const u = UPD.state === 'installing' ? {...SET.upd, state: 'installing'} : SET.upd;
  setText(V.app, SET.version.app || LAST.version || '—');
  setText(V.sb, SET.version.singbox || LAST.singbox || '—');
  show(V.updBtn, !updFlow());
  V.updBtn.classList.toggle('working', u.state === 'busy');
  setDisabled(V.updBtn, u.state === 'busy');
  const got = !u.got ? '' : u.total ? ` ${setMb(u.got)} / ${setMb(u.total)} МБ` : ` ${setMb(u.got)} МБ`;
  const [kind, text] = {busy: ['busy', 'Проверяю…'],
                        latest: ['ok', 'Установлена последняя версия'],
                        update: ['', `Доступна ${u.latest}`],
                        error: ['err', 'Не удалось проверить — нет связи с GitHub → проверь интернет и повтори'],
                        download: ['busy', `Скачиваю…${got}`],
                        verify: ['busy', 'Проверяю файл'],
                        bad: ['err', 'Файл обновления не прошёл проверку и удалён. ' +
                                     'Скачайте установщик вручную со страницы релиза'],
                        ready: ['ok', 'Готово к установке'],
                        installing: ['busy', 'Обновляю TunnelVPN…']}[u.state] || ['', ''];
  show(V.updState, !!text);
  show(V.updOrb, !!kind);
  if (kind) setOrb(V.updOrb, kind);
  setText(V.updText, text);
  setKind(V.updText, kind === 'err' ? 'err' : '');

  show(V.get, u.state === 'update' && !!u.install);
  show(V.release, (u.state === 'update' && !u.install || u.state === 'bad') && !!u.url);
  show(V.cancel, u.state === 'download');
  setDisabled(V.cancel, !!u.cancelling);
  show(V.install, u.state === 'ready');
  // Без размера от сервера полоса без значения — неопределённая.
  show(V.bar, u.state === 'download');
  if (u.state === 'download' && u.total) {
    setAttr(V.bar, 'max', u.total);
    setAttr(V.bar, 'value', u.got || 0);
  } else setAttr(V.bar, 'value', null);
  setText(V.downtime, `VPN отключится примерно на ${plural(u.downtime || 0, ['секунду', 'секунды', 'секунд'])} ` +
                      'и включится обратно');
  show(V.downtime, u.state === 'ready' && !!LAST.up && !!u.downtime);
  // «Что нового» — у свежей версии и у установленной, если она последняя.
  show(V.notes.node, !!u.notes && u.state !== 'installing');
  setText(V.notes.text, `Что нового в ${u.latest || ''}`);
  setText(V.notesText, u.notes || '');
}

function paintSettings(s, m) {
  paintNow(s, m);
  paintLaunch(s, m);
  paintDebug(s, m);
  paintAbout();
  tick('settings');
}
onRender(paintSettings);
