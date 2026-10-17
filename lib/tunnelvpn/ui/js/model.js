'use strict';
/* Выводы из статуса службы, общие для разделов: основной туннель, запасной
   выход, молчащие туннели, заголовок окна и список проблем. render (core)
   зовёт model(s) раз на статус и раздаёт итог разделам вторым аргументом. */

// В окне туннель зовётся своим конфигом (решение #12), имя туннеля — запасное.
const tname = t => t.active || t.name;
const hasConf = t => !!(t.confs || []).length;

// ------------------------------------------------- проверка всех туннелей
// Идёт проверка — по кнопке, при открытии окна или сама после подъёма
// туннеля. CHECK_LEFT — id туннелей, чей ответ ещё не пришёл: быстрый не
// ждёт молчащего. CHECK_BY_HAND — итог покажет уведомление (по кнопке).
let CHECKING = false;
let CHECK_LEFT = new Set();
let CHECK_BY_HAND = false;
// Проверка одного конфига (↻ плитки, «Проверить все» без VPN): «id/имя», чей
// ответ ещё не пришёл, — checking в статусе появится только со следующим
// опросом (cards.js:testStart). TEST_ALL — их запустила «Проверить все»: она
// крутится только за свои проверки, ↻ одной плитки — не её (topbar.js).
const TESTING = new Set();
let TEST_ALL = false;
const testingAny = s => TESTING.size > 0 ||
  (s.tunnels || []).some(t => Object.values(t.tests || {}).some(r => r.checking));

// Python зовёт их и при сбое службы: кнопка не должна застрять.
function checkStart() {
  CHECKING = true;
  CHECK_LEFT = new Set((LAST.tunnels || []).map(t => t.id));
  render(LAST);
}
// Один туннель проверен, другие ещё ждут: перекрашивается только проверенный.
function checkSides(s, left) {
  CHECK_LEFT = new Set(left.filter(k => CHECK_LEFT.has(k)));
  render(s);
}
function checkDone() {
  CHECKING = false;
  CHECK_LEFT = new Set();
  render(LAST);
  if (CHECK_BY_HAND) {
    CHECK_BY_HAND = false;
    const m = model(LAST);
    if (LAST.up) toast(m.problems.length ? `Проверено: ${problemCount(m.problems)}` : 'Проверено: все туннели отвечают',
                       m.worst || 'ok');
  }
}

// Первая проверка после включения VPN: пока ни один включённый туннель не
// ответил, кнопка VPN не зеленеет раньше ⏻ плиток — обе дышат (topbar.js,
// cards.js:paintCard). Только после замеченного подъёма: проверка по кнопке
// или при открытии окна — не включение. SETTLING — когда замечен подъём, 0 —
// не ждём; ответа нет дольше PENDING_MS (topbar.js) — перестаём ждать.
let SETTLING = 0;
let WAS_UP = null;

// --------------------------------------------- обновление из окна (settings.js)
// installing — установщик запущен: до самого закрытия окна заголовок и кнопки
// говорят «Обновляю TunnelVPN…», а не «Выключено» или «Служба не отвечает»;
// tunnel — обновились, а VPN установщик обратно не поднял. version — до какой.
let UPD = {state: '', version: ''};

// «Перезапустить службу» (topbar.js:reviveService) нажата, итога ещё нет:
// заголовок говорит «Перезапускаю службу…», а не «Служба не отвечает».
let REVIVING = false;

// ---------------------------------------------------------------- модель
function model(s) {
  const up = !!s.up;
  const T = s.tunnels || [];
  // Работающий основной (tray._main_on): выключенный остаётся основным в
  // карточке, но трафик не везёт — всё остальное идёт напрямую, не запасным выходом.
  const main = T.find(t => t.mode === 'all' && t.enabled !== false);
  // Пока идёт проверка, прежний ответ устарел: «не отвечает» от прошлой
  // проверки читалось бы как свежий результат. Каждый туннель по себе.
  const checking = t => up && ((CHECKING && CHECK_LEFT.has(t.id)) || !!t.checking);
  // Основной не работает, и служба увела выход напрямую: адрес провайдера
  // тогда ожидаем, это не утечка. Без основного напрямую идёт всё — так задумано.
  // Kill-switch: выход не напрямую, а закрыт. out служба переключает раньше, чем проверка
  // выхода перемерит exit_state, — как у трея (tray.py:_away), out решает первым.
  const blocked = !!main && (s.out === 'block' || (s.out !== 'direct' && s.exit_state === 'blocked'));
  const direct = !!main && !blocked && (s.out === 'direct' || s.exit_state === 'direct');
  // «По списку» молчит, а остальное работает — отдельный случай, не «всё сломалось».
  const silent = up ? T.filter(t => t.mode === 'list' && t.enabled !== false
                                   && t.check === 'error' && !checking(t)) : [];
  // Без службы кнопки бессмысленны: портативной её не нужно, установленной —
  // нужна установка (daemon === false) или она не отвечает.
  const noService = !!s.no_service;
  const noDaemon = noService && s.daemon === false && !s.portable;
  // Туннель без конфига сборка пропускает, без всех — не соберёт
  // (buildconfig.py:build). Говорим заранее, а не «Не запускается» после нажатия.
  const missing = !noService && !up && !!s.tunnels && !T.some(hasConf);
  if (!s.loading && !noService) {
    if (WAS_UP === false && up) SETTLING = Date.now();
    WAS_UP = up;
  }
  const on = T.filter(t => t.enabled !== false && hasConf(t));
  // Ждём основной, а не любого: ответивший «по списку» ещё не значит, что идёт
  // всё остальное, и «Всё работает» вставало бы раньше ответа основного.
  const lead = main && hasConf(main) ? [main] : on;
  const answered = lead.some(t => t.check && !checking(t));
  if (!up || !on.length || answered || direct || blocked || s.exit_state === 'leak'
      || Date.now() - SETTLING > PENDING_MS) SETTLING = 0;
  const settling = SETTLING > 0;
  // Туннели, чья плитка пишет «проверяю…» (cards.js:cardState): ответа нет или идёт
  // проверка. Без выбранного конфига служба туннель не поднимает — и не ждём.
  const waiting = up ? on.filter(t => confOf(t) && (!t.check || checking(t))) : [];
  const ctx = {s, up, T, main, checking, direct, blocked, silent, noService, noDaemon, missing, settling, waiting};
  const problems = problemsOf(ctx);
  const worst = problems.length ? problems[0].kind : '';
  return {...ctx, head: headOf(ctx), problems, worst};
}

// Заголовок: {kind, title, sub}. Порядок веток — как у значка трея
// (tray.py:_tray_state): утечка IPv6 раньше запасного выхода, на запасном
// выходе адрес провайдера ожидаем.
function headOf({s, up, T, main, direct, blocked, silent, noService, noDaemon, missing, settling, waiting}) {
  if (UPD.state === 'installing') return {kind: 'busy', title: 'Обновляю TunnelVPN…',
                                         sub: 'окно закроется и откроется снова, когда установка закончится'};
  if (s.loading) return {kind: 'busy', title: 'Подключаюсь к службе…', sub: ''};
  if (noDaemon) return {kind: 'unset', title: 'Нужна установка',
                        sub: 'служба Windows не установлена — без неё VPN не включить'};
  if (noService && REVIVING) return {kind: 'busy', title: 'Перезапускаю службу…',
                                     sub: 'обычно это занимает несколько секунд'};
  // Процесс службы бывает жив, а канал молчит: диспетчер служб её сам не
  // перезапустит, кнопка в топ-баре — да (трей тоже, через HEAL_AFTER).
  if (noService) return s.portable
    ? {kind: 'err', title: 'Служба не отвечает',
       sub: 'VPN держит процесс в трее → закройте окно и запустите TunnelVPN-Portable.exe заново'}
    : {kind: 'err', title: 'Служба не отвечает',
       sub: 'без неё VPN не включить → «Перезапустить службу»'};
  if (missing) return {kind: 'unset', title: 'Добавьте конфиг', sub: 'без него VPN не включится'};
  // Служба сама снимает и поднимает туннель: на это время up ложный, и без
  // этой ветки заголовок мигал бы «Выключено» или прошлой ошибкой.
  if (s.busy === 'переподключаю') return {kind: 'busy', title: 'Переподключаю',
                                          sub: 'сменилась сеть — поднимаю туннель заново'};
  // Сторож перезапускает процесс одного туннеля: up при этом истинный, tun цел.
  if (up && (s.busy || '').startsWith('перезапускаю')) {
    return {kind: 'busy', title: 'Перезапускаю', sub: `${s.busy}, интернет работает`};
  }
  if (s.v6_leak) return {kind: 'err', title: 'Утечка IPv6',
                         sub: `часть трафика идёт мимо туннеля: ${s.v6_leak}`};
  // Обновление встало, а туннель — нет: «Включить» рядом, в кнопках топ-бара.
  if (!up && UPD.state === 'tunnel') return {kind: 'warn', title: `Обновлено до ${UPD.version}, VPN не включился`,
                                             sub: 'установщик не поднял туннель обратно → «Включить» вернёт его'};
  // Первая попытка не вышла, служба повторит сама (service.py:_status retrying):
  // при загрузке ПК сеть встаёт позже службы — это ожидание, не сбой.
  if (!up && s.retrying) return {kind: 'busy', title: 'Жду сеть…',
                                 sub: `первая попытка не вышла: ${s.last_error || 'нет сети'} — повторю сам`};
  // Не поднялся и не просто выключен — причина, а не пустота.
  if (!up && s.last_error) return {kind: 'err', title: 'Не запускается', sub: s.last_error};
  if (!up) return {kind: 'off', title: 'Выключено', sub: 'VPN не включён'};
  // Заголовок обязан отражать карточки ниже: сверху «всё работает», а у
  // основного — «мимо туннеля» было бы прямым противоречием.
  if (blocked) return {kind: 'err', title: 'Интернет закрыт',
                       sub: `«${tname(main)}» не работает, kill-switch не пускает трафик мимо туннеля`};
  if (direct) return {kind: 'warn', title: 'Запасной выход',
                      sub: `«${tname(main)}» не работает, интернет идёт напрямую`};
  if (s.exit_state === 'leak') return {kind: 'err', title: 'Трафик мимо туннеля',
                                       sub: 'Сайты видят адрес провайдера → Перезапустить или проверьте основной туннель'};
  if (silent.length) {
    return {kind: 'warn',
            title: silent.length === 1 ? `«${tname(silent[0])}» не отвечает` : 'Туннели не отвечают',
            sub: silent.length < T.length ? 'остальные туннели работают' : 'туннели подняты, но молчат'};
  }
  // «Позже» в «Переподключить VPN?»: правка на диске, а VPN работает по старой
  // (service.py:pending_apply) — «Всё работает» скрыло бы это до выключения.
  if (s.pending_apply) return {kind: 'warn', title: 'Правка ждёт переподключения',
                               sub: 'сохранено, применится после переподключения → «Перезапустить»'};
  // ⏻ и кнопка VPN дышат (settling), а плитки пишут «проверяю…»: «Всё работает»
  // сверху обгоняло бы ответ туннелей.
  if (settling || waiting.length) {
    return {kind: 'busy', title: 'Проверяю…',
            sub: waiting.length === 1 ? `жду ответа «${tname(waiting[0])}»` : 'жду ответа туннелей'};
  }
  const live = T.filter(t => t.enabled !== false).length;
  return {kind: 'ok', title: 'Всё работает',
          sub: !main ? 'всё остальное — напрямую, без VPN' : live > 1 ? 'туннели подняты' : 'туннель поднят'};
}

// Проблемы для пилюли и панели: {kind: err|warn, id (туннель или ''), where,
// text, fix, act}. act — что предложить: check, rules, log, card, restart,
// add (topbar.js). Ошибки раньше предупреждений, внутри — по порядку туннелей.
function problemsOf({s, up, T, main, checking, direct, blocked, noService}) {
  if (noService || s.loading || UPD.state === 'installing') return [];
  const out = [];
  const vpn = (kind, text, fix, act) => out.push({kind, id: '', where: 'VPN', text, fix, act});
  const of = (t, kind, text, fix, act) => out.push({kind, id: t.id, where: tname(t), text, fix, act});

  if (s.v6_leak) vpn('err', `Утечка IPv6: ${s.v6_leak}`, 'перезапустите VPN', 'restart');
  if (!up && s.last_error && !s.retrying) vpn('err', `Не запускается: ${s.last_error}`, 'подробности — в журнале', 'log');
  if (up && blocked) of(main, 'err', 'не работает — kill-switch закрыл интернет', 'проверьте конфиг или выберите запасной', 'card');
  else if (up && direct) of(main, 'warn', 'не работает — интернет идёт напрямую', 'проверьте конфиг или выберите запасной', 'card');
  else if (up && s.exit_state === 'leak') vpn('err', 'трафик идёт мимо туннеля', 'перезапустите VPN', 'restart');

  for (const t of T) {
    if (t.enabled === false) continue;
    // Пустой туннель (конфиг удалён, правила оставлены) — не сбой: карточка сама
    // пишет «Нет конфига», а без конфигов совсем заголовок говорит «Добавьте конфиг».
    if (!hasConf(t)) continue;
    // Без active и с несколькими конфигами служба не выберет за человека.
    if (!t.active && t.confs.length > 1) {
      of(t, 'warn', 'не выбран конфиг — служба не поднимет туннель', 'выберите один в карточке', 'card');
      continue;
    }
    if (!up || t.mode !== 'list' || checking(t)) continue;
    if (t.check === 'error') of(t, 'warn', 'не отвечает', 'проверьте ещё раз или загляните в журнал', 'check');
    else if (t.check === 'rules') {
      of(t, 'warn', `«${t.answer || ''}» через него не открывается`, 'проверьте туннелирование', 'rules');
    } else if (t.check === 'none') {
      of(t, 'warn', 'нечем проверить', 'добавьте домен в «Пускать через туннель»', 'rules');
    }
  }
  return out.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'err' ? -1 : 1));
}

// «2 ошибки · 1 предупреждение».
function problemCount(problems) {
  const err = problems.filter(p => p.kind === 'err').length, warn = problems.length - err;
  return [err && plural(err, ['ошибка', 'ошибки', 'ошибок']),
          warn && plural(warn, ['предупреждение', 'предупреждения', 'предупреждений'])]
    .filter(Boolean).join(' · ');
}
