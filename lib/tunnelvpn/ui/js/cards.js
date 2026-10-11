'use strict';
/* Карточки туннелей: карточка на туннель в порядке tunnels.json (решение #15).
   Конфиги туннеля — внутри карточки списком «Запасные»: выбранный и запасные
   строками с выбором, своей ↻ и ⋮. Прямой выход — запасной путь службы, а не
   конфиг: карточки у него нет. */

// Выбранный конфиг: без active служба сама берёт единственный, а из
// нескольких не выберет (tunnels.active_conf) — тогда пусто.
const confOf = t => t.active || ((t.confs || []).length === 1 ? t.confs[0] : '');

// ------------------------------------------------------- проверка конфига
// ↻ проверяет один конфиг, VPN не трогает. TESTING (model.js) — «id/имя», чей
// ответ ещё не пришёл: без него кнопка молчала бы секунды. TESTED — seq проверки
// туннеля на момент ↻: у выбранного конфига живого VPN итог теста виден, пока
// туннель не проверили заново.
const TESTED = {};
const testKey = (id, name) => `${id}/${name}`;
const testOf = (t, name) => (t.tests || {})[name];
const testing = (t, name) => TESTING.has(testKey(t.id, name)) || !!(testOf(t, name) || {}).checking;

function testStart(id, name) {
  const t = (LAST.tunnels || []).find(x => x.id === id);
  if (!t || !name || testing(t, name)) return;
  TESTING.add(testKey(id, name));
  TESTED[testKey(id, name)] = t.seq;
  render(LAST);
  send('test_config', {tunnel: id, name});
}
// Python зовёт и при отказе службы: кнопка не должна застрять.
function testDone(a) {
  TESTING.delete(testKey(a.tunnel, a.name));
  render(LAST);
}

// ------------------------------------------- нажато, статус ещё не догнал
// Кнопка питания и выбор конфига показывают желаемое сразу: до опроса они
// казались бы мёртвыми. {ключ: {val, at}}; догнал статус или вышло время — снято.
const WANT = new Map();
function wanted(key, actual) {
  const w = WANT.get(key);
  if (!w) return actual;
  if (w.val === actual || Date.now() - w.at > PENDING_MS) { WANT.delete(key); return actual; }
  return w.val;
}
ON_FAILED.push(() => {
  if (!WANT.size) return;
  WANT.clear();
  render(LAST);
});

// Служба занята своей операцией или идёт команда из окна: выбор и
// переключение подождут — команда ушла бы в середину цикла.
const locked = s => !!s.loading || !!s.no_service || !!s.busy || busyNow(s);

const enabledNow = t => hasConf(t) && wanted(`on:${t.id}`, t.enabled !== false);

// Номер нажатия ⏻: мост не держит порядок вызовов, и window.py отбрасывает
// нажатие, старше уже записанного для того же туннеля.
let FLIP_SEQ = 0;
function sendEnabled(tunnel, on, replace = false) {
  WANT.set(`on:${tunnel}`, {val: on, at: Date.now()});
  send('set_enabled', {tunnel, on, replace, seq: ++FLIP_SEQ});
  render(LAST);
}

function flipEnabled(t) {
  if (locked(LAST)) return;
  sendEnabled(t.id, !wanted(`on:${t.id}`, t.enabled !== false));
}

// ⏻ плитки. VPN выключен — поднимает только этот туннель: остальные
// выключаются, и общая кнопка дальше включает этот набор. Выключен последний
// включённый — VPN останавливается, а его флаг остаётся: иначе следующее
// «Включить» нашло бы пустой набор и служба включила бы все (_wake_all).
// Пункт ⋮ «Включить / Выключить» — только флаг (flipEnabled).
const lastOn = t => enabledNow(t) && !(LAST.tunnels || []).some(x => x.id !== t.id && enabledNow(x));

function tilePower(t) {
  if (locked(LAST) || !confOf(t)) return;
  if (!LAST.up) { startOnly(t); return; }
  if (lastOn(t)) toggleVpn(); else flipEnabled(t);
}

// Флаги и start — одной командой службы (start-only): не поднялся VPN — флаги как были.
function startOnly(t) {
  const at = Date.now();
  for (const x of LAST.tunnels || []) {
    if (x.id !== t.id && enabledNow(x)) WANT.set(`on:${x.id}`, {val: false, at});
  }
  if (t.enabled === false) WANT.set(`on:${t.id}`, {val: true, at});
  send('start_only', {tunnel: t.id});
  pend('start');
}

function pickConf(t, name) {
  const key = `pick:${t.id}`;
  if (locked(LAST) || wanted(key, t.active) === name) return;
  WANT.set(key, {val: name, at: Date.now()});
  send('use_config', {tunnel: t.id, name});
  render(LAST);
}

// ------------------------------------------------------------- состояние
// Итог проверки конфига (Core._test_conf): отвечает ли его сервер — без VPN,
// у запасного или по ↻. prefix — чем карточка была бы без теста.
function testState(r, prefix) {
  const a = r.answer || '';
  if (r.result === 'up') return ['ok', `${prefix}проверен: работает${a ? ` · ${a}` : ''}`];
  if (r.result === 'rules') return ['warn', `${prefix}сервер отвечает · «${a}» не открывается`];
  if (r.result === 'error') return ['err', `${prefix}проверен: не работает${a ? ` — ${a}` : ''}`];
  if (r.result === 'none') return ['warn', `${prefix}нечем проверить`];
  return ['off', `${prefix}проверить не удалось`];
}

// Карточка: [вид, строка]. Без VPN мерить нечем — показываем, как туннель
// отработал в прошлый раз: сломанный заменяют до включения, а не после.
// Проверка конфига (↻, после добавления) свежее прошлого раза — тогда её итог.
// Вид совпадает с проблемой того же туннеля в пилюле (model.js:problemsOf).
function cardState(t, s, m) {
  if (!hasConf(t)) return ['unset', 'Нет конфига'];
  const name = confOf(t);
  if (!name) return ['unset', 'Не выбран конфиг'];
  const test = testOf(t, name);
  if (testing(t, name)) return ['busy', 'проверяю…'];
  // Выключенный основной остаётся основным, но всё остальное идёт напрямую.
  if (t.enabled === false) {
    const off = t.mode === 'all' ? 'выключен · всё остальное напрямую' : 'туннель выключен';
    return test ? testState(test, `${off} · `) : ['off', off[0].toUpperCase() + off.slice(1)];
  }
  const wait = 'ждёт включения VPN';
  if (!s.up) {
    return test ? testState(test, `${wait} · `)
      : t.last === 'error' ? ['warn', `${wait} · раньше не работал`]
      : t.last === 'up' ? ['off', `${wait} · раньше работал`] : ['off', wait];
  }
  const fresh = test && t.seq === TESTED[testKey(t.id, name)] ? testState(test, '') : null;
  const said = (text, a) => (a ? `${text} · ${a}` : text);
  if (t.mode === 'all') {
    if (m.blocked) return ['err', 'не работает · kill-switch закрыл интернет'];
    if (m.direct) return ['warn', 'не работает · интернет идёт напрямую'];
    if (m.checking(t)) return ['busy', 'проверяю…'];
    if (s.exit_state === 'leak') return ['err', 'трафик идёт мимо туннеля'];
    if (fresh) return fresh;
    if (t.check === 'up') return ['ok', said('через туннель', t.answer)];
    if (t.check === 'error') return ['err', 'не работает'];
    return ['busy', 'проверяю…'];
  }
  if (m.checking(t)) return ['busy', 'проверяю…'];
  if (fresh) return fresh;
  if (t.check === 'up') return ['ok', said('отвечает', t.answer)];
  // Сервер отвечает, а домен из «пускать» его DNS не знает: правила от другой сети.
  if (t.check === 'rules') return ['warn', `работает · «${t.answer || ''}» не открывается`];
  if (t.check === 'error') return ['warn', 'не отвечает'];
  if (t.check === 'none') return ['warn', 'нечем проверить'];
  return ['busy', 'проверяю…'];
}

const RECORDS = ['запись', 'записи', 'записей'];

// Строка плитки переносится между частями « · », а не посреди части:
// «остальное → «nl-amsterdam-1»» уходит на новую строку целиком.
const wrapParts = s => s.split(' · ').map(p => p.replace(/ /g, ' ').replace(/-/g, '‑')).join(' · ');

// Что туннель забирает и куда идёт не забранное им: в основной, а без него
// или пока он не работает — напрямую.
function roleOf(t, m) {
  if (t.enabled === false) return 'ничего не забирает · конфиги и правила сохранены';
  if (t.mode === 'all') return `всё остальное через него${t.rules ? ` · мимо VPN: ${t.rules}` : ''}`;
  const own = t.rules ? `по списку: ${plural(t.rules, RECORDS)}` : 'только сети своего конфига';
  const rest = m.blocked ? 'закрыто' : m.main && !m.direct ? `«${tname(m.main)}»` : 'напрямую';
  return `${own} · остальное → ${rest}`;
}

// ⇄: пунктир — правил нет, зелёный — применены и туннель отвечает, красный —
// домен из «пускать» через туннель не открывается, серый — туннель выключен.
function splitState(t, s) {
  const n = t.rules || 0;
  const what = t.mode === 'all' ? `мимо VPN: ${n}` : plural(n, RECORDS);
  if (t.enabled === false) return ['off', `Туннелирование: ${what} · туннель выключен`];
  if (s.up && t.check === 'rules') {
    return ['err', `Туннелирование: «${t.answer || ''}» через туннель не открывается → проверьте правила`];
  }
  if (!n) {
    return ['unset', t.mode === 'all' ? 'Туннелирование: мимо VPN ничего не идёт'
                                       : 'Туннелирование: правил нет — туннель забирает только сети своего конфига'];
  }
  if (s.up && t.check === 'up') return ['ok', `Туннелирование: ${what} · применены`];
  return ['', `Туннелирование: ${what}`];
}

// Строка запасного: выбранный, итог его ↻ или просто «запасной».
function spareState(t, name) {
  if (testing(t, name)) return ['busy', 'проверяю…'];
  const test = testOf(t, name);
  return test ? testState(test, '') : ['', 'запасной'];
}

const ROLE_TAG = {all: 'основной', list: 'по списку'};

// ---------------------------------------------------------------- меню ⋮
const setMode = (tunnel, mode) => send('set_mode', {tunnel, mode});

// Группы: просмотр, правила, роль, опасное — красным внизу (README, «Меню ⋯»).
function cardMenu(t, name, anchor) {
  const main = (LAST.tunnels || []).find(x => x.mode === 'all');
  const arg = {tunnel: t.id, name};
  const rules = {label: 'Туннелирование…', icon: 'split', act: () => send('tunnel_rules', t.id)};
  if (!hasConf(t)) {
    openMenu(anchor, [[{label: 'Добавить конфиг…', icon: 'plus', act: () => send('add_config', {tunnel: t.id})}],
                      [rules],
                      [{label: 'Удалить туннель', icon: 'trash', danger: true, act: () => delTunnel(t)}]]);
    return;
  }
  const view = name ? [{label: 'Подробности', icon: 'info', act: () => confDetails(t, name)},
                       {label: 'Показать', icon: 'eye', act: () => send('show_config', arg)},
                       {label: 'Редактировать', icon: 'edit', act: () => send('edit_config', arg)}] : [];
  const del = name ? [{label: `Удалить «${name}»`, icon: 'trash', danger: true, act: () => delConfig(t, name)}] : [];
  // Любой туннель включается и выключается сам; выключенный основной остаётся
  // основным — всё остальное тогда идёт напрямую.
  const role = [{label: t.enabled === false ? 'Включить' : 'Выключить', icon: 'power', disabled: locked(LAST),
                 act: () => flipEnabled(t)}];
  if (t.mode === 'all') {
    openMenu(anchor, [view, [rules], role, del]);
    return;
  }
  // Всё остальное — только через конфиг с 0.0.0.0/0: иначе его сервер отбросит
  // чужой трафик (служба тоже откажет). Запасным — если основной уже есть.
  if (t.full && name) {
    role.push({label: 'Всё остальное через этот туннель', icon: 'swap', act: () => setMode(t.id, 'all')});
    if (main) role.push({label: `Запасным к «${tname(main)}»`, icon: 'swap', act: () => moveToAll(t, name)});
  }
  openMenu(anchor, [view,
                    [rules, {label: 'Заменить файл…', icon: 'file',
                             act: () => send('add_config', {tunnel: t.id, place: 'replace'})}],
                    role, del]);
}

// Запасной: просмотр, вынос в отдельный туннель (у основного), удаление.
function spareMenu(t, name, anchor) {
  const arg = {tunnel: t.id, name};
  openMenu(anchor, [
    [{label: 'Подробности', icon: 'info', act: () => confDetails(t, name)},
     {label: 'Показать', icon: 'eye', act: () => send('show_config', arg)},
     {label: 'Редактировать', icon: 'edit', act: () => send('edit_config', arg)}],
    t.mode === 'all' ? [{label: 'Отдельным туннелем по списку', icon: 'split',
                         act: () => send('move_config', {...arg, to: 'new'})}] : [],
    [{label: `Удалить «${name}»`, icon: 'trash', danger: true, act: () => delConfig(t, name)}],
  ]);
}

// ------------------------------------------------------------- вопросы
const cancel = {label: 'Отмена', kind: 'ghost', act: () => closeSheet()};

// Последний конфиг туннеля уходит: правила без него бесполезны, но набраны
// руками — есть они, спрашиваем; нет — туннель уходит вместе с конфигом.
function dropAsk(t, title, act) {
  ask({title, safe: closeSheet,
       text: `У туннеля есть правила туннелирования (${plural(t.rules, RECORDS)}). Оставить их до нового ` +
             'конфига или удалить туннель вместе с ними?',
       buttons: [cancel,
                 {label: 'Оставить правила', act: () => { closeSheet(); act(false); }},
                 {label: 'Удалить вместе', kind: 'danger solid', act: () => { closeSheet(); act(true); }}]});
}

function delConfig(t, name) {
  const go = drop => send('del_config', {tunnel: t.id, name, drop_tunnel: drop});
  const last = (t.confs || []).length <= 1;
  if (last && t.rules) { dropAsk(t, `Удалить последний конфиг «${name}»?`, go); return; }
  ask({title: `Удалить «${name}»?`, safe: closeSheet,
       text: last ? 'Файл конфига удалится, туннель уйдёт вместе с ним.'
                  : 'Файл конфига удалится; остальные конфиги туннеля останутся.',
       buttons: [cancel, {label: 'Удалить', kind: 'danger solid', act: () => { closeSheet(); go(last); }}]});
}

const moveToAll = (t, name) => {
  const go = drop => send('move_config', {tunnel: t.id, name, to: 'all', drop_tunnel: drop});
  if ((t.confs || []).length > 1) go(false);
  else if (!t.rules) go(true);
  else dropAsk(t, `Последний конфиг «${tname(t)}»`, go);
};

function delTunnel(t) {
  ask({title: `Удалить туннель «${tname(t)}»?`, safe: closeSheet,
       text: t.rules ? `Правила туннелирования (${plural(t.rules, RECORDS)}) удалятся вместе с ним.`
                     : 'Конфигов и правил у него нет.',
       buttons: [cancel, {label: 'Удалить', kind: 'danger solid',
                          act: () => { closeSheet(); send('del_tunnel', t.id); }}]});
}

// Включаемый забирает те же адреса, что включённые a.clash: вместе их не
// включить, служба ничего не записала. Замена — те выключаются той же записью.
function askSwap(a) {
  WANT.delete(`on:${a.tunnel}`);
  render(LAST);
  const T = LAST.tunnels || [];
  const nameOf = (id, fallback) => { const x = T.find(u => u.id === id); return x ? tname(x) : fallback; };
  const name = nameOf(a.tunnel, a.tunnel);
  const others = a.clash.map(o => `«${nameOf(o.id, o.name)}»`).join(', ');
  ask({title: `Включить «${name}»?`, safe: closeSheet,
       text: [`«${name}» забирает те же адреса, что ${others}: вместе их не включить. ` +
              `Выключить ${others} и включить «${name}»?`,
              'Конфиги и правила выключенного останутся — вернуть его можно той же кнопкой ⏻.'],
       buttons: [cancel, {label: 'Заменить', kind: 'primary', act: () => {
         closeSheet();
         sendEnabled(a.tunnel, true, true);
       }}]});
}

// ------------------------------------------------------------------ разметка
const CARDS = (() => {
  const pane = $('pane-tunnels');
  const grid = el('div', {class: 'cards'});
  const empty = el('section', {class: 'empty', hidden: true},
    icon('logo', 'empty-logo'),
    el('h2', {text: 'Добавьте конфиг WireGuard или AmneziaWG'}),
    el('p', {text: 'Файл .conf от своего VPN. С AllowedIPs = 0.0.0.0/0 он станет основным туннелем, ' +
                   'с отдельными сетями — туннелем по списку.'}),
    el('button', {class: 'btn primary', onclick: () => send('add_config', {})}, icon('file'), 'Выбрать файл…'));
  pane.append(grid, empty);
  return {grid, empty};
})();

// Раскрытые «Запасные» по id туннеля: опрос не должен их сворачивать.
const OPEN = new Set();

function badge(b, t, name) {
  const awg = (t.awg || {})[name];
  show(b, awg != null && !!name);
  setText(b, awg ? 'AWG' : 'WG');
  b.classList.toggle('awg', !!awg);
  setTip(b, awg ? 'AmneziaWG — с маскировкой' : 'WireGuard');
}

function makeCard() {
  const r = {};
  const n = el('article', {class: 'card', tabindex: '-1'});
  const t = () => n._t;
  r.orb = orb('busy');
  r.name = el('h3', {class: 'card-name ell'});
  r.badge = el('span', {class: 'badge', hidden: true});
  r.tag = el('span', {class: 'tag'});
  r.split = ibtn('split', 'Туннелирование…', {class: 'ibtn split', onclick: () => send('tunnel_rules', t().id)});
  r.test = ibtn('refresh', 'Проверить конфиг', {class: 'ibtn test', onclick: () => testStart(t().id, confOf(t()))});
  r.menu = ibtn('more', 'Действия', {'aria-expanded': 'false', onclick: () => cardMenu(t(), confOf(t()), r.menu)});
  r.state = el('div', {class: 'card-state'});
  r.role = el('div', {class: 'card-role'});
  r.warnText = el('span', {class: 'ell'});
  r.warn = el('div', {class: 'card-warn'}, icon('warn'), r.warnText);
  r.add = el('button', {class: 'btn sm', onclick: () => send('add_config', {tunnel: t().id})},
             icon('plus'), 'Добавить конфиг…');
  r.tglText = el('span');
  r.tgl = el('button', {class: 'spares-tgl', 'aria-expanded': 'false',
                        onclick: () => { const id = t().id; OPEN.has(id) ? OPEN.delete(id) : OPEN.add(id); render(LAST); }},
             icon('chev', 'chev'), r.tglText);
  // Кнопка питания туннеля — круг без подписи; включён ли, говорят цвет и подсказка.
  r.pow = ibtn('power', 'Выключить', {class: 'ibtn power', 'aria-pressed': 'false', onclick: () => tilePower(t())});
  r.foot = el('div', {class: 'card-foot'}, r.add, r.tgl);
  r.list = el('div', {class: 'spares', role: 'radiogroup'});
  r.drawerIn = el('div', {class: 'drawer-in'}, r.list);
  r.drawer = el('div', {class: 'drawer'}, r.drawerIn);
  n.append(el('div', {class: 'card-head'},
                el('div', {class: 'card-id'}, r.orb, r.name, r.badge, r.tag),
                el('div', {class: 'card-acts'}, r.split, r.test, r.menu)),
           el('div', {class: 'card-body'}, el('div', {class: 'card-lines'}, r.state, r.role, r.warn), r.pow),
           r.foot, r.drawer);
  n._r = r;
  return n;
}

function makeSpare(card) {
  return () => {
    const r = {};
    const row = el('div', {class: 'spare'});
    const name = () => row.dataset.key;
    r.name = el('span', {class: 'ell'});
    r.badge = el('span', {class: 'badge', hidden: true});
    r.pick = el('button', {class: 'spare-pick', role: 'radio', 'aria-checked': 'false',
                           onclick: () => pickConf(card._t, name())},
                el('span', {class: 'radio'}), r.name, r.badge);
    r.stateIc = icon('ok');
    r.stateText = el('span', {class: 'ell'});
    r.state = el('span', {class: 'spare-state'}, r.stateIc, r.stateText);
    r.test = ibtn('refresh', 'Проверить конфиг', {class: 'ibtn test', onclick: () => testStart(card._t.id, name())});
    r.menu = ibtn('more', 'Действия', {'aria-expanded': 'false', onclick: () => {
      const t = card._t;
      if (name() === t.active) cardMenu(t, name(), r.menu); else spareMenu(t, name(), r.menu);
    }});
    row.append(r.pick, r.state, r.test, r.menu);
    row._r = r;
    return row;
  };
}

// --------------------------------------------------------------- отрисовка
function paintCard(n, t, s, m) {
  const r = n._r;
  // Сменился состав или роль — открытое меню этой карточки устарело.
  const sig = JSON.stringify([t.mode, t.active, t.confs, t.enabled, t.full]);
  if (n._sig !== sig && POP && n.contains(POP.anchor)) closePop();
  n._sig = sig;
  n._t = t;

  const name = confOf(t);
  const [kind, text] = cardState(t, s, m);
  const enabled = wanted(`on:${t.id}`, t.enabled !== false);
  const conf = hasConf(t);
  setKind(n, kind);
  setOrb(r.orb, kind);
  setText(r.name, tname(t));
  setTip(r.name, tname(t));
  setAttr(n, 'aria-label', tname(t));
  badge(r.badge, t, name);
  // Выключенный основной — всё ещё основной: метка роли, «выключен» — в строке состояния.
  setText(r.tag, !conf ? 'без конфига' : t.enabled === false && t.mode !== 'all' ? 'выключен'
                                        : ROLE_TAG[t.mode] || '');
  setText(r.state, wrapParts(text), true);
  const role = conf ? roleOf(t, m) : 'правила остались — добавьте конфиг в этот туннель';
  setText(r.role, wrapParts(role));

  // Работает — неоновая обводка цвета статуса; ждёт VPN — обычная; выключен —
  // пунктир и приглушённое содержимое (меню и кнопка питания — в полную силу).
  const live = !!s.up && t.enabled !== false && conf && !['busy', 'off', 'unset'].includes(kind);
  n.classList.toggle('live', live);
  n.classList.toggle('off', t.enabled === false);

  // Проблемы этого туннеля из пилюли: первая — строкой, все — в подсказке.
  const mine = m.problems.filter(p => p.id === t.id);
  const p = mine[0];
  r.warn.classList.toggle('vacant', !p);
  if (p) {
    setKind(r.warn, p.kind);
    setIcon(r.warn.firstChild, p.kind);
    setText(r.warnText, `${p.text} → ${p.fix}`);
  } else setText(r.warnText, '');
  setTip(r.warn, mine.map(x => `${x.text} → ${x.fix}`).join('\n'));

  const [sk, stip] = splitState(t, s);
  setKind(r.split, sk);
  setTip(r.split, stip);
  setAttr(r.split, 'aria-label', stip);
  show(r.split, conf);

  show(r.test, !!name);
  // Крутится, пока плитка пишет «проверяю…»: и свой ↻, и проверка службы
  // после включения VPN или по «Проверить все». Крутится — не нажимается.
  const checking = kind === 'busy';
  r.test.classList.toggle('working', checking);
  setDisabled(r.test, checking || m.noService);

  const lock = locked(s);
  show(r.add, !conf);
  // Кнопка питания у любого туннеля с конфигом: выключенный основной остаётся
  // основным, всё остальное тогда идёт напрямую. Нажато, статус не догнал —
  // ореол дышит.
  show(r.pow, conf);
  // VPN выключен — нажатие поднимает VPN с одним этим туннелем (startOnly);
  // последний включённый при живом VPN выключает сам VPN (tilePower). Не выбран
  // конфиг — включать нечего: кнопка недоступна, причина — в подсказке.
  const powTip = !name ? 'Сначала выберите конфиг'
    : !s.up ? `Включить только «${tname(t)}»`
    : lastOn(t) ? 'Выключить VPN'
    : `${enabled ? 'Выключить' : 'Включить'} «${tname(t)}»`;
  // Нажата — флаг туннеля, и при выключенном VPN: светлый значок у включённых.
  setAttr(r.pow, 'aria-pressed', String(enabled));
  setAttr(r.pow, 'aria-label', powTip);
  setTip(r.pow, powTip);
  // VPN включается или выключается, или поднят, а туннели ещё не ответили
  // (m.settling), — дышат вместе с общей кнопкой.
  const moving = busyNow(s) || s.busy === 'включаю' || s.busy === 'выключаю' || m.settling;
  r.pow.classList.toggle('working', WANT.has(`on:${t.id}`) || (enabled && moving));
  // Свет — вместе с обводкой и её цвета: пока проверяю или VPN выключен — без света.
  r.pow.classList.toggle('lit', enabled && live);
  setDisabled(r.pow, lock || !name);

  // Несколько конфигов — список: выбранный и запасные. Не выбран ни один —
  // список раскрыт сам: выбрать за человека служба не станет.
  const confs = t.confs || [];
  const many = confs.length > 1;
  show(r.tgl, many);
  // Подвал — только когда в нём есть что показать: иначе плитка выше без толку.
  show(r.foot, !conf || many);
  if (many && !name) OPEN.add(t.id);
  const open = many && OPEN.has(t.id);
  setText(r.tglText, name ? `Запасные: ${confs.length - 1}` : `Выберите конфиг: ${confs.length}`);
  setAttr(r.tgl, 'aria-expanded', String(open));
  r.drawer.classList.toggle('open', open);
  setAttr(r.drawerIn, 'inert', !open);
  setAttr(r.list, 'aria-label', `Конфиги «${tname(t)}»`);
  if (!many) { r.list.replaceChildren(); return; }

  // Выбранный — первым, за ним запасные в порядке папки.
  const chosen = wanted(`pick:${t.id}`, t.active);
  const order = confs.includes(t.active) ? [t.active, ...confs.filter(c => c !== t.active)] : confs;
  reconcile(r.list, order, c => c, makeSpare(n), (row, c) => {
    const q = row._r;
    setText(q.name, c);
    setTip(q.name, c);
    badge(q.badge, t, c);
    const on = c === chosen;
    setAttr(q.pick, 'aria-checked', String(on));
    setDisabled(q.pick, lock);
    const [k, txt] = c === t.active ? ['', 'выбран'] : spareState(t, c);
    setKind(q.state, k);
    setAttr(q.stateIc, 'hidden', !k);   // у SVG нет свойства hidden — только атрибут
    if (k) setIcon(q.stateIc, k);
    if (setText(q.stateText, txt)) setTip(q.stateText, txt);
    const busy = testing(t, c);
    q.test.classList.toggle('working', busy);
    setDisabled(q.test, busy || m.noService);
  });
}

function paintCards(s, m) {
  const T = s.tunnels;
  const has = Array.isArray(T) && !m.noService && !s.loading;
  const cards = [...CARDS.grid.children];
  const was = cards.find(c => c.contains(document.activeElement));
  show(CARDS.grid, has && T.length > 0);
  show(CARDS.empty, has && !T.length);
  if (!has) return;
  reconcile(CARDS.grid, T, t => t.id, makeCard, (n, t) => paintCard(n, t, s, m));
  // Карточка ушла вместе с кнопкой меню — меню висело бы над пустотой.
  if (POP && !POP.anchor.isConnected) closePop();
  if (was && !CARDS.grid.contains(document.activeElement)) keepFocus(was, cards.indexOf(was));
  tick('cards');
}

// Удаление конфига или туннеля уносит узел с фокусом (⋯ карточки или строки
// запасного) — фокус не падает на body: та же карточка, соседняя на её месте
// или «Выбрать файл…», если карточек не осталось.
function keepFocus(was, at) {
  const cards = CARDS.grid.children;
  const n = was.isConnected ? was : cards[Math.min(at, cards.length - 1)];
  (n || CARDS.empty.querySelector('button')).focus();
}
onRender(paintCards);
// Меню у карточки при прокрутке оторвалось бы от неё.
$('pane-tunnels').addEventListener('scroll', () => closePop());

// Пилюля («Открыть карточку») и трей: прокрутка к карточке и вспышка рамки.
function focusCard(id) {
  showTab('tunnels');
  const n = [...CARDS.grid.children].find(c => c.dataset.key === id);
  if (!n) return;
  n.scrollIntoView({block: 'nearest', behavior: REDUCED.matches ? 'auto' : 'smooth'});
  n.focus({preventScroll: true});
  n.classList.remove('flash');
  void n.offsetWidth;   // перезапуск анимации при повторном клике
  n.classList.add('flash');
}
