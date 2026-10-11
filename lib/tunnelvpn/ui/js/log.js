'use strict';
/* Вкладка «Журнал»: последние строки sing-box — общего и туннелей
   (service.Core._tail_log), по времени. Строка туннеля начинается с
   «[Имя] » — здесь это цветная пилюля.

   Python присылает все 400 строк каждый раз (renderLogs), а страница
   добавляет только новые и убирает ушедшие: узлы строк живут, пока строка в
   окне, — иначе раз в пару секунд слетали бы прокрутка и выделение. Новая
   строка не обязательно в хвосте: журналы сливаются по штампу с точностью до
   секунды, и строка общего журнала может встать перед строкой туннеля с тем
   же штампом. Поэтому сверка — по тексту строки, а не по сдвигу окна.

   Строки лежат блоками по LOG_CHUNK с content-visibility: auto — браузер не
   раскладывает то, что вне экрана. Блок за жизнь не перестраивается: новая
   строка встаёт рядом с соседкой, пустой блок удаляется. */

const LOG_CHUNK = 50;
const LOG_POLL_MS = 2000;     // как POLL_EVERY в window.py
const LOG_SEARCH_MS = 120;
const LOG_STICK_PX = 40;      // «внизу» — с допуском, как раньше

// «+0300 2026-10-10 12:00:05 INFO [123 4ms] сообщение». Часовой пояс и дата
// одинаковы во всех строках — показываем время; id соединения приглушаем:
// по нему связываются строки одного соединения.
const LOG_LINE = /^([+-]\d{4})\s+\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})\s+(\w+)\s*(\[[^\]]*\])?\s*(.*)$/;
const LOG_MARK = /^\[([^\]\n]+)\] (.*)$/;
const LOG_ERR = new Set(['ERROR', 'FATAL', 'PANIC']);
const LOG_LEVELS = [['all', 'Все', null],
                    ['warn', 'Предупреждения', 'предупреждения и ошибки'],
                    ['err', 'Ошибки', null]];
const LOG_CORE = '\u0001core';   // фильтр «Общее»: строки без метки туннеля

// items — строки в порядке журнала: {raw, mark, kind, low, node, q, parts};
// stick — держаться у хвоста; fresh — новых строк, пока пользователь выше;
// going — идёт плавная прокрутка «к новым».
const LOG = {items: [], lvl: 'all', tun: '', q: '', stick: true, going: false, top: 0, fresh: 0, names: '', tz: ''};

// --------------------------------------------------------------- разметка
const LOGV = (() => {
  const q = el('input', {id: 'log-q', type: 'search', placeholder: 'Поиск по журналу', spellcheck: 'false',
                         autocomplete: 'off', 'aria-label': 'Поиск по журналу', 'aria-controls': 'log-lines'});
  const v = {
    q,
    hits: el('span', {class: 'log-hits', 'aria-live': 'polite'}),
    lvl: el('div', {class: 'seg', role: 'group', 'aria-label': 'Уровень'}),
    tun: el('div', {class: 'seg log-tuns', role: 'group', 'aria-label': 'Туннель'}),
    tz: el('span', {class: 'log-tz'}),
    // На узкой панели — только значок; подсказка core показывает, лишь когда
    // подпись скрыта.
    copy: el('button', {class: 'btn sm log-copy', 'data-tip': 'Копировать видимое: строки, что прошли поиск и фильтры'},
             icon('copy'), el('span', {class: 'lbl', text: 'Копировать видимое'})),
    box: el('div', {class: 'log-lines scroll', id: 'log-lines', role: 'log', 'aria-label': 'Строки журнала'}),
    empty: el('div', {class: 'log-empty'}, el('p'), el('button', {class: 'btn sm', text: 'Сбросить фильтры'})),
    fresh: el('button', {class: 'log-fresh', hidden: true}, icon('down'), el('span')),
    colors: el('style', {id: 'log-colors'}),
  };
  for (const [id, label, tip] of LOG_LEVELS) {
    v.lvl.append(el('button', {'data-lvl': id, 'aria-pressed': String(id === 'all'), 'data-tip': tip, text: label}));
  }
  $('pane-log').append(
    el('div', {class: 'log-bar'},
       el('label', {class: 'log-search'}, icon('search'), q, v.hits),
       v.lvl, el('span', {class: 'grow'}), v.tz, v.copy),
    v.tun,
    el('div', {class: 'log-body'}, v.box, v.empty, v.fresh));
  document.head.append(v.colors);
  return v;
})();

// ----------------------------------------------------------------- разбор
function logItem(raw) {
  let mark = '', body = raw;
  const m = LOG_MARK.exec(raw);
  if (m) [, mark, body] = m;
  const p = LOG_LINE.exec(body);
  const lvl = p ? p[3].toUpperCase() : '';
  const kind = p ? (LOG_ERR.has(lvl) ? 'err' : lvl === 'WARN' ? 'warn' : '')
    : /\b(ERROR|FATAL|PANIC)\b/.test(body) ? 'err' : /\bWARN\b/.test(body) ? 'warn'
    : /^(===|→)/.test(body) ? 'head' : '';
  if (p && !LOG.tz) LOG.tz = p[1];
  const it = {raw, mark, kind, low: raw.toLowerCase(), q: '', parts: []};
  // Части, где ищем подсветку: [узел, текст].
  const part = (cls, text) => { const s = el('span', {class: cls, text}); it.parts.push([s, text]); return s; };
  // Метка — после времени: колонка времени ровная и у строк общего журнала.
  const tun = mark ? part('ltun', mark) : null;
  if (tun) tun.dataset.t = mark;
  it.node = el('div', {class: `ln ${kind}`.trim()},
               p ? [part('ltime', p[2]), tun, el('span', {class: 'llvl', text: lvl}),
                    p[4] ? part('lid', p[4]) : null, part('lmsg', p[5])]
                 : [tun, part('lmsg', body)]);
  return it;
}

// ------------------------------------------------------------------ фильтр
function logPasses(it) {
  if (LOG.lvl === 'err' && it.kind !== 'err') return false;
  if (LOG.lvl === 'warn' && it.kind !== 'err' && it.kind !== 'warn') return false;
  if (LOG.tun && (LOG.tun === LOG_CORE ? it.mark : it.mark !== LOG.tun)) return false;
  return !LOG.q || it.low.includes(LOG.q);
}

// Подсветка совпадений — в каждой части строки отдельно; ставится лениво,
// только видимым строкам.
function logMark(it) {
  if (it.q === LOG.q) return;
  it.q = LOG.q;
  for (const [s, text] of it.parts) {
    const low = text.toLowerCase();
    if (!LOG.q || !low.includes(LOG.q)) { if (s.childElementCount || s.textContent !== text) s.textContent = text; continue; }
    const out = [];
    let at = 0;
    for (let i = low.indexOf(LOG.q); i >= 0; i = low.indexOf(LOG.q, at)) {
      if (i > at) out.push(text.slice(at, i));
      out.push(el('mark', {text: text.slice(i, i + LOG.q.length)}));
      at = i + LOG.q.length;
    }
    if (at < text.length) out.push(text.slice(at));
    s.replaceChildren(...out);
  }
}

function logShow(it) {
  const on = logPasses(it);
  if (on) logMark(it);
  show(it.node, on);
  return on;
}

// Блок без видимых строк прячем целиком: иначе он держал бы пустое место
// размером, который content-visibility запомнил до фильтра.
function logChunks() {
  for (const c of LOGV.box.children) show(c, !!c.querySelector(':scope > .ln:not([hidden])'));
}

function logSummary() {
  const total = LOG.items.length;
  const shown = LOG.items.reduce((n, it) => n + !it.node.hidden, 0);
  const filtered = LOG.lvl !== 'all' || LOG.tun || LOG.q;
  setText(LOGV.hits, filtered && total ? `${shown} из ${total}` : '');
  show(LOGV.empty, !shown);
  setText(LOGV.empty.firstChild, total ? 'Ничего не найдено → измените поиск или сбросьте фильтры'
                                       : 'Журнала пока нет → он появится, когда VPN включится');
  show(LOGV.empty.lastChild, total && filtered);
  setDisabled(LOGV.copy, !shown);
  setText(LOGV.tz, LOG.tz ? `UTC${LOG.tz[0]}${+LOG.tz.slice(1, 3)}${LOG.tz.slice(3) === '00' ? '' : ':' + LOG.tz.slice(3)}` : '');
  setTip(LOGV.tz, LOG.tz ? 'Время в журнале — местное' : null);
}

function logFilter() {
  LOG.items.forEach(logShow);
  logChunks();
  logSummary();
  LOG.fresh = 0;
  logFresh();
  if (LOG.stick) logToEnd();
}

// --------------------------------------------------------------- прокрутка
function logNearEnd() {
  const b = LOGV.box;
  return b.scrollHeight - b.scrollTop - b.clientHeight < LOG_STICK_PX;
}
// Блоки вне экрана content-visibility меряет по запомненному размеру — после
// отрисовки хвоста высота уточняется, поэтому второй раз — на следующем кадре.
function logToEnd() {
  const b = LOGV.box;
  b.scrollTop = b.scrollHeight;
  requestAnimationFrame(() => { if (LOG.stick) b.scrollTop = b.scrollHeight; });
}
function logFresh() {
  show(LOGV.fresh, LOG.fresh > 0 && !LOG.stick);
  setText(LOGV.fresh.lastChild, `к новым (${LOG.fresh})`);
}
// Скрытая вкладка (display: none) прокрутку не держит — запоминаем сами.
// Плавная прокрутка «к новым» идёт к хвосту, который content-visibility по
// ходу удлиняет: её промежуточные scroll не значат «пользователь ушёл вверх» —
// до scrollend держимся хвоста и там доводим до конца. Колесо, мышь или
// клавиша прерывают её — дальше решает положение.
LOGV.box.addEventListener('scroll', () => {
  if (!LOGV.box.clientHeight) return;
  if (!LOG.going) LOG.stick = logNearEnd();
  LOG.top = LOGV.box.scrollTop;
  if (LOG.stick && LOG.fresh) { LOG.fresh = 0; logFresh(); }
}, {passive: true});
LOGV.box.addEventListener('scrollend', () => {
  if (!LOG.going) return;
  LOG.going = false;
  logToEnd();
});
for (const type of ['wheel', 'pointerdown', 'keydown']) {
  LOGV.box.addEventListener(type, () => { LOG.going = false; }, {passive: true});
}
LOGV.fresh.addEventListener('click', () => {
  LOG.stick = true;
  LOG.going = true;
  LOG.fresh = 0;
  logFresh();
  LOGV.box.scrollTo({top: LOGV.box.scrollHeight, behavior: REDUCED.matches ? 'auto' : 'smooth'});
});

// ------------------------------------------------------------------ строки
function logNewChunk() {
  const c = el('div', {class: 'log-chunk'});
  LOGV.box.append(c);
  return c;
}

// Новая строка — сразу за предыдущей в журнале, в её блок; за последней
// строкой полного блока — в начало следующего или в новый.
function logPlace(node, prev) {
  if (!prev) {
    const first = LOGV.box.firstElementChild || logNewChunk();
    first.prepend(node);
    return;
  }
  const c = prev.parentNode;
  if (prev.nextSibling || c.childElementCount < LOG_CHUNK) { prev.after(node); return; }
  (c.nextElementSibling || logNewChunk()).prepend(node);
}

// Python (window.py:_push_logs): последние 400 строк целиком. Прежний узел
// берём, только если он идёт после уже взятых, — так порядок узлов совпадает
// с журналом и при повторах одной строки; не взятые уходят.
function renderLogs(lines) {
  lines = Array.isArray(lines) ? lines.map(String) : [];
  const old = new Map();   // текст → индексы прежних строк по возрастанию
  LOG.items.forEach((it, i) => (old.get(it.raw) || old.set(it.raw, []).get(it.raw)).push(i));
  const kept = new Set(), next = [];
  let added = 0, shownAdded = 0, last = -1;
  for (const raw of lines) {
    const q = old.get(raw);
    while (q && q.length && q[0] <= last) q.shift();
    if (q && q.length) {
      last = q.shift();
      kept.add(last);
      next.push(LOG.items[last]);
      continue;
    }
    const it = logItem(raw);
    logPlace(it.node, next.length ? next[next.length - 1].node : null);
    added += 1;
    if (logShow(it)) shownAdded += 1;
    next.push(it);
  }
  const gone = LOG.items.filter((it, i) => !kept.has(i));
  gone.forEach(it => it.node.remove());
  if (gone.length) for (const c of [...LOGV.box.children]) if (!c.firstChild) c.remove();
  LOG.items = next;
  if (added || gone.length) {
    logChunks();
    logSummary();
  }
  if (LOG.stick) logToEnd();
  else if (shownAdded) { LOG.fresh += shownAdded; logFresh(); }
  tick('logs');
}

// ----------------------------------------------------------------- фильтры
// Чипы туннелей — из статуса; цвет пилюли — по месту туннеля в списке, одним
// правилом на имя: строки не перекрашиваем.
const LOG_HUES = 5;           // --tun-1…5 в app.css
function paintLogTunnels(s) {
  const names = (s.tunnels || []).map(t => t.name);
  const sig = names.join('\u0001');
  if (sig === LOG.names) return;
  LOG.names = sig;
  LOGV.colors.textContent = names.map((n, i) =>
    `[data-t="${CSS.escape(n)}"] { --tc: var(--tun-${i % LOG_HUES + 1}); }`).join('\n');
  if (LOG.tun && LOG.tun !== LOG_CORE && !names.includes(LOG.tun)) { LOG.tun = ''; logFilter(); }
  const chips = [['', 'Все туннели'], [LOG_CORE, 'Общее'], ...names.map(n => [n, n])];
  reconcile(LOGV.tun, names.length ? chips : [], c => c[0],
    ([id, label]) => el('button', {class: id && id !== LOG_CORE ? 'ell' : null, 'data-tun': id,
                                   'data-t': id && id !== LOG_CORE ? id : null,
                                   'data-tip': id === LOG_CORE ? 'Общий sing-box: маршруты, DNS, tun' : null},
                        id && id !== LOG_CORE ? el('span', {class: 'log-dot'}) : null, label),
    (n, [id]) => setAttr(n, 'aria-pressed', String(id === LOG.tun)));
}
onRender(paintLogTunnels);

function setLogChip(box, attr, value) {
  for (const b of box.children) setAttr(b, 'aria-pressed', String(b.dataset[attr] === value));
}
LOGV.lvl.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || b.dataset.lvl === LOG.lvl) return;
  LOG.lvl = b.dataset.lvl;
  setLogChip(LOGV.lvl, 'lvl', LOG.lvl);
  logFilter();
});
LOGV.tun.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || b.dataset.tun === LOG.tun) return;
  LOG.tun = b.dataset.tun;
  setLogChip(LOGV.tun, 'tun', LOG.tun);
  logFilter();
});

const logSearch = debounce(() => {
  const q = LOGV.q.value.trim().toLowerCase();
  if (q === LOG.q) return;
  LOG.q = q;
  logFilter();
}, LOG_SEARCH_MS);
LOGV.q.addEventListener('input', () => logSearch());
// Esc в непустом поиске — очистить поиск, а не закрывать что-то ещё.
LOGV.q.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || !LOGV.q.value) return;
  e.stopPropagation();
  LOGV.q.value = '';
  logSearch.now();
});

function resetLogFilters() {
  LOGV.q.value = '';
  Object.assign(LOG, {lvl: 'all', tun: '', q: ''});
  setLogChip(LOGV.lvl, 'lvl', 'all');
  setLogChip(LOGV.tun, 'tun', '');
  logFilter();
}
LOGV.empty.lastChild.addEventListener('click', resetLogFilters);

LOGV.copy.addEventListener('click', async () => {
  const lines = LOG.items.filter(it => !it.node.hidden).map(it => it.raw);
  if (!lines.length) return;
  if (await copyText(lines.join('\n'))) toast(`Скопировано: ${plural(lines.length, ['строка', 'строки', 'строк'])}`);
  else toast('Не удалось скопировать → выделите строки мышью и нажмите Ctrl+C', 'err');
});

// Ctrl+F — в поиск журнала (и в русской раскладке).
KEYS['ctrl+f'] = KEYS['ctrl+а'] = () => {
  showTab('log');
  LOGV.q.focus();
  LOGV.q.select();
};

// --------------------------------------------------------------------- опрос
// Опрос окна (window.poll) шлёт только статус; свежий журнал просим сами,
// пока вкладка открыта.
// Без службы не просим: Api.send ответил бы failed — тост раз в 2 с.
let _logTimer = 0;
const askLogs = () => { if (!document.hidden && !LAST.no_service) send('logs'); };
function pollLogs(on) {
  clearInterval(_logTimer);
  _logTimer = on ? setInterval(askLogs, LOG_POLL_MS) : 0;
}
TAB_HOOKS.push(tab => {
  pollLogs(tab === 'log');
  if (tab !== 'log') return;
  askLogs();
  if (LOG.stick) logToEnd(); else LOGV.box.scrollTop = LOG.top;
});
logSummary();
