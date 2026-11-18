'use strict';
/* Предпросмотр окна без Windows: подменяет window.pywebview.api, если окно
   открыто не в pywebview и в адресе есть ?mock=<сценарий>. В сборку не идёт
   (installer/tunnelvpn.spec исключает ui/dev).

   Фикстуры — ровно то, что отдаёт window.py: render(st) — статус службы
   (service.Core._status), renderVersion, renderHowto (window.Api._howto),
   renderLogs (строки service.Core._tail_log), ответы методов моста.

     python -m http.server -d lib/tunnelvpn/ui 8765
     http://127.0.0.1:8765/index.html?mock=ok            сценарий
     …&tab=settings  …&open=rules:work  …&tick=0         вкладка, лист, без опроса
     ?mock=update&upd=net|bad|link|error  …&outcome=updated|tunnel|error|installing   обновление
     ?mock=down&revive=error                             «Перезапустить службу» не вышло */
(() => {
  const P = new URLSearchParams(location.search);
  const SCENARIO = P.get('mock') || 'ok';
  const TICK_MS = P.get('tick') === '0' ? 0 : 1000;
  const delay = ms => new Promise(r => setTimeout(r, ms));
  const page = (fn, ...args) => (args.length > 1 ? window.callN(fn, args) : window.call(fn, args[0]));
  const clone = o => JSON.parse(JSON.stringify(o));

  // ------------------------------------------------------------ фикстуры
  const tunnel = (id, name, mode, active, confs, extra = {}) => ({
    id, name, mode, active, enabled: true, confs, rules: 0, records: 0, apps: 0, carries: true,
    full: mode === 'all', pinned: false,
    check: 'up', answer: mode === 'all' ? '' : '10.1.1.5', seq: 3, last: 'up',
    checking: false, tests: {}, awg: Object.fromEntries(confs.map(c => [c, /^(nl|awg|fi)/.test(c)])),
    ...extra});

  // Лист «Туннелирование»: тот же набор полей, что отдают service.py:_get_tunnels
  // и ответ set-tunnel, — списки по режиму.
  const rulesOf = t => ({
    id: t.id, name: t.name, mode: t.mode, active: t.active, pinned: !!t.pinned,
    switchable: t.mode === 'all' || t.pinned,
    include: t.mode === 'all' ? [] : ['corp.example', 'git', '10.0.0.0/33', '10.20.0.0/16'],
    include_apps: t.mode === 'all' ? [] : ['C:\\Program Files\\Mozilla Firefox\\firefox.exe'],
    exclude: ['198.51.100.7/32'],
    exclude_apps: t.mode === 'all' ? ['C:\\Program Files\\Steam\\steam.exe'] : []});

  const base = () => ({
    up: true, busy: '', checking: false, last_error: '', autostart: true,
    version: '0.3.4-beta', singbox: '1.14.0-lx', log_level: 'info', kill_switch: false,
    tun: 45, r_low: true, r_high: true, out: 'tunnel', check_seq: 3,
    exit_ip: '185.12.4.9', exit_state: 'tunnel', exit_country: 'NL', exit_city: 'Amsterdam',
    exit_org: 'AS9009 M247', v6_leak: '', v6_off: true, main_v6: false, corp_dns: '10.1.1.5',
    tunnels: [
      tunnel('home', 'Личный', 'all', 'nl-amsterdam-1', ['nl-amsterdam-1']),
      tunnel('work', 'Работа', 'list', 'corp-msk', ['corp-msk'], {rules: 12, answer: '10.1.1.5'}),
      tunnel('lab', 'Лаборатория', 'list', 'home-lab', ['home-lab'], {rules: 3, answer: 'git.lab.local'}),
    ]});

  const LONG = ['production-eu-central-frankfurt-amsterdam-backup-node-01',
                'corp-moscow-office-datacenter-primary-with-a-very-long-name',
                'home-lab-raspberry-pi-cluster-behind-double-nat', 'de-frankfurt-hetzner-cx22',
                'fi-helsinki-awg-obfuscated-mobile-friendly', 'jp-tokyo-latency-test',
                'us-west-oregon-streaming', 'kz-almaty-relay-for-family'];

  const SCENARIOS = {
    empty: () => ({...base(), up: false, exit_ip: '', tunnels: []}),
    noservice: () => ({up: false, no_service: true, daemon: false}),
    down: () => ({up: false, no_service: true, daemon: true}),
    portabledown: () => ({up: false, no_service: true, portable: true}),
    off: () => {
      const s = base();
      s.up = false; s.exit_ip = '';
      s.tunnels[1].last = 'error';
      s.tunnels[2].enabled = false;
      return s;
    },
    ok: base,
    spares: () => {
      const s = base();
      s.tunnels[0].confs = ['nl-amsterdam-1', 'de-frankfurt', 'fi-helsinki', 'us-west'];
      s.tunnels[0].awg = {'nl-amsterdam-1': true, 'de-frankfurt': false, 'fi-helsinki': true, 'us-west': false};
      s.tunnels[0].tests = {'de-frankfurt': {result: 'up', answer: '41 мс', at: 1760100000, checking: false},
                            'fi-helsinki': {result: 'error', answer: 'нет рукопожатия', at: 1760100000, checking: false}};
      return s;
    },
    silent: () => {
      const s = base();
      Object.assign(s.tunnels[1], {check: 'error', answer: ''});
      Object.assign(s.tunnels[2], {check: 'rules', answer: 'git.lab.local'});
      return s;
    },
    direct: () => {
      const s = base();
      s.out = 'direct'; s.exit_state = 'direct'; s.exit_ip = '46.242.14.241';
      Object.assign(s.tunnels[0], {check: 'error', answer: ''});
      return s;
    },
    // Основной не везёт, kill-switch закрыл выход: адреса нет (probe._slow_exit).
    blocked: () => {
      const s = base();
      Object.assign(s, {kill_switch: true, out: 'block', exit_state: 'blocked', exit_ip: '',
                        exit_country: '', exit_city: ''});
      Object.assign(s.tunnels[0], {check: 'error', answer: ''});
      return s;
    },
    // Основной выключен сам: всё остальное напрямую, это не запасной выход.
    mainoff: () => {
      const s = base();
      Object.assign(s, {out: 'direct', exit_state: 'direct', exit_ip: '46.242.14.241'});
      s.tunnels[0].enabled = false;
      return s;
    },
    stray: () => {
      const s = base();
      Object.assign(s, {out: 'direct', exit_state: 'direct', exit_ip: '46.242.14.241'});
      s.tunnels[0].enabled = false;
      for (const t of s.tunnels.slice(1)) Object.assign(t, {rules: 0, carries: false});
      return s;
    },
    leak: () => ({...base(), exit_state: 'leak', exit_ip: '46.242.14.241'}),
    v6leak: () => ({...base(), v6_leak: '2a00:1450:4010:c05::71'}),
    busy: () => ({...base(), up: false, exit_ip: '', busy: 'включаю'}),
    reconnect: () => ({...base(), up: false, exit_ip: '', busy: 'переподключаю'}),
    long8: () => {
      const s = base();
      s.exit_ip = '2001:0db8:85a3:0000:0000:8a2e:0370:7334';
      s.tunnels = LONG.map((n, i) => tunnel(`t${i}`, `Туннель ${i}`, i ? 'list' : 'all', n, [n],
                                            {rules: i * 7, enabled: i !== 5}));
      return s;
    },
    errors8: () => {
      const s = base();
      s.exit_state = 'leak';
      s.v6_leak = '2a00:1450:4010:c05::71';
      s.tunnels = LONG.map((n, i) => tunnel(`t${i}`, `Туннель ${i}`, i ? 'list' : 'all', n, [n], {
        rules: i * 3,
        check: ['error', 'rules', 'none', 'error'][i % 4],
        answer: i % 4 === 1 ? `very-long-subdomain-${i}.corp.example.internal.company.ru` : ''}));
      return s;
    },
    portable: base,
    rules: base,
    log400: base,
    settings: base,
    update: base,
  };

  // Как window.Api._settings_info + settings.service_state.
  const PORTABLE = ['portable', 'portabledown'].includes(SCENARIO);
  const SETTINGS_INFO = {
    portable: PORTABLE,
    admin: true,
    service: PORTABLE ? null
      : SCENARIO === 'noservice' ? {installed: false, running: false, start: ''}
      : {installed: true, running: SCENARIO !== 'down', start: 'auto'},
    tray_autostart: PORTABLE ? null : true,
    data_dir: PORTABLE ? 'D:\\Tools\\TunnelVPN-Data' : 'C:\\ProgramData\\TunnelVPN',
    logs_dir: 'C:\\ProgramData\\TunnelVPN\\state\\logs',
    repo: 'https://github.com/enkeym/tunnel-vpn',
  };

  // ------------------------------------------------------------ обновление
  // Как settings.plain_notes: Markdown релиза простым текстом.
  const NOTES = 'Обновление из окна: скачивание, проверка подписи и тихая установка.\n' +
                '• Установщик в стиле окна, обновление в два клика.\n' +
                '• Переезд с DualVPN переносит конфиги и туннелирование.';
  // Как update.pending; &outcome=updated|tunnel|error|installing — итог прошлого запуска.
  const OUTCOMES = {
    updated: {code: 0, state: 'updated'},
    tunnel: {code: 11, state: 'tunnel'},
    error: {code: 10, state: 'error', text: 'Обновление установлено, но служба TunnelVPN не запустилась. ' +
                                           'Подробности — в журнале установки: %TEMP%\\Setup Log …'},
    installing: {code: null, state: 'installing'},
  };
  let pending = OUTCOMES[P.get('outcome')] ? {version: '0.3.5', from: '0.3.4-beta', ...OUTCOMES[P.get('outcome')]} : null;
  let polls = 0;
  let dlCancel = false;

  // Как поток window.Api._download: ход, проверка, итог.
  async function download() {
    const total = Math.round(18.4 * 2 ** 20);
    let got = 0;
    dlCancel = false;
    while (got < total) {
      await delay(250);
      if (dlCancel) { page('updateFailed', {stage: 'cancel', text: ''}); return; }
      got = Math.min(total, got + Math.round(1.3 * 2 ** 20));
      if (P.get('upd') === 'net' && got > total / 2) {
        page('updateFailed', {stage: 'download', text: 'Не удалось скачать обновление → проверьте интернет и повторите'});
        return;
      }
      page('updateProgress', {got, total});
    }
    page('updateStage', 'verify');
    await delay(1200);
    if (P.get('upd') === 'bad') page('updateFailed', {stage: 'verify', text: 'подпись установщика: NotSigned'});
    else page('updateReady', {version: '0.3.5'});
  }

  // Как probe._check_result: ответ основного — адрес выхода, работает он,
  // если выход виден и идёт не мимо туннеля.
  function seeExit(s) {
    if (!s.up) return s;
    const ok = !!s.exit_ip && !['direct', 'block'].includes(s.out)
      && !['leak', 'direct', 'blocked'].includes(s.exit_state);
    (s.tunnels || []).filter(t => t.mode === 'all' && t.enabled)
      .forEach(t => Object.assign(t, {check: ok ? 'up' : 'error', answer: s.exit_ip || ''}));
    return s;
  }

  let st = seeExit(SCENARIOS[SCENARIO] ? SCENARIOS[SCENARIO]() : base());
  // Код 11: обновились, а туннель установщик не поднял.
  if (P.get('outcome') === 'tunnel') Object.assign(st, {up: false, exit_ip: ''});
  const T = id => (st.tunnels || []).find(t => t.id === id);

  // --------------------------------------------------------------- журнал
  function logLines(n) {
    const names = (st.tunnels || []).map(t => t.name);
    const lvls = ['INFO', 'INFO', 'INFO', 'DEBUG', 'WARN', 'INFO', 'ERROR'];
    const msgs = [
      'outbound/direct[direct]: outbound connection to 142.250.74.46:443',
      'dns: exchanged A www.google.com. 300 IN A 142.250.74.46',
      'endpoint/wireguard[wg-work]: outbound connection to 10.1.1.20:22',
      'router: match[3] domain_suffix=corp.example => wg-work',
      'endpoint/wireguard[wg-home]: handshake did not complete after 5 seconds, retrying',
      'inbound/tun[tun-in]: inbound packet connection from 172.19.0.1:53000',
      'endpoint/wireguard[wg-lab]: connection timeout: dial tcp 10.9.0.4:443: i/o timeout'];
    const out = ['=== запуск 2026-10-10 12:00:00 ==='];
    for (let i = 0; i < n - 1; i++) {
      const t = new Date(Date.UTC(2026, 9, 10, 9, 0, 0) + i * 1700);
      const stamp = t.toISOString().slice(0, 19).replace('T', ' ');
      const mark = i % 3 && names.length ? `[${names[i % names.length]}] ` : '';
      out.push(`${mark}+0300 ${stamp} ${lvls[i % lvls.length]} [${1000000 + i} ${i % 90}ms] ${msgs[i % msgs.length]}`);
    }
    return out;
  }
  let LOG = logLines(SCENARIO === 'log400' ? 400 : 60);
  let logN = LOG.length;

  const LINK_RE = /^(vless|hysteria2|ss|trojan|tuic|anytls|socks5|socks4|http):\/\/\S+/i;
  const BAD_PCT = /%(?![0-9A-Fa-f]{2})/;
  const CHECKED = ['http', 'socks4', 'socks5'];
  const protoOf = s => (s.match(LINK_RE) || [, ''])[1].toLowerCase();
  const dec = v => { try { return decodeURIComponent(v); } catch { return ''; } };
  const hostOf = s => {
    const m = /:\/\/(?:[^@/?#\s]*@)?([^:/?#\s]+)/.exec(s) || [, ''];
    const q = /[?&]host=([^&\s]+)/.exec(s);
    return (q && dec(q[1])) || m[1];
  };
  const portOf = s => {
    const q = /[?&]port=(\d+)/.exec(s);
    if (q) return +q[1];
    const m = /:\/\/(?:[^@/?#\s]*@)?[^:/?#\s]+:(\d+)/.exec(s);
    return m ? +m[1] : 443;
  };
  const labelOf = s => {
    const cut = s.lastIndexOf('#');
    const tail = cut > 0 ? dec(s.slice(cut + 1).split('&')[0]) : '';
    if (tail) return tail;
    const pk = /[?&]peer=([^&\s]+)/.exec(s);
    if (pk) return dec(pk[1]) || pk[1];
    const at = /:\/\/([^@/?#\s]*)@/.exec(s);
    return (at ? at[1] : hostOf(s)) || s.slice(0, 20);
  };

  let SUBS = [], PROFS = [], PID = 0, JOBS = 0, SID = 0;
  const newProf = (name, proto, source, host, port) => ({id: `p_${++PID}`, name, proto,
    host: host || `${proto}.mock.example`, port: port || 443,
    fingerprint: `mock${PID}`, source, missing: false});
  const linkOf = p => `${p.proto}://user@${p.host}:${p.port}#${encodeURIComponent(p.name)}`;
  const SUB_FIX = [['RU-1', 'vless'], ['DE-2', 'vless'], ['NL-3', 'hysteria2'],
                   ['US-4', 'socks5'], ['dead-01', 'socks5']];

  function vlessTunnel(first, tid) {
    if (tid) {
      const one = T(tid);
      if (one) { one.names = one.names || {}; return one; }
    }
    let t = st.tunnels.find(x => x.proto === 'vless');
    if (!t) {
      t = tunnel('proxy', first, 'all', '', [], {proto: 'vless', names: {}, enabled: false});
      st.tunnels.push(t);
    }
    return t;
  }

  function putProfiles(t, items) {
    const made = items.map(it => {
      const p = newProf(it.name, it.proto, it.source, it.host, it.port);
      PROFS.push(p);
      t.confs.push(p.id);
      t.names[p.id] = p.name;
      return p;
    });
    if (made.length && !t.active) { t.enabled = false; t.active = made[0].id; }
    return made;
  }

  function amneziaTunnel(tid) {
    let t = (tid ? T(tid) : null) || T(AMNEZIA.id);
    if (!t) {
      t = tunnel(AMNEZIA.id, AMNEZIA.name, 'all', '', [], {enabled: false});
      st.tunnels.push(t);
    }
    if (!t.confs.includes(AMNEZIA.conf)) {
      t.confs.push(AMNEZIA.conf);
      t.awg[AMNEZIA.conf] = true;
    }
    if (!t.active) t.active = AMNEZIA.conf;
    return t;
  }

  function runJob(t, items) {
    const todo = items.filter(p => CHECKED.includes(p.proto));
    if (!todo.length) return ['', []];
    const bad = todo.filter(p => /dead/i.test(p.name));
    const id = String(++JOBS);
    const job = state => ({id, kind: 'add', state, total: todo.length,
                           done: state === 'done' ? todo.length : 0,
                           added: todo.length - bad.length, failed: bad.length,
                           problems: bad.map(p => ({name: p.name,
                                                     reason: 'прокси не прошёл HTTPS-проверку'})),
                           error: ''});
    st.job = job('running');
    setTimeout(() => {
      st.job = job('done');
      bad.forEach(p => {
        PROFS = PROFS.filter(x => x !== p);
        t.confs = t.confs.filter(c => c !== p.id);
        delete t.names[p.id];
      });
      if (bad.some(p => p.id === t.active)) t.active = t.confs[0] || '';
      pushStatus();
    }, 1800);
    return [id, bad];
  }

  // ----------------------------------------------------------- отрисовка
  // Как service.Core._status: каждый статус службы — со своим номером rev, без службы — без него.
  let rev = 0;
  const snap = () => (st.no_service ? clone(st) : {...clone(st), rev: ++rev});
  const pushStatus = () => page('render', snap());
  function pushFull() {
    pushStatus();
    if (st.no_service) return;
    page('renderVersion', {app: st.version, singbox: st.singbox});
    // Как service.Core._howto: выключенный VPN — пусто, прошлую сводку держит страница.
    page('renderHowto', {
      endpoints: st.up ? (st.tunnels || []).filter(t => t.enabled).map(t => {
        const list = t.mode === 'list';
        return {tag: `wg-${t.id}`, address: list ? '10.1.1.77/32' : '10.8.0.2/32',
                mtu: t.awg[t.active] ? 1280 : 1420, awg: !!t.awg[t.active], peer: '203.0.113.7',
                dns: list && t.id === 'work' ? '10.1.1.5' : '',
                nets: list ? (t.id === 'work' ? ['10.1.0.0/16', '192.168.50.0/24'] : ['10.9.0.0/24']) : [],
                domains: list ? (t.id === 'work' ? ['corp.example', 'git.corp.example'] : ['lab.local']) : []};
      }) : []});
    page('renderLogs', LOG.slice());
  }
  // Как service.Core._apply_locked (empty): удалён последний конфиг — VPN выключен, без ошибки.
  function emptied() {
    if (!st.tunnels.some(t => t.confs.length)) Object.assign(st, {up: false, exit_ip: ''});
  }

  // was — ответы до включения (start): проверка возвращает их туннелям.
  async function check(was) {
    if (!st.up) return;
    page('checkStart');
    const left = (st.tunnels || []).map(t => t.id);
    for (const t of st.tunnels) {
      await delay(250 + Math.random() * 400);
      if (was instanceof Map) t.check = was.get(t.id) || 'up';
      if (t.mode === 'all') seeExit(st);
      left.splice(left.indexOf(t.id), 1);
      page('checkSides', snap(), left.slice());
    }
    pushStatus();
    page('checkDone');
  }

  async function recheck(t) {
    const was = t.check;
    Object.assign(t, {check: '', checking: true});
    pushStatus();
    await delay(900 + Math.random() * 600);
    Object.assign(t, {check: was || 'up', checking: false});
    pushStatus();
  }

  async function testConf({tunnel, name}) {
    const t = T(tunnel);
    if (!t) return;
    t.tests[name] = {...(t.tests[name] || {}), checking: true};
    await delay(1200);
    t.tests[name] = {result: /fi-/.test(name) ? 'error' : 'up', answer: /fi-/.test(name) ? 'нет рукопожатия' : '44 мс',
                     at: Date.now() / 1000, checking: false};
    t.seq += 1;
    pushStatus();
    page('testDone', {tunnel, name});
  }

  // Грубая копия routelist.check — только чтобы лист было на чём посмотреть;
  // в окне разбирает Python. clash — у «Лаборатории» corp.example занят «Работой».
  function mockCheck(text, clash) {
    const out = {count: 0, domains: 0, ips: 0, nets: 0, problems: []}, seen = new Set();
    text.split('\n').forEach((line, i) => line.split(/[\s,;]+/).filter(Boolean).forEach(tok => {
      const ip = /^(\d+\.\d+\.\d+\.\d+)(?:\/(\d+))?$/.exec(tok);
      const why = ip && +(ip[2] ?? 32) > 32 ? 'маска больше 32 → для IPv4 маска от 0 до 32'
        : !ip && /[^a-z0-9.*-]/i.test(tok) ? 'в домене не бывает таких знаков → только буквы, цифры, дефис и точка'
        : !ip && !tok.includes('.') ? `короткое имя не сработает → укажите полный домен, например ${tok}.company.ru` : '';
      const p = text => out.problems.push({line: i + 1, token: tok, text});
      if (why) { if (!seen.has(tok)) { seen.add(tok); p(`«${tok}» — ${why}`); } return; }
      out.count += 1;
      if (!ip) out.domains += 1; else if ((ip[2] ?? '32') === '32') out.ips += 1; else out.nets += 1;
      if (clash && tok === 'corp.example') p('«corp.example» уже в «Работа» → одна запись — у одного туннеля: удалите её там или здесь');
    }));
    return out;
  }

  // Грубая копия routelist.check_apps: полный путь к .exe, по одному на строку.
  function mockCheckApps(text) {
    const out = {count: 0, problems: []}, seen = new Set();
    text.split('\n').forEach((line, i) => {
      const raw = line.trim(), path = raw.replace(/^"|"$/g, '');
      if (!raw) return;
      const why = !/\.exe$/i.test(path) ? 'это не программа → нужен путь к файлу .exe'
        : !/^([a-z]:\\|\\\\)/i.test(path) ? 'нужен полный путь → например C:\\Program Files\\App\\app.exe или кнопка «Добавить программу…»' : '';
      if (!why) { if (!seen.has(path.toLowerCase())) { seen.add(path.toLowerCase()); out.count += 1; } return; }
      if (!seen.has(raw)) { seen.add(raw); out.problems.push({line: i + 1, token: raw, text: `«${raw}» — ${why}`}); }
    });
    return out;
  }
  const lines = v => String(v || '').split('\n').map(s => s.trim()).filter(Boolean);
  const subUrl = s => /^(https:\/\/|happ:\/\/crypt(?:[2-5])?\/)/i.test(String(s).trim());
  const keyOf = p => `${p.proto}|${String(p.host).toLowerCase()}|${p.port}`;
  const vpnKeyOf = text => /vpn:\/\//i.test(String(text));
  const AMNEZIA = {id: 'amnezia-md', name: 'Amnezia Молдова', conf: 'amnezia-moldova',
                   host: 'amnezia.mock.example', port: 9129};

  function parseRows(text) {
    const seen = new Set(PROFS.map(keyOf));
    const items = [];
    let invalid = 0, duplicates = 0;
    lines(text).forEach(raw => {
      const proto = protoOf(raw);
      if (!proto || BAD_PCT.test(raw)) { invalid += 1; return; }
      const host = hostOf(raw), port = portOf(raw);
      const key = keyOf({proto, host, port});
      if (seen.has(key)) { duplicates += 1; return; }
      seen.add(key);
      items.push({name: labelOf(raw), proto, host, port, source: {manual: true}});
    });
    return {items, invalid, duplicates};
  }

  const subProfiles = sub => SUB_FIX.map(([name, proto]) => ({
    name, proto, host: `${name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}.mock.example`,
    port: proto === 'hysteria2' ? 443 : 8443, source: {subscription: sub.id}}));

  function newSub(url) {
    const sub = {id: `s_${++SID}`, name: hostOf(url) || 'Подписка', url, kind: 'vless',
                 allow_private: false, updated_at: new Date().toISOString(), last_error: ''};
    SUBS.push(sub);
    return sub;
  }

  function dropProfiles(match, tunnelId, dropTunnel) {
    let main = false;
    PROFS.filter(match).forEach(p => {
      st.tunnels.forEach(t => {
        if (t.proto !== 'vless') return;
        if (t.active === p.id) {
          main = main || (t.mode === 'all' && t.enabled !== false);
          t.active = '';
        }
        t.confs = t.confs.filter(c => c !== p.id);
        delete t.names[p.id];
      });
    });
    PROFS = PROFS.filter(p => !match(p));
    st.tunnels.filter(t => t.proto === 'vless' && !t.active)
      .forEach(t => { t.active = t.confs[0] || ''; });
    if (dropTunnel && tunnelId && !PROFS.length) {
      st.tunnels = st.tunnels.filter(t => t.id !== tunnelId);
    }
    emptied();
    return main;
  }

  // ------------------------------------------------------- мост: ответы
  const ok = (extra = {}) => ({ok: true, ...extra});
  const HANDLERS = {
    ready: () => { pushFull(); check(); afterReady(); },
    check,
    test_config: testConf,
    test_group: async arg => {
      for (const name of arg.names) await testConf({tunnel: arg.tunnel, name});
    },
    logs: () => page('renderLogs', LOG.slice()),
    // Как служба: после подъёма прошлых ответов нет — их даёт проверка (check).
    start: async () => {
      st.busy = 'включаю'; pushStatus(); await delay(900);
      const was = new Map(st.tunnels.map(t => [t.id, t.check]));
      st.tunnels.forEach(t => { t.check = ''; });
      // Ответ основного — тоже из проверки (check), не сразу с подъёмом:
      // служба стирает итоги, пока туннель опущен (probe._forget_checks).
      st.busy = ''; st.up = true; st.exit_ip = '185.12.4.9'; pushStatus();
      check(was);
    },
    stop: async () => { st.busy = 'выключаю'; pushStatus(); await delay(600); st.busy = ''; st.up = false; st.exit_ip = ''; pushStatus(); },
    // Как start-only службы: включён один этот туннель, остальные выключены.
    start_only: ({tunnel}) => {
      st.tunnels.forEach(t => { t.enabled = t.id === tunnel; });
      pushFull();
      return HANDLERS.start();
    },
    restart: async () => { st.up = false; pushStatus(); await delay(900); st.up = true; pushStatus(); },
    use_config: ({tunnel, name}) => { T(tunnel).active = name; pushFull(); },
    set_enabled: ({tunnel, on}) => {
      T(tunnel).enabled = on;
      // Без работающего основного selector out — direct, пробер видит адрес провайдера.
      if (T(tunnel).mode === 'all' && st.up) {
        Object.assign(st, on ? {out: 'tunnel', exit_state: 'tunnel', exit_ip: '185.12.4.9'}
                             : {out: 'direct', exit_state: 'direct', exit_ip: '46.242.14.241'});
        seeExit(st);
      }
      // Как apply службы: поднятый на лету туннель пробер проверяет заново
      // (remeasure), до ответа — checking в статусе, прошлого ответа нет.
      if (on && st.up) recheck(T(tunnel));
      pushFull();
    },
    set_mode: ({tunnel, mode, keep_enabled}) => {
      const target = T(tunnel);
      // Бывший основной — «по списку» без DNS и домена: проверять нечем.
      const demote = t => Object.assign(t, {mode: 'list', pinned: true, check: 'none', answer: ''});
      // Как service.py:_make_main: у выключенного основной не меняется —
      // вкладка листа только запоминает режим, живой VPN не трогаем.
      if (target.enabled !== false) {
        st.tunnels.forEach(t => { if (mode === 'all' && t.mode === 'all') demote(t); });
      }
      if (mode === 'all') target.mode = mode; else if (target.mode === 'all') demote(target);
      // Как service.py:_plan_tunnel: режим с вкладки помнит, что выбран человеком,
      // и полный конфиг в «по списку» остаётся переключаемым. Вкладка листа
      // выключенный туннель не включает, пункт меню ⋮ — включает.
      target.pinned = mode === 'list';
      if (mode === 'all' && !keep_enabled) target.enabled = true;
      if (mode === 'list') target.full = true;
      seeExit(st);
      pushFull();
      // Ответ службы несёт оба набора списков: лист рисует их без повторного запроса.
      return {ok: true, enabled: target.enabled, ...rulesOf(target)};
    },
    del_config: ({tunnel, name, drop_tunnel}) => {
      const t = T(tunnel);
      const main = t.active === name && t.enabled !== false && t.mode === 'all';
      t.confs = t.confs.filter(c => c !== name);
      if (t.active === name) t.active = t.confs[0] || '';
      if (drop_tunnel && !t.confs.length) st.tunnels = st.tunnels.filter(x => x !== t);
      page('closeSheet');
      emptied();
      pushFull();
      if (main) page('askFull');
      return {ok: true, main};
    },
    del_configs: ({tunnel, drop_tunnel}) => {
      const t = T(tunnel);
      const main = t.enabled !== false && t.mode === 'all' && t.confs.length > 0;
      const removed = t.confs.length;
      t.confs = [];
      t.active = '';
      if (drop_tunnel) st.tunnels = st.tunnels.filter(x => x !== t);
      page('closeSheet');
      emptied();
      pushFull();
      if (main) page('askFull');
      return {ok: true, main, removed};
    },
    del_tunnel: id => {
      const t = T(id);
      const main = !!t && t.enabled !== false && t.mode === 'all';
      st.tunnels = st.tunnels.filter(x => x.id !== id);
      emptied();
      pushFull();
      if (main) page('askFull');
      return {ok: true};
    },
    move_config: () => { page('closeSheet'); pushFull(); },
    apply_full: async () => { st.up = false; pushStatus(); await delay(900); st.up = true; pushFull(); },
    add_config: () => page('askPlace', {id: 'work', name: 'Работа', conf: 'corp-msk', file: 'corp-msk-2'}),
    place_config: () => pushFull(),
    preview: (arg = {}) => {
      if (arg.link) {
        if (vpnKeyOf(arg.link))
          return P.get('vpn') === 'fail'
            ? {ok: false, error: 'шлюз Amnezia недоступен'}
            : ok({type: 'link', name: AMNEZIA.name, host: AMNEZIA.host,
                  port: AMNEZIA.port, profiles: 1, invalid: 0, duplicates: 0});
        const p = parseRows(arg.link);
        if (!p.items.length) return {ok: false, error: 'нет поддерживаемых ссылок прокси или VPN'};
        const first = p.items[0];
        return ok({type: 'link', name: first.name, host: first.host,
                   port: first.port, profiles: p.items.length,
                   invalid: p.invalid, duplicates: p.duplicates});
      }
      if (!arg.url) return {ok: false, error: 'нужна ссылка vless://, hysteria2:// или HTTPS-адрес подписки'};
      const host = hostOf(arg.url);
      return ok({type: 'vless', name: host || 'Подписка', host, port: portOf(arg.url),
                 profiles: SUB_FIX.length});
    },
    add_link: async ({link = '', tunnel = ''} = {}) => {
      if (vpnKeyOf(link)) {
        await delay(600);
        if (P.get('vpn') === 'fail') return {ok: false, error: 'шлюз Amnezia недоступен'};
        const t = amneziaTunnel(tunnel);
        page('closeSheet');
        pushFull();
        return ok({tunnel: t.id, place: 'all', added: 1, profiles: 1,
                   checking: true, invalid: 0, duplicates: 0});
      }
      const p = parseRows(link);
      if (!p.items.length) return {ok: false, error: 'нет поддерживаемых ссылок прокси или VPN'};
      const t = vlessTunnel(p.items[0].name, tunnel);
      const made = putProfiles(t, p.items);
      const [job] = runJob(t, made);
      page('closeSheet');
      pushFull();
      return ok({profile: made[0].id, tunnel: t.id, place: 'to', added: made.length,
                 profiles: made.length, invalid: p.invalid, duplicates: p.duplicates,
                 job, checking: !!job});
    },
    add_subscription: ({url = ''} = {}) => {
      if (!subUrl(url)) return {ok: false, error: 'нужен HTTPS-адрес подписки'};
      const sub = newSub(url);
      const rows = subProfiles(sub);
      const t = vlessTunnel(rows[0].name);
      const made = putProfiles(t, rows);
      runJob(t, made);
      page('closeSheet');
      pushFull();
      return ok({subscription: sub.id, profiles: made.length, tunnel: t.id, profile: made[0].id});
    },
    list_subscriptions: () => ok({subscriptions: clone(SUBS), profiles: clone(PROFS),
      tunnels: st.tunnels.filter(t => t.proto === 'vless')
        .map(t => ({id: t.id, name: t.name, active: t.active}))}),
    update_subscription: ({id = ''} = {}) => {
      const sub = SUBS.find(s => s.id === id);
      if (!sub) return {ok: false, error: `нет подписки ${id}`};
      const rows = subProfiles(sub).filter(r => !(P.get('sub') === 'gone' && r.name === 'US-4'));
      const mine = PROFS.filter(p => p.source && p.source.subscription === id);
      const known = new Set(mine.map(p => p.name));
      const made = putProfiles(vlessTunnel(sub.name), rows.filter(r => !known.has(r.name)));
      const live = new Set(rows.map(r => r.name));
      let missing = 0;
      PROFS.filter(p => p.source && p.source.subscription === id)
        .forEach(p => { p.missing = !live.has(p.name); if (p.missing) missing += 1; });
      sub.updated_at = new Date().toISOString();
      sub.last_error = '';
      pushFull();
      return ok({id, added: made.length, updated: rows.length - made.length, missing,
                 total: rows.length + missing});
    },
    remove_subscription: ({id = ''} = {}) => {
      const sub = SUBS.find(s => s.id === id);
      if (!sub) return {ok: false, error: `нет подписки ${id}`};
      SUBS = SUBS.filter(s => s.id !== id);
      const main = dropProfiles(p => p.source && p.source.subscription === id);
      pushFull();
      if (main) page('askFull');
      return ok({id});
    },
    remove_profile: ({id = '', tunnel = '', drop_tunnel = false} = {}) => {
      if (!PROFS.some(p => p.id === id)) return {ok: false, error: `нет профиля ${id}`};
      const main = dropProfiles(p => p.id === id, tunnel, drop_tunnel === true);
      pushFull();
      if (main) page('askFull');
      return ok({id, main});
    },
    links: ({scope = '', id = ''} = {}) => {
      const of = f => PROFS.filter(f);
      let rows = of(() => true);
      if (scope === 'profile') rows = of(p => p.id === id);
      else if (scope === 'subscription') rows = of(p => p.source && p.source.subscription === id);
      else if (scope === 'manual') rows = of(p => p.source && p.source.manual);
      else if (scope !== 'all') return {ok: false, error: 'неизвестная область экспорта ссылок'};
      if (!rows.length) return {ok: false, error: 'в выбранной области нет профилей'};
      return ok({text: rows.map(linkOf).join('\n'), profiles: rows.length});
    },
    export_links: () => {},
    show_config: ({tunnel, name}) => page('showConf', tunnel, name,
      '[Interface]\nPrivateKey = MOCK+private+key+preview+only+not+a+real+key0=\nAddress = 10.8.0.2/32\n' +
      'DNS = 10.1.1.5\nMTU = 1420\nJc = 4\nJmin = 40\nJmax = 70\nS1 = 15\nS2 = 68\nH1 = 1106457265\n\n' +
      '[Peer]\nPublicKey = MOCK+public+key+preview+only+not+a+real+key00=\n' +
      'PresharedKey = MOCK+preshared+key+preview+only+not+a+real+k=\nAllowedIPs = 0.0.0.0/0\n' +
      'Endpoint = vpn.example.net:51820\nPersistentKeepalive = 25\n'),
    conf_details: ({name}) => (name.startsWith('de') ? {ok: false, error: 'запрос прав отменён или не выполнился'} : ok({
      path: `C:\\ProgramData\\TunnelVPN\\conf\\tunnels\\home\\${name}.conf`, awg: true,
      endpoint: 'vpn.example.net:51820', address: ['10.8.0.2/32'], dns: ['10.1.1.5'], allowed: ['0.0.0.0/0'],
      keepalive: '25', mtu: 1280, mtu_file: 1420, cut: true,
      mask: [['Jc', '4'], ['Jmin', '40'], ['Jmax', '70'], ['S1', '15'], ['S2', '68'], ['H1', '1106457265']],
      problem: name.startsWith('fi') ? 'Endpoint должен быть host:port, получено \'vpn.example.net\'' : ''})),
    edit_config: () => {},
    tunnel_rules: id => page('showRules', rulesOf(T(id))),
    save_rules: arg => page('rulesSaved', {include: String(arg.include || '').split(/[\s,;]+/).filter(Boolean),
                                           exclude: String(arg.exclude || '').split(/[\s,;]+/).filter(Boolean),
                                           include_apps: lines(arg.include_apps),
                                           exclude_apps: lines(arg.exclude_apps),
                                           rejected: {}, mode: '',
                                           applied: T(arg.tunnel).enabled === false ? 'off' : 'sides'}),
    validate_rules: arg => ok({fields: {
      ...Object.fromEntries(['include', 'exclude'].filter(k => k in arg)
        .map(k => [k, mockCheck(String(arg[k] || ''), k === 'include' && arg.tunnel === 'lab')])),
      ...Object.fromEntries(['include_apps', 'exclude_apps'].filter(k => k in arg)
        .map(k => [k, mockCheckApps(String(arg[k] || ''))]))},
      max: 500, max_apps: 100, fate: {}}),
    // Как window.Api._pick_app: путь из проводника — в поле листа.
    pick_app: ({field} = {}) => page('appPicked', {
      path: 'C:\\Program Files\\Mozilla Firefox\\firefox.exe', field: field || 'exclude_apps'}),
    load_rules: () => page('fillRules', {include: 'imported.example\n10.30.0.0/16', exclude: ''}),
    export_rules: () => {},
    set_autostart: async on => { await delay(400); st.autostart = !!on; page('autostartDone'); pushStatus(); },
    log_level: async on => { await delay(400); st.log_level = on ? 'debug' : 'info'; page('logLevelDone'); pushStatus(); },
    // Как Core._set_kill_switch: выход уже мимо основного — из direct в block и обратно.
    kill_switch: async on => {
      await delay(400);
      st.kill_switch = !!on;
      if (st.out === 'direct' && on && st.tunnels.some(t => t.mode === 'all' && t.enabled)) {
        Object.assign(st, {out: 'block', exit_state: 'blocked', exit_ip: ''});
      } else if (st.out === 'block' && !on) {
        Object.assign(st, {out: 'direct', exit_state: 'direct', exit_ip: '46.242.14.241'});
      }
      page('killSwitchDone'); pushStatus();
    },
    // Как window.Api._restart_service: ответ — когда канал снова отвечает, статус — раньше ответа.
    restart_service: async () => {
      await delay(1500);
      if (P.get('revive') === 'error') return {ok: false, error: 'Службу не удалось перезапустить → перезагрузите компьютер'};
      st = seeExit(base());
      pushFull();
      return ok();
    },
    rendered: () => {},
    jserror: msg => console.error('jserror:', msg),
    settings_info: () => ok(SETTINGS_INFO),
    set_tray_autostart: async on => { await delay(600); SETTINGS_INFO.tray_autostart = !!on; return ok({tray_autostart: !!on}); },
    diagnostics: () => ok({text: `TunnelVPN ${st.version}: ${st.up ? 'работает' : 'выключен'}\n  выход    : скрыт\n\nверсия   : ${st.version}`}),
    open_logs: () => ok(),
    // ?mock=update — есть свежее, иначе — последняя. &upd=error — нет связи,
    // link — ставить из окна нельзя (только ссылка), net — обрыв скачивания,
    // bad — файл не прошёл проверку.
    check_update: async () => {
      await delay(700);
      if (P.get('upd') === 'error') return ok({state: 'error'});
      return SCENARIO === 'update'
        ? ok({state: 'update', latest: '0.3.5', url: `${SETTINGS_INFO.repo}/releases/tag/v0.3.5`,
              notes: NOTES, install: P.get('upd') !== 'link', downtime: 30})
        : ok({state: 'latest', latest: '0.3.4-beta', url: `${SETTINGS_INFO.repo}/releases/tag/v0.3.4-beta`,
              notes: NOTES, install: false, downtime: 30});
    },
    update_download: () => { download(); return ok(); },
    update_cancel: () => { dlCancel = true; },
    update_install: async () => {
      await delay(400);
      pending = {version: '0.3.5', from: '0.3.4-beta', code: null, state: 'installing'};
      return ok();
    },
    // &outcome=installing: три опроса установщик работает, потом — обновлено.
    update_outcome: () => {
      if (pending && pending.state === 'installing' && P.get('outcome') === 'installing' && ++polls > 3) {
        pending = {...pending, code: 0, state: 'updated'};
      }
      return ok({pending: pending && clone(pending)});
    },
    update_seen: () => { if (pending && pending.state !== 'installing') pending = null; },
    open_url: url => (String(url).startsWith(SETTINGS_INFO.repo) ? ok() : {ok: false, error: 'чужая ссылка'}),
  };

  // Сценарий сам открывает вкладку или лист: ?tab=log, ?open=rules:work.
  function afterReady() {
    const tab = P.get('tab') || (SCENARIO === 'log400' ? 'log'
      : ['settings', 'update', 'portable'].includes(SCENARIO) ? 'settings' : '');
    if (tab && window.showTab) window.showTab(tab);
    const open = P.get('open') || (SCENARIO === 'rules' ? 'rules:work' : '');
    if (open.startsWith('rules:')) setTimeout(() => HANDLERS.tunnel_rules(open.slice(6)), 50);
    else if (open.startsWith('add')) {
      const tid = open.slice(4).replace(/^:/, '');
      setTimeout(() => { if (window.addSheet) window.addSheet(tid); }, 50);
    } else if (open === 'subs') {
      setTimeout(() => { if (window.subscriptionsSheet) window.subscriptionsSheet(); }, 50);
    }
  }

  // Опрос, как у window.poll: статус раз в секунду (POLL_EVERY там — 2 с;
  // здесь чаще, чтобы проверить, что обновление не сбивает меню и фокус).
  if (TICK_MS) {
    setInterval(() => {
      if (st.no_service) { pushStatus(); return; }
      if (SCENARIO === 'log400' || P.get('tab') === 'log') {
        LOG = [...LOG.slice(1), logLines(logN + 2)[logN + 1]];
        logN += 1;
        page('renderLogs', LOG.slice());
      }
      pushStatus();
    }, TICK_MS);
  }

  // Перетаскивание: путь файла в браузере не узнать — отвечаем, как служба на
  // добавление (window.py:_drop → _send_config).
  addEventListener('drop', e => {
    const f = [...(e.dataTransfer ? e.dataTransfer.files : [])].find(x => /\.(conf|vpn)$/i.test(x.name));
    if (!f) { page('failed', 'Нужен файл .conf или .vpn'); return; }
    f.text().then(text => {
      if (vpnKeyOf(text) || text.trim().startsWith('{') || parseRows(text).items.length)
        HANDLERS.add_link({link: text});
      else HANDLERS.add_config();
    }).catch(() => HANDLERS.add_config());
  });

  window.pywebview = {api: {
    send(name, arg) {
      const h = HANDLERS[name];
      if (!h) {
        console.warn('mock: нет ответа на', name, arg);
        return Promise.resolve(null);
      }
      // Как у pywebview: ответ — промисом, после текущего кадра.
      return delay(30).then(() => h(arg)).then(r => r ?? null);
    },
  }};
  window.MOCK = {get state() { return st; }, set state(v) { st = v; pushFull(); }, push: pushFull, SCENARIOS: Object.keys(SCENARIOS)};
  queueMicrotask(() => dispatchEvent(new Event('pywebviewready')));
})();
