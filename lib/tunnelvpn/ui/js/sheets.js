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
  const r = await request('conf_details', {tunnel: t.id, name, proto: t.proto || ''});
  if (!r.ok) { toast(humanError(r.error), 'err'); return; }
  const test = testOf(t, name);
  const check = test && test.at
    ? `${new Date(test.at * 1000).toLocaleString('ru-RU')} · ${testState(test, '')[1]}`
    : 'ещё не проверялся';
  let rows;
  if (r.proto === 'vless') {
    rows = [
      ['Профиль', cname(t, name)],
      ['Ссылка', r.path],
      ['Endpoint', r.endpoint],
      ['Безопасность', r.security],
      ['Транспорт', r.transport],
      ['Host', r.host],
      ['Путь WS', r.ws_path],
      ['SNI', r.sni],
      ['Отпечаток TLS', r.fp],
      ['Проверка', check],
    ];
  } else {
    const mtu = r.cut ? `${r.mtu} — в файле ${r.mtu_file}, AWG с junk-параметрами не пролезает в мобильных сетях`
              : r.mtu_file ? String(r.mtu) : `${r.mtu} — по умолчанию, в файле не задан`;
    rows = [
      ['Файл', r.path],
      ['Тип', r.awg ? 'AmneziaWG — с маскировкой' : 'WireGuard'],
      ['Endpoint', r.endpoint],
      ['Адрес', r.address.join(', ')],
      ['MTU', mtu],
      ['DNS', r.dns.join(', ') || 'не задан'],
      ['AllowedIPs', r.allowed.join(', ')],
      ['Keepalive', r.keepalive ? `${r.keepalive} с` : ''],
      ...r.mask.map(([k, v]) => [k, v]),
      ['Проверка', check],
    ];
  }
  rows = rows.filter(([, v]) => v);
  const kv = el('dl', {class: 'kv'}, rows.flatMap(([k, v]) =>
    [el('dt', {text: k}), el('dd', {class: 'mono val', text: v})]));
  const warn = r.problem
    ? el('p', {class: 'sheet-warn st-err'}, icon('err'), el('span', {text: `Не соберётся: ${r.problem}`})) : null;
  openSheet({title: `Подробности «${cname(t, name)}»`, body: el('div', {}, warn, kv),
             buttons: [{label: 'Закрыть', kind: 'primary', act: () => closeSheet()}]});
  tick('details');
}

function markBad(node, box, text) {
  if (!node.isConnected) { toast(text, 'err'); return; }
  node.classList.add('bad');
  setAttr(node, 'aria-invalid', 'true');
  box.hidden = false;
  setText(box.lastChild, text);
  node.focus();
}

function addLinkSheet(tunnel = '') {
  const area = el('textarea', {class: 'rules-area mono', id: 'link-text', spellcheck: 'false',
                               autocapitalize: 'off', placeholder: 'vless://…'});
  const bad = el('p', {class: 'sheet-warn st-err', hidden: true}, icon('err'), el('span'));
  const info = el('p', {class: 'rules-hint', hidden: true});
  const check = el('button', {class: 'btn sm', type: 'button', text: 'Проверить ссылку'});
  const body = el('div', {class: 'rules'},
    el('div', {class: 'rules-field'},
      el('label', {class: 'rules-label', for: 'link-text', text: 'Ссылка VLESS'}),
      area,
      el('span', {class: 'rules-hint',
                  text: 'Одна ссылка vless:// (WebSocket + TLS). UUID и параметры останутся в профиле.'}),
      check, info, bad));
  check.onclick = async () => {
    const link = area.value.trim();
    if (!link) { markBad(area, bad, 'вставьте ссылку vless://'); return; }
    check.disabled = true;
    try {
      const r = await request('preview', {link});
      if (!r.ok) { markBad(area, bad, humanError(r.error)); return; }
      bad.hidden = true;
      setText(info, `VLESS · WebSocket + TLS · ${r.name} — ${r.host}:${r.port}`);
      info.hidden = false;
    } finally { check.disabled = false; }
  };
  openSheet({title: 'Вставить ссылку', body, buttons: [cancel,
    {label: 'Добавить', kind: 'primary', act: async () => {
      const link = area.value.trim();
      if (!/^vless:\/\//i.test(link)) {
        markBad(area, bad, 'нужна ссылка vless:// — WireGuard и AmneziaWG добавляются файлом .conf');
        return;
      }
      const r = await request('add_link', {link, tunnel});
      if (!r.ok) { markBad(area, bad, humanError(r.error)); return; }
      if (area.isConnected) closeSheet();
    }}]});
  tick('add-link');
}

function addSubscriptionSheet() {
  const url = el('input', {class: 'subs-input mono', id: 'sub-url', type: 'text',
                           spellcheck: 'false', autocapitalize: 'off', placeholder: 'https://…'});
  const name = el('input', {class: 'subs-input', id: 'sub-name', type: 'text',
                            placeholder: 'по адресу источника'});
  const bad = el('p', {class: 'sheet-warn st-err', hidden: true}, icon('err'), el('span'));
  const info = el('p', {class: 'rules-hint', hidden: true});
  const check = el('button', {class: 'btn sm', type: 'button', text: 'Проверить источник'});
  let allowPrivate = false;
  const priv = setSwitch('Разрешить частные адреса',
    'Только для источника во внутренней сети: разрешит адреса 10.0.0.0/8, 172.16.0.0/12 и 192.168.0.0/16.',
    on => { allowPrivate = on; priv.sw.setAttribute('aria-checked', String(on)); });
  const body = el('div', {class: 'rules'},
    el('div', {class: 'rules-field'},
      el('label', {class: 'rules-label', for: 'sub-url', text: 'Адрес подписки'}), url,
      el('span', {class: 'rules-hint',
                  text: 'HTTPS-адрес со ссылками vless:// — uri-list, base64 или sing-box JSON.'}),
      check, info, bad),
    el('div', {class: 'rules-field'},
      el('label', {class: 'rules-label', for: 'sub-name', text: 'Название'}), name,
      el('span', {class: 'rules-hint', text: 'Необязательно: по умолчанию — адрес источника.'})),
    priv.row);
  check.onclick = async () => {
    const href = url.value.trim();
    if (!/^https:\/\//i.test(href)) {
      markBad(url, bad, 'нужен HTTPS-адрес: http:// и частные адреса по умолчанию закрыты');
      return;
    }
    check.disabled = true;
    try {
      const r = await request('preview', {url: href, name: name.value.trim(),
                                          allow_private: allowPrivate});
      if (!r.ok) { markBad(url, bad, humanError(r.error)); return; }
      bad.hidden = true;
      const kind = {'uri-list': 'список ссылок', base64: 'base64',
                    singbox: 'sing-box JSON'}[r.type] || r.type;
      setText(info, `${kind} · ${r.name} · профилей: ${r.profiles}`);
      info.hidden = false;
    } finally { check.disabled = false; }
  };
  openSheet({title: 'Добавить подписку', cls: 'wide', body, buttons: [cancel,
    {label: 'Добавить', kind: 'primary', act: async () => {
      const href = url.value.trim();
      if (!/^https:\/\//i.test(href)) {
        markBad(url, bad, 'нужен HTTPS-адрес: http:// и частные адреса по умолчанию закрыты');
        return;
      }
      const r = await request('add_subscription',
                              {url: href, name: name.value.trim(), allow_private: allowPrivate});
      if (!r.ok) { markBad(url, bad, humanError(r.error)); return; }
      if (url.isConnected) closeSheet();
    }}]});
  tick('add-subscription');
}

const subHost = url => {
  try { return new URL(url).host; } catch { return url; }
};

function profileRow(p, inUse) {
  const tags = [el('span', {class: 'badge vless', text: 'VLESS'})];
  if (p.missing) tags.push(el('span', {class: 'subs-missing', text: 'пропал у источника'}));
  if (inUse.has(p.id)) tags.push(el('span', {class: 'subs-used', text: 'в туннеле'}));
  return el('div', {class: 'subs-prof'},
    el('span', {class: 'ell', text: p.name}),
    tags,
    ibtn('trash', 'Удалить профиль', {onclick: () => askDelProfile(p)}));
}

function subBlock(s, profiles, inUse) {
  const mine = profiles.filter(p => p.source && p.source.subscription === s.id);
  const head = el('div', {class: 'subs-head'},
    el('div', {class: 'subs-id'},
      el('b', {class: 'ell', text: s.name}),
      el('span', {class: 'subs-url mono ell', text: subHost(s.url)})),
    el('button', {class: 'btn sm', onclick: async () => {
      closeSheet();
      const r = await request('update_subscription', {id: s.id});
      if (!r.ok) toast(humanError(r.error), 'err');
    }}, icon('refresh'), 'Обновить'),
    ibtn('trash', 'Удалить подписку', {onclick: () => askDelSub(s)}));
  const count = mine.length ? plural(mine.length, ['профиль', 'профиля', 'профилей'])
                            : 'профилей нет';
  const updated = s.updated_at
    ? ` · обновлена ${new Date(s.updated_at).toLocaleString('ru-RU')}` : '';
  const fail = s.last_error
    ? el('p', {class: 'sheet-warn st-err'}, icon('err'), el('span', {text: s.last_error})) : null;
  const priv = setSwitch('Разрешить частные адреса',
    'Источник во внутренней сети: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16.',
    async on => {
      priv.sw.setAttribute('aria-checked', String(on));
      const r = await request('set_allow_private', {id: s.id, on});
      if (!r.ok) {
        priv.sw.setAttribute('aria-checked', String(!on));
        toast(humanError(r.error), 'err');
      }
    });
  priv.sw.setAttribute('aria-checked', String(!!s.allow_private));
  return el('section', {class: 'subs-item', 'aria-label': s.name},
    head, el('p', {class: 'subs-meta', text: count + updated}), fail, priv.row,
    mine.length ? el('div', {class: 'subs-profs'}, mine.map(p => profileRow(p, inUse)))
      : el('p', {class: 'subs-meta', text: 'Профилей нет: «Обновить» перечитает подписку.'}));
}

function manualBlock(profiles, inUse) {
  return el('section', {class: 'subs-item', 'aria-label': 'Вставленные ссылки'},
    el('div', {class: 'subs-head'}, el('b', {text: 'Вставленные ссылки'})),
    el('div', {class: 'subs-profs'}, profiles.map(p => profileRow(p, inUse))));
}

async function subscriptionsSheet() {
  const r = await request('list_subscriptions');
  if (!r.ok) { toast(humanError(r.error), 'err'); return; }
  const subs = r.subscriptions || [], profiles = r.profiles || [];
  const inUse = new Set((r.tunnels || []).map(t => t.active).filter(Boolean));
  const blocks = subs.map(s => subBlock(s, profiles, inUse));
  const manual = profiles.filter(p => p.source && p.source.manual);
  if (manual.length) blocks.push(manualBlock(manual, inUse));
  const body = el('div', {class: 'subs'},
    blocks.length ? blocks : el('p', {class: 'subs-empty',
      text: 'Подписок пока нет: добавьте HTTPS-адрес со списком ссылок vless://.'}));
  openSheet({title: 'Подписки и профили', cls: 'wide', body, buttons: [
    {label: 'Добавить подписку…', icon: 'import', kind: 'ghost', left: true,
     act: () => addSubscriptionSheet()},
    {label: 'Закрыть', kind: 'primary', act: () => closeSheet()}]});
  tick('subscriptions');
}

function askDelSub(s) {
  ask({title: `Удалить подписку «${s.name}»?`, safe: closeSheet,
       text: 'Её профили удалятся из списка. Туннели и правила останутся, но без профиля ' +
             'VLESS-туннель ничего не заберёт.',
       buttons: [cancel, {label: 'Удалить', kind: 'danger solid', act: async () => {
         closeSheet();
         const r = await request('remove_subscription', {id: s.id});
         if (!r.ok) toast(humanError(r.error), 'err');
       }}]});
}

function askDelProfile(p) {
  ask({title: `Удалить профиль «${p.name}»?`, safe: closeSheet,
       text: 'Если профиль пришёл из подписки, обновление вернёт его, пока он есть у источника.',
       buttons: [cancel, {label: 'Удалить', kind: 'danger solid', act: async () => {
         closeSheet();
         const r = await request('remove_profile', {id: p.id});
         if (!r.ok) toast(humanError(r.error), 'err');
       }}]});
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
