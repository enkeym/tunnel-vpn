'use strict';
/* Листы конфига и вопросы службы: «Показать» — файл как есть, ключи под •••;
   «Подробности» — разобранные поля без ключей; «Похоже на …» и
   «Переподключить VPN?». Плюс перетаскивание .conf на окно. */

// ------------------------------------------------------------ показать
// Значения этих полей — секрет: на экране — точки, пока не попросят.
const SECRET = new Set(['privatekey', 'presharedkey', 'headerprotectionkey']);
const MASK = '••••••••••••';

// Строка файла: секция, комментарий или «ключ = значение».
function confLine(raw, secrets) {
  if (/^\s*\[[^\]]+\]\s*$/.test(raw)) return el('span', {class: 'conf-sec', text: raw});
  const hash = raw.indexOf('#'), eq = raw.indexOf('=');
  if (eq < 0 || (hash >= 0 && hash < eq)) return el('span', {class: 'conf-cmt', text: raw});
  const key = raw.slice(0, eq), val = raw.slice(eq + 1);
  const v = el('span', {class: 'conf-val', text: val});
  if (SECRET.has(key.trim().toLowerCase())) {
    v.classList.add('conf-secret');
    v.textContent = ` ${MASK}`;
    secrets.push([v, val]);
  }
  return el('span', {}, el('span', {class: 'conf-key', text: key}), '=', v);
}

// Python (window.py:_show_conf): текст прочитала служба — каталог закрыт.
function showConf(tid, name, text) {
  const secrets = [];
  const pre = el('pre', {class: 'conf mono'},
                 text.split(/\r?\n/).flatMap(l => [confLine(l, secrets), '\n']));
  let open = false;
  const flip = {label: 'Показать ключи', icon: 'eye', kind: 'ghost', left: true, id: 'conf-keys', act: () => {
    open = !open;
    for (const [v, val] of secrets) v.textContent = open ? val : ` ${MASK}`;
    $('conf-keys').lastChild.textContent = open ? 'Скрыть ключи' : 'Показать ключи';
  }};
  // Копируется файл целиком, с ключами: он нужен владельцу как есть.
  const copy = {label: 'Копировать', icon: 'copy', act: async () => {
    const ok = await copyText(text);
    toast(ok ? 'Конфиг скопирован — в нём ключи, не пересылайте его' : 'Не вышло → выделите текст и нажмите Ctrl+C',
          ok ? 'ok' : 'err');
  }};
  openSheet({title: `${name}.conf`, body: pre, cls: 'wide',
             buttons: [...(secrets.length ? [flip] : []), copy,
                       {label: 'Закрыть', kind: 'primary', act: () => closeSheet()}]});
  tick('conf');
}

// ---------------------------------------------------------- подробности
// Что не влезло на карточку. Поля читает служба (UAC, как «Показать»);
// время и итог проверки — из статуса.
async function confDetails(t, name) {
  const r = await request('conf_details', {tunnel: t.id, name});
  if (!r.ok) { toast(humanError(r.error), 'err'); return; }
  const test = testOf(t, name);
  const mtu = r.cut ? `${r.mtu} — в файле ${r.mtu_file}, AWG с junk-параметрами не пролезает в мобильных сетях`
            : r.mtu_file ? String(r.mtu) : `${r.mtu} — по умолчанию, в файле не задан`;
  const rows = [
    ['Файл', r.path],
    ['Тип', r.awg ? 'AmneziaWG — с маскировкой' : 'WireGuard'],
    ['Endpoint', r.endpoint],
    ['Адрес', r.address.join(', ')],
    ['MTU', mtu],
    ['DNS', r.dns.join(', ') || 'не задан'],
    ['AllowedIPs', r.allowed.join(', ')],
    ['Keepalive', r.keepalive ? `${r.keepalive} с` : ''],
    ...r.mask.map(([k, v]) => [k, v]),
    ['Проверка', test && test.at
      ? `${new Date(test.at * 1000).toLocaleString('ru-RU')} · ${testState(test, '')[1]}` : 'ещё не проверялся'],
  ].filter(([, v]) => v);
  const kv = el('dl', {class: 'kv'}, rows.flatMap(([k, v]) =>
    [el('dt', {text: k}), el('dd', {class: 'mono val', text: v})]));
  const warn = r.problem
    ? el('p', {class: 'sheet-warn st-err'}, icon('err'), el('span', {text: `Не соберётся: ${r.problem}`})) : null;
  openSheet({title: `Подробности «${name}»`, body: el('div', {}, warn, kv),
             buttons: [{label: 'Закрыть', kind: 'primary', act: () => closeSheet()}]});
  tick('details');
}

// ------------------------------------------------------------- вопросы
// Служба не решилась сама (window.py:_send_config): файл похож на конфиг
// туннеля «по списку». Заменить или отдельным — решает человек.
function askPlace(a) {
  const old = a.conf || a.name;
  ask({title: `Похоже на «${old}»`, safe: closeSheet,
       text: [`«${a.file}» ведёт в те же сети, что и «${old}». Заменить его конфиг (правила ` +
              'туннелирования сохранятся) или добавить отдельным туннелем?',
              `Отдельный ляжет выключенным: одни сети — у одного включённого туннеля. Включите его — ` +
              `предложу выключить «${old}».`],
       buttons: [cancel,
                 {label: 'Отдельный туннель', act: () => { closeSheet(); send('place_config', {place: 'new'}); }},
                 {label: 'Заменить', kind: 'primary',
                  act: () => { closeSheet(); send('place_config', {place: 'replace', tunnel: a.id}); }}]});
}

// Правку на лету не применить (window.py:_apply, Tunnel.live_plan): полный
// перезапуск уронит соединения всех приложений — только по слову человека.
// Правка уже записана: «Позже» — со следующего включения.
function askFull() {
  ask({title: 'Переподключить VPN?', safe: closeSheet,
       text: ['Эту правку на лету не применить: VPN переподключится целиком, интернет пропадёт на ' +
              'несколько секунд, открытые соединения приложений оборвутся.',
              'Правка уже сохранена: без переподключения она вступит в силу при следующем включении VPN.'],
       buttons: [{label: 'Позже', kind: 'ghost', act: () => closeSheet()},
                 {label: 'Переподключить', kind: 'primary', act: () => {
                   closeSheet();
                   send('apply_full');
                   pend('restart');
                 }}]});
}

// ------------------------------------------------------- перетаскивание
// Оверлей держит страница, сам файл берёт Python (window.py:_drop): полный
// путь брошенного файла есть только у него. dragenter/dragleave приходят и от
// дочерних узлов — считаем глубину.
const DROP = el('div', {class: 'drop', hidden: true},
  el('div', {class: 'drop-in'}, icon('file'),
     el('b', {text: 'Отпустите, чтобы добавить конфиг'}),
     el('span', {text: 'Файл .conf WireGuard или AmneziaWG — место по AllowedIPs выберу сам'})));
document.body.append(DROP);
let dragDepth = 0;
const dragsFiles = e => !!e.dataTransfer && [...e.dataTransfer.types].includes('Files');
addEventListener('dragenter', e => {
  if (!dragsFiles(e)) return;
  e.preventDefault();
  dragDepth += 1;
  show(DROP, true);
});
addEventListener('dragover', e => {
  if (!dragsFiles(e)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'copy';
});
addEventListener('dragleave', e => {
  if (!dragsFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) show(DROP, false);
});
// Без preventDefault WebView2 открыл бы файл вместо окна.
addEventListener('drop', e => {
  e.preventDefault();
  dragDepth = 0;
  show(DROP, false);
});
