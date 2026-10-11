'use strict';
/* Лист «Туннелирование». У туннеля «по списку» — «Пускать через туннель» и
   «Не пускать», у основного одно поле — его exclude: «пускать» ему не нужно,
   туда и так идёт всё, что не забрали другие.

   Разбор и дубли с другими туннелями — в Python (window.py:_validate_rules →
   routelist.check и check-rules): парсер в JS не дублируем. Сохраняет служба
   (set-tunnel), ответ — rulesSaved, rulesFailed или rulesMoved. */

const RULE_FIELDS = {
  include: ['Пускать через туннель',
            'домены, IP и подсети: a.ru, *.b.ru, 1.2.3.4, 10.0.0.0/8 — через пробел, запятую, «;» или с новой строки'],
  exclude: ['Не пускать',
            'не пойдёт в этот туннель, даже если попадает в «пускать» или в AllowedIPs его конфига'],
};
const ALL_FIELD = ['Мимо VPN (напрямую)', 'адреса и домены, которые идут напрямую, без туннеля'];
const ruleKeys = r => r.mode === 'all' ? ['exclude'] : ['include', 'exclude'];
const VALIDATE_MS = 250;

// Открытый лист: t — туннель (id, name, mode); saved — текст полей, как он
// записан; f — узлы поля по ключу; seq — номер последней проверки.
let RULES = null;

// Python (window.py:_tunnel_rules): списки — только у администратора.
function showRules(r) {
  const keys = ruleKeys(r), saved = {};
  const f = {};
  for (const k of keys) {
    saved[k] = (r[k] || []).join('\n');
    const [label, hint] = r.mode === 'all' ? ALL_FIELD : RULE_FIELDS[k];
    const area = el('textarea', {class: 'rules-area mono', id: `rules-${k}`, spellcheck: 'false',
                                 autocapitalize: 'off', 'aria-describedby': `rules-${k}-sum`});
    area.value = saved[k];
    area.addEventListener('input', () => { syncSave(); checkRules(); });
    area.addEventListener('blur', () => checkRules.now());
    f[k] = {area,
            sum: el('span', {class: 'rules-sum', id: `rules-${k}-sum`}),
            fate: el('p', {class: 'sheet-warn st-warn rules-fate', hidden: true}, icon('warn'), el('span')),
            probs: el('ul', {class: 'rules-probs', 'aria-live': 'polite'})};
    // div, а не label вокруг: клик по причине под полем ушёл бы в textarea.
    f[k].box = el('div', {class: 'rules-field'},
                  el('label', {class: 'rules-label', for: `rules-${k}`, text: label}), area,
                  el('span', {class: 'rules-hint', text: hint}), f[k].sum, f[k].fate, f[k].probs);
  }
  RULES = {t: {id: r.id, name: r.name, mode: r.mode}, keys, saved, f, seq: 0, saving: false,
           body: el('div', {class: 'rules'}, keys.map(k => f[k].box))};
  openRules();
  checkRules.now();
  tick('rules');
}

// Лист заново из тех же узлов: вопрос («Очистить все», «Отменить изменения?»)
// заменяет лист, набранное в полях остаётся.
function openRules() {
  const {t} = RULES;
  openSheet({title: `Туннелирование · ${t.name}`, body: RULES.body, cls: 'wide', onEsc: leaveRules,
             buttons: [
               {label: 'Импорт…', icon: 'file', kind: 'ghost', left: true, act: () => send('load_rules')},
               {label: 'Экспорт…', kind: 'ghost', left: true, act: () => send('export_rules',
                 {name: t.name, ...Object.fromEntries(RULES.keys.map(k => [k, RULES.saved[k].split('\n')]))})},
               {label: 'Очистить все', kind: 'ghost danger', left: true, id: 'rules-clear', act: askClear},
               {label: 'Отмена', kind: 'ghost', act: leaveRules},
               {label: 'Сохранить', kind: 'primary', id: 'rules-save', act: saveRules}]});
  syncSave();
}

const rulesValues = () => Object.fromEntries(RULES.keys.map(k => [k, RULES.f[k].area.value]));
const rulesDirty = () => RULES.keys.some(k => RULES.f[k].area.value !== RULES.saved[k]);

// Пока служба сохраняет, поля только для чтения: rulesSaved перепишет их
// ответом, и набранное за это время пропало бы молча.
function syncSave() {
  for (const k of RULES.keys) RULES.f[k].area.readOnly = RULES.saving;
  const b = $('rules-save'), c = $('rules-clear');
  if (b) {
    setDisabled(b, !rulesDirty() || RULES.saving);
    b.classList.toggle('working', RULES.saving);
  }
  if (c) setDisabled(c, RULES.keys.every(k => !RULES.f[k].area.value.trim()));
}

// ------------------------------------------------------------- проверка
// С паузой ввода и сразу на потере фокуса; устаревший ответ отбрасывается.
const checkRules = debounce(async () => {
  if (!RULES) return;
  const mine = RULES, seq = ++mine.seq;
  const r = await request('validate_rules', {tunnel: mine.t.id, ...rulesValues()});
  if (mine !== RULES || seq !== mine.seq || !r.ok) return;
  for (const k of mine.keys) paintField(k, r.fields[k], r.max);
  paintFate(r.fate);
}, VALIDATE_MS);

function paintField(k, v, max) {
  const {sum} = RULES.f[k];
  if (!v) return;
  setText(sum, v.count
    ? `домены: ${v.domains} · IP: ${v.ips} · подсети: ${v.nets} · всего ${v.count} / ${max}`
    : `пусто · до ${max} записей`);
  sum.classList.toggle('over', v.count > max);
  paintProblems(k, v.problems);
}

// Причины под полем: строка и что не так → как исправить; клик выделяет запись.
function paintProblems(k, probs) {
  const {area, probs: box} = RULES.f[k];
  area.classList.toggle('bad', probs.length > 0);
  setAttr(area, 'aria-invalid', probs.length ? 'true' : null);
  box.replaceChildren(...probs.map(p => el('li', {},
    el('button', {class: 'rules-prob', type: 'button', disabled: !p.line,
                  onclick: e => { e.preventDefault(); pickToken(area, p.line, p.token); }},
       icon('err'), p.line ? el('span', {class: 'rules-line', text: `строка ${p.line}`}) : null,
       el('span', {class: 'val', text: p.text})))));
}

// Выделить токен в строке line (с 1) и прокрутить к нему поле.
function pickToken(area, line, token) {
  const lines = area.value.split('\n');
  if (!line || line > lines.length) return;
  const at = lines.slice(0, line - 1).reduce((n, l) => n + l.length + 1, 0);
  const i = Math.max(0, lines[line - 1].indexOf(token));
  area.focus({preventScroll: true});
  area.setSelectionRange(at + i, at + i + (token ? token.length : 0));
  const lh = parseFloat(getComputedStyle(area).lineHeight) || 20;
  area.scrollTop = Math.max(0, (line - 1) * lh - area.clientHeight / 3);
}

// Куда уйдёт полный конфиг «по списку» без записей «пускать» (README:
// «Всё остальное через этот туннель»). Окно с правами знает точно (fate из
// check-rules); без прав — по статусу: full и есть ли основной.
function fateOf(fate) {
  if (fate && fate.to) return fate;
  const all = LAST.tunnels || [], t = all.find(x => x.id === RULES.t.id);
  if (!t || t.mode !== 'list' || !t.full) return null;
  const main = all.find(x => x.mode === 'all');
  return main ? {to: 'spare', main: main.name} : {to: 'main'};
}
const fateText = f => f.to === 'main'
  ? 'Без записей «пускать» туннель станет основным: через него пойдёт всё остальное'
  : `Без записей «пускать» конфиг уйдёт запасным к «${f.main}», а сам туннель удалится`;

function paintFate(fate) {
  const box = RULES.f.include && RULES.f.include.fate;
  if (!box) return;
  const f = RULES.f.include.area.value.trim() ? null : fateOf(fate);
  show(box, !!f);
  if (f) setText(box.lastChild, fateText(f));
}

// ------------------------------------------------------- вопросы листа
function askClear() {
  const {t, keys} = RULES;
  const names = keys.map(k => `«${(t.mode === 'all' ? ALL_FIELD : RULE_FIELDS[k])[0]}»`).join(' и ');
  const f = keys.includes('include') ? fateOf(null) : null;
  ask({title: 'Очистить все записи?', safe: openRules,
       text: [`Поля ${names} туннеля «${t.name}» станут пустыми. В файл это попадёт только после «Сохранить».`,
              ...(f ? [`${fateText(f)}.`] : [])],
       buttons: [{label: 'Вернуться', kind: 'ghost', act: openRules},
                 {label: 'Очистить', kind: 'danger solid', act: () => {
                   openRules();
                   for (const k of keys) RULES.f[k].area.value = '';
                   syncSave();
                   checkRules.now();
                 }}]});
}

// Esc, крестик и «Отмена»: несохранённое молча не теряем.
function leaveRules() {
  if (!RULES || !rulesDirty()) { RULES = null; closeSheet(); return; }
  ask({title: 'Отменить изменения?', safe: openRules,
       text: `Набранное в листе «Туннелирование · ${RULES.t.name}» не сохранено и пропадёт.`,
       buttons: [{label: 'Вернуться', kind: 'ghost', act: openRules},
                 {label: 'Отменить изменения', kind: 'danger solid', act: () => { RULES = null; closeSheet(); }}]});
}

// ------------------------------------------------------------ сохранение
function saveRules() {
  if (!RULES || !rulesDirty() || RULES.saving) return;
  RULES.saving = true;
  syncSave();
  checkRules.cancel();
  send('save_rules', {tunnel: RULES.t.id, ...rulesValues()});
}
ON_FAILED.push(function rulesUnlock() {
  if (RULES && RULES.saving) { RULES.saving = false; syncSave(); }
});

// Ответ set-tunnel: записи — в том виде, как их понял routelist, непонятое
// остаётся в поле, причина — под ним.
function rulesSaved(r) {
  if (!RULES) return;
  RULES.saving = false;
  // Без «пускать» и без основного полный конфиг стал основным: у листа теперь
  // одно поле — «Мимо VPN».
  if (r.mode && r.mode !== RULES.t.mode) {
    showRules({...RULES.t, mode: r.mode, include: r.include || [], exclude: r.exclude || []});
    toast('Сохранено · туннель стал основным: через него идёт всё остальное');
    return;
  }
  let n = 0, bad = 0;
  for (const k of RULES.keys) {
    const rej = (r.rejected || {})[k] || [];
    n += (r[k] || []).length;
    bad += rej.length;
    RULES.saved[k] = [...(r[k] || []), ...rej].join('\n');
    RULES.f[k].area.value = RULES.saved[k];
  }
  syncSave();
  checkRules.now();
  toast(bad ? `Сохранено: ${plural(n, RECORDS)} · не распознано: ${bad} → подробности под полем`
            : `Сохранено: ${plural(n, RECORDS)}`, bad ? 'warn' : 'ok');
}

// Без «пускать» при живом основном конфиг ушёл к нему запасным, а туннель
// листа удалён — лист закрывается, карточки обновит refresh.
function rulesMoved(m) {
  RULES = null;
  closeSheet();
  toast(`«${m.name}» — теперь запасной к «${m.main}»`);
}

// Отказ set-tunnel: лист остаётся открытым с набранным, причины — под своими
// полями; без поля (отменённый UAC, предел записей) — под первым.
function rulesFailed(r) {
  if (!RULES) return;
  RULES.saving = false;
  syncSave();
  const by = Object.fromEntries(RULES.keys.map(k => [k, []]));
  for (const p of r.problems || []) (by[p.field] || by[RULES.keys[0]]).push(p);
  if (!(r.problems || []).length) by[RULES.keys[0]].push({line: null, token: '', text: humanError(r.error)});
  for (const k of RULES.keys) paintProblems(k, by[k]);
}

// Импорт: JSON экспорта — по полям, просто текст — в первое поле.
function fillRules(v) {
  if (!RULES) return;
  const {keys, f} = RULES;
  if ('text' in v) f[keys[0]].area.value = v.text;
  else for (const k of keys) if (k in v) f[k].area.value = v[k];
  syncSave();
  checkRules.now();
  toast('Импортировано — проверьте и нажмите «Сохранить»');
}
