#!/usr/bin/env node
/* The phone app's office host (web/office_app_host.js), run in node against a fake bridge.
 *
 *   - the snapshot is built from the desktop's /link/office/api answers by the very same build() / track()
 *   - the calls it makes (method, path, body) and their pace: 3 s / 9 s / 30 s
 *   - the sync members office.html reads at its top level (get, lang, uiScale, boardOnly, isBoardKey, md, chat.has)
 *   - read only: no send / answer / attach / dismiss, no board act / request / setEnabled, objects.action refused without a
 *     call (the personnel 編輯執掌 answered here); a person is handed to the app: native openChat / editBot / openUrl / close
 *   - active(false) stops the polling (and the ops window's heartbeat), active(true) polls at once
 *   - ready after the first snapshot; error: app_too_old, off, no_snapshot, script; a failed round keeps the last snapshot
 *   - nothing happens without window.SFOfficeApp (the desktop's iframe loads the same office.html)
 * Run: node tests_office_app_host.js */
'use strict';
const path = require('path');
const fs = require('fs');
const vm = require('vm');
const W = f => path.join(__dirname, 'web', f);
const core = require(W('office_core.js')), rules = require(W('chat_rules.js')), kit = require(W('bots_kit.js'));
const { create } = require(W('office_app_host.js'));

let pass = 0, fail = 0;
function check(name, ok, detail) {
  if (ok) pass++; else fail++;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + JSON.stringify(detail === undefined ? '' : detail)}`);
}
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise(r => setImmediate(r)); };

// ── what the desktop answers ──────────────────────────────────────────────────
const BOTS = { enabled: true, primary: 'main', roster: {
  main: { label: 'Main', responsibility: 'the lobby', bot: { primary: true, memory: true }, cli: { label: 'Claude' } },
  qa: { label: 'QA', responsibility: 'checks', bot: { memory: true } },
} };
const TABS = { success: true, now: 1, bots_on: true, tabs: [
  { sid: 's1', label: 'Main', cmd: 'claude', role: 'main', kind: 'bot', state: 'working', line: 'writing the plan', narration: 'Plan written.', loop: null },
  { sid: 's2', label: 'scratch', cmd: 'codex', role: '', kind: 'temp', state: 'decision', line: 'asking', narration: '', loop: null },
  { sid: 's3', label: 'term', cmd: '', role: '', kind: 'terminal', state: 'idle', line: '', narration: '', loop: null },
], helpers: { s1: [{ id: 'h1', description: 'look it up', model: 'm', agent_type: 'Explore', state: 'working', started_at: 100, last_at: 120 }] } };
const PEERS = { peers: { PEER1: { name: 'Studio Mac', primary: 'a', bots: [
  { role: 'a', label: 'Ann', sid: 'r1', state: 'idle', latest: 'hello' }, { role: 'b', label: 'Bo', state: 'asleep' }],
  tabs: { rq: { kind: 'temp', name: 'Quill', cli: 'claude', state: 'working', latest: 'x' } } } }, pending: [] };
const TASKS = { success: true, decisions: [{ id: 7, title: 'Pick', owner: 'main', requester: 'qa', status: 'decision' }], open: [], stuck: [],
  deliverables: { new: 2, by_task: {}, by_owner: { main: { label: 'Main', count: 3, new: 2, types: [] } } } };
const OBJECTS = { success: true, objects: [{ id: 'deliverables', title: 'Deliverables', furniture: 'tray', place: 'lobby:desk', badge: 0, filters: [], sorts: [], actions: [] },
                                          { id: 'personnel', title: 'Personnel', furniture: 'cabinet', place: 'lobby:wall', badge: 0, filters: [], sorts: [], actions: [] }] };
const ANSWERS = {
  bots_state: () => BOTS, office_tabs: () => TABS, link_peer_bots: () => PEERS, tasks_overview: () => TASKS,
  link_peer_tasks: () => ({ peers: { PEER1: { state: 'ok', at: 5, decisions: [], stuck: [], open: [] } } }), office_objects: () => OBJECTS,
  task_detail: a => ({ success: true, task: { id: a[0], events: [] } }), schedules_list: () => ({ schedules: [], configured: true, launchd_ok: true }),
  office_object_list: () => ({ groups: [], items: [], total: 0 }),
  office_object_item: a => ({ success: true, item: { title: 'x', markdown: '', actions: a[0] === 'personnel' ? ['edit_duty', 'secret_thing'] : ['keep', 'open'] } }),
  office_ops_view: () => ({ success: true, sections: [] }), office_ops_live: () => ({ success: true }),
};

// ── a fake page, bridge and clock ─────────────────────────────────────────────
function world(opts) {
  opts = opts || {};
  const W = { sent: [], timers: [], clock: 1000000, events: [], updates: [], errors: [], answers: Object.assign({}, ANSWERS, opts.answers || {}) };
  W.calls = () => W.sent.filter(m => m.op === 'call').map(m => m.body.method);
  W.native = () => W.sent.filter(m => m.op === 'native');
  W.posted = op => W.sent.filter(m => m.op === op);
  W.win = {
    SFOfficeApp: Object.assign({ v: 1, lang: 'en', uiScale: 1.2, idiom: 'pad', pairedIds: ['PEER1'], caps: { editBot: true, openChat: true },
                                 prefs: { seats: { main: 3 }, mode: 'flat', zoning: 'cli' } }, opts.app || {}),
    webkit: { messageHandlers: { sfoffice: { postMessage(m) {
      W.sent.push(JSON.parse(JSON.stringify(m)));
      if (m.op !== 'call') return undefined;
      if (opts.bridge) { const r = opts.bridge(m); if (r !== undefined) return Promise.resolve(r); }
      const f = W.answers[m.body.method];
      if (!f) return Promise.resolve({ success: false, code: 'no_method' });
      const out = f(m.body.args);
      return Promise.resolve({ success: true, result: opts.textResult ? JSON.stringify(out) : out });
    } } } },
    document: { dispatchEvent(e) { W.events.push(e.type); return true; } },
    Event: class { constructor(t) { this.type = t; } },
    addEventListener(t, f) { W.errors.push([t, f]); },
  };
  if (!opts.noOffice) W.win.Office = { update: s => { if (opts.updateThrows) throw new Error('boom'); W.updates.push(s); } };
  W.env = { window: W.win, core, rules, kit,
    now: () => W.clock,
    setTimeout: (f, ms) => { const t = { f, ms, id: W.timers.length + 1, live: true }; W.timers.push(t); return t.id; },
    clearTimeout: id => { const t = W.timers.find(x => x.id === id); if (t) t.live = false; },
    warn: () => {} };
  W.live = () => W.timers.filter(t => t.live);
  W.fire = async (ms) => { const t = W.live().find(x => ms == null || x.ms === ms); if (!t) return false; t.live = false; t.f(); await flush(); return true; };
  W.h = create(W.env);
  return W;
}

(async () => {
  console.log('the snapshot, from the desktop\'s answers');
  let w = world();
  check('init starts the polling', w.h.init() === true);
  await flush();
  const first = w.sent.filter(m => m.op === 'call');
  check('every call is POST /link/office/api with {method, args, lang}',
    first.length >= 5 && first.every(m => m.method === 'POST' && m.path === '/link/office/api' && typeof m.body.method === 'string'
      && Array.isArray(m.body.args) && m.body.lang === 'en'), first.map(m => m.path));
  check('the first round asks for tabs, peers, bots, tasks, peer tasks and objects, once each',
    JSON.stringify(w.calls().slice().sort()) === JSON.stringify(['bots_state', 'link_peer_bots', 'link_peer_tasks', 'office_objects', 'office_tabs', 'tasks_overview']), w.calls());
  check('Office.update got one snapshot', w.updates.length === 1);
  const S = w.updates[0] || {};
  const main = (S.bots || []).find(b => b.id === 'main'), qa = (S.bots || []).find(b => b.id === 'qa');
  check('a bot with a tab works (the tab light), carries tab:true and no sid', main && main.state === 'working' && main.tab === true && !('sid' in main), main);
  check('a bot with no tab is asleep, tab:false', qa && qa.state === 'asleep' && qa.tab === false, qa);
  check('an AI tab that is no bot is an intern asking (decision); a terminal is not shown',
    (S.temps || []).length === 1 && S.temps[0].id === 's2' && S.temps[0].state === 'decision' && S.temps[0].tab === true, S.temps);
  check('the subagent is a part-timer of the bot', (S.helpers || []).length === 1 && S.helpers[0].parent === 'main', S.helpers);
  const pr = (S.peers || [])[0];
  check('the paired computer: its bot with a tab, its sleeping bot, its intern', pr && pr.bots.find(b => b.id === 'a').tab === true
    && pr.bots.find(b => b.id === 'b').tab === false && pr.temps.length === 1 && pr.temps[0].tab === true, pr);
  check('the decision is on the board and the peer board arrived', (S.board.decisions || S.board.all || []).length >= 1 && pr.boardState === 'ok', S.board);
  check('the objects ride the snapshot and the deliverables badge follows tasks_overview',
    (S.objects || []).length === 2 && S.objects.find(o => o.id === 'deliverables').badge === 2, S.objects);
  check('the person\'s own 在辦公室看到他 event is there (track ran)', main && main.events && main.events.length === 1, main && main.events);
  check('ready was sent once with the app\'s v', w.posted('ready').length === 1 && w.posted('ready')[0].v === 1);
  check('no error was sent', w.posted('error').length === 0);
  check('a rounds timer is set for 3 s', w.live().some(t => t.ms === 3000), w.live().map(t => t.ms));

  console.log('the pace');
  w.sent.length = 0;
  w.clock += 3100; await w.fire(3000);
  check('3 s later: tabs and peers only (+ nothing else yet)', JSON.stringify(w.calls().sort()) === JSON.stringify(['link_peer_bots', 'office_tabs']), w.calls());
  w.sent.length = 0; w.clock += 6000; await w.fire(3000);
  check('9 s after the first: tasks and peer tasks', w.calls().includes('tasks_overview') && w.calls().includes('link_peer_tasks') && !w.calls().includes('office_objects'), w.calls());
  w.sent.length = 0; w.clock += 21000; await w.fire(3000);
  check('30 s after the first: objects and the bot roster again', w.calls().includes('office_objects') && w.calls().includes('bots_state'), w.calls());
  check('Office.update every round', w.updates.length === 4, w.updates.length);

  console.log('the answer as JSON text works too');
  const wt = world({ textResult: true });
  wt.h.init(); await flush();
  check('result given as a string is parsed', wt.updates.length === 1 && wt.updates[0].bots.length === 2);

  console.log('the sync members');
  const H = w.h.host;
  check('lang / uiScale / boardOnly / isBoardKey', H.lang() === 'en' && H.uiScale() === 1.2 && H.boardOnly() === false && H.isBoardKey({ key: 'B' }) === false);
  w.win.SFOfficeApp.lang = 'zh-Hant';
  check('lang follows SFOfficeApp.lang', H.lang() === 'zh-Hant' || H.lang() === 'zh', H.lang());
  w.win.SFOfficeApp.lang = 'en'; w.win.SFOfficeApp.uiScale = 9;
  check('uiScale out of 0.7–1.6 is 1', H.uiScale() === 1);
  check('get: the injected prefs; an unknown key is null', JSON.stringify(H.get('seats')) === '{"main":3}' && H.get('mode') === 'flat' && H.get('nope') === null);
  const nb = world({ app: { prefs: { prefs: { seats: { x: 1 } } } } });
  check('get: prefs given as the whole office_prefs answer', JSON.stringify(nb.h.host.get('seats')) === '{"x":1}');
  const np = world({ app: { prefs: undefined, caps: undefined, pairedIds: undefined } });
  check('missing prefs / caps / pairedIds have safe defaults', np.h.host.get('seats') === null && np.h.host.editBot('main') === false && np.h.host.chat.open('b:main') === false);
  H.set('mode', '3d'); H.set('panelTab', 'info');
  check('a display key is kept in memory', H.get('mode') === '3d' && H.get('panelTab') === 'info');
  H.set('seats', { main: 9 }); H.set('items', []); H.set('arrange', {}); H.set('groups', {});
  check('a shared key is not changed and the stray set is noted', JSON.stringify(H.get('seats')) === '{"main":3}' && w.h.sets.join() === 'seats,items,arrange,groups', w.h.sets);
  check('md: safe Markdown, the page\'s own renderer', H.md('**hi** <script>x</script>').includes('<strong>hi</strong>') && !H.md('<script>x</script>').includes('<script>'), H.md('**hi** <script>'));
  check('chat.has follows the last snapshot (a bot with a tab, asleep, an intern, an unknown one)',
    H.chat.has('b:main') === true && H.chat.has('b:qa') === false && H.chat.has('t:s2') === true && H.chat.has('t:zzz') === false);
  check('chat.has for the paired computer: bot with a tab, asleep one, intern',
    H.chat.has('p:PEER1/a') === true && H.chat.has('p:PEER1/b') === false && H.chat.has('q:PEER1/rq') === true);

  console.log('read only: the app does the talking');
  check('the chat host has only has / open / openUrl', JSON.stringify(Object.keys(H.chat).sort()) === '["has","open","openUrl"]', Object.keys(H.chat));
  check('the board host has no act / request / setEnabled / reply / lineOf', ['act', 'request', 'setEnabled', 'reply', 'lineOf'].every(k => !(k in H.board))
    && H.board.canRequest('b:main') === false && H.board.takeFocus() === null);
  w.sent.length = 0;
  check('chat.open(b:main) → native openChat with its sid', H.chat.open('b:main') === true
    && JSON.stringify(w.native()) === JSON.stringify([{ op: 'native', name: 'openChat', args: { key: 'b:main', sid: 's1', peer: '', rsid: '' } }]), w.native());
  w.sent.length = 0;
  check('chat.open(t:s2) → sid s2', H.chat.open('t:s2') === true && w.native()[0].args.sid === 's2');
  w.sent.length = 0;
  check('chat.open(p:PEER1/a) → peer and rsid', H.chat.open('p:PEER1/a') === true
    && JSON.stringify(w.native()[0].args) === '{"key":"p:PEER1/a","sid":"","peer":"PEER1","rsid":"r1"}', w.native());
  w.sent.length = 0;
  check('chat.open(q:PEER1/rq) → peer and rsid rq', H.chat.open('q:PEER1/rq') === true && w.native()[0].args.rsid === 'rq' && w.native()[0].args.peer === 'PEER1');
  w.sent.length = 0;
  check('a bot asleep, an unknown person: false and nothing sent', H.chat.open('b:qa') === false && H.chat.open('t:nope') === false && H.chat.open('p:PEER1/b') === false && w.sent.length === 0);
  w.win.SFOfficeApp.pairedIds = [];
  check('a computer the app is not paired with: false and nothing sent', H.chat.open('p:PEER1/a') === false && H.chat.has('p:PEER1/a') === false && w.sent.length === 0);
  w.win.SFOfficeApp.pairedIds = ['PEER1'];
  H.chat.openUrl('https://example.com/x'); H.chat.openUrl('javascript:alert(1)'); H.chat.openUrl('file:///etc/passwd');
  check('openUrl: http(s) only → native openUrl', JSON.stringify(w.native().map(m => m.args.url || m.name)) === '["https://example.com/x"]', w.native());
  w.sent.length = 0;
  check('editBot(role) → true and native editBot {role}', H.editBot('qa') === true && JSON.stringify(w.native()) === '[{"op":"native","name":"editBot","args":{"role":"qa"}}]', w.native());
  w.sent.length = 0; H.close();
  check('close → native close', JSON.stringify(w.native()) === '[{"op":"native","name":"close","args":{}}]', w.native());
  w.sent.length = 0;
  const ra = await H.objects.action('bookshelf', 'x', 'keep', {});
  const rb = await H.objects.action('deliverables', 'd1', 'open', {});
  const rc = await H.objects.action('personnel', 'bot:qa', 'edit_duty', {});
  const rd = await H.objects.action('personnel', 'bot:ghost', 'edit_duty', {});
  check('objects.action: refused without a call; the personnel edit_duty answered here as {go:{settings}}',
    ra.readonly === true && rb.readonly === true && rc.go && rc.go.settings === 'qa' && rd.readonly === true && w.sent.length === 0, [ra, rb, rc, rd]);
  const pv = await H.objects.peerView('PEER1', ''), pl = await H.objects.peerLive('PEER1', true);
  check('a paired computer\'s TV is not offered', pv.success === false && pv.code === 'old' && pl.success === false && w.sent.length === 0);

  console.log('on demand');
  w.sent.length = 0;
  const td = await H.board.detail('7'), sc = await H.board.schedules();
  check('board.detail → task_detail [7]; schedules → schedules_list, shaped', td.success === true && sc.ok === true && w.sent[0].body.args[0] === 7 && w.sent[1].body.method === 'schedules_list', w.sent);
  w.sent.length = 0; w.win.SFOfficeApp.lang = 'zh';
  await H.objects.list('bookshelf', { query: 'x' });
  const itp = await H.objects.item('personnel', 'bot:main'), itd = await H.objects.item('deliverables', 'd1');
  check('objects.list → office_object_list [id, json] with lang', w.sent[0].body.method === 'office_object_list' && w.sent[0].body.args[1] === '{"query":"x"}' && w.sent[0].body.lang === 'zh', w.sent[0]);
  check('objects.item keeps only the one action the app can do (personnel edit_duty); none for others',
    JSON.stringify(itp.item.actions) === '["edit_duty"]' && itd.item.actions.length === 0, [itp, itd]);
  w.win.SFOfficeApp.lang = 'en'; w.sent.length = 0;
  await H.objects.view('ops'); await H.objects.live('ops', true);
  check('ops: office_ops_view, then office_ops_live [true]', JSON.stringify(w.calls()) === '["office_ops_view","office_ops_live"]' && w.sent[1].body.args[0] === true, w.calls());

  console.log('active(false) / active(true)');
  w.sent.length = 0; w.events.length = 0;
  w.win.SFOfficeApp.active(false);
  await flush();
  check('active(false): no timer left, flag set, visibilitychange dispatched, the ops heartbeat told false',
    w.live().length === 0 && w.win.__sfInactive === true && w.events.includes('visibilitychange')
    && w.sent.some(m => m.body && m.body.method === 'office_ops_live' && m.body.args[0] === false), { live: w.live().length, flag: w.win.__sfInactive, sent: w.calls() });
  const before = w.updates.length;
  w.clock += 10000; await w.fire();
  check('nothing polls while in the background', w.updates.length === before && w.calls().filter(m => m === 'office_tabs').length === 0);
  w.sent.length = 0;
  w.win.SFOfficeApp.active(true);
  await flush();
  check('active(true): polls at once, a timer again, the heartbeat back, flag cleared',
    w.calls().includes('office_tabs') && w.live().length >= 1 && w.win.__sfInactive === false && w.sent.some(m => m.body && m.body.method === 'office_ops_live' && m.body.args[0] === true), w.calls());
  w.win.SFOfficeApp.active(false);

  console.log('a failed round keeps the last snapshot; offline backs off');
  const wf = world({ bridge: m => (wf && wf.down ? { success: false, code: 'offline' } : undefined) });
  wf.h.init(); await flush();
  wf.down = true; const n0 = wf.updates.length;
  for (let i = 0; i < 3; i++) { wf.clock += 40000; await wf.fire(); }
  check('no new snapshot while offline, none broken', wf.updates.length === n0 && wf.posted('error').length === 0);
  check('the rounds back off (6 s, 12 s … up to 30 s)', wf.live().length === 1 && wf.live()[0].ms > 3000 && wf.live()[0].ms <= 30000, wf.live().map(t => t.ms));
  wf.down = false; wf.clock += 40000; await wf.fire();
  check('back online: a snapshot again and the pace back to 3 s', wf.updates.length === n0 + 1 && wf.live()[0].ms === 3000, wf.live().map(t => t.ms));

  console.log('ready / error');
  const wo = world({ app: { v: 0 } });
  check('SFOfficeApp.v below the bridge → error app_too_old, no call, no ready', wo.h.init() === false);
  await flush();
  check('…', JSON.stringify(wo.posted('error')) === '[{"op":"error","why":"app_too_old"}]' && wo.posted('call').length === 0 && wo.posted('ready').length === 0 && typeof wo.win.SFOfficeApp.active === 'function', wo.sent);
  const woff = world({ answers: { office_tabs: () => ({ success: false, code: 'off' }) }, bridge: m => (m.body.method === 'office_tabs' ? { success: false, code: 'off' } : undefined) });
  woff.h.init(); await flush();
  check('the desktop says off → error off and the polling stops', JSON.stringify(woff.posted('error')) === '[{"op":"error","why":"off"}]' && woff.live().length === 0 && woff.posted('ready').length === 0, woff.sent.filter(m => m.op !== 'call'));
  const wn = world({ bridge: () => ({ success: false, code: 'timeout' }) });
  wn.h.init(); await flush();
  await wn.fire(30000);
  check('no snapshot within 30 s → error no_snapshot', JSON.stringify(wn.posted('error')) === '[{"op":"error","why":"no_snapshot"}]' && wn.posted('ready').length === 0, wn.sent.filter(m => m.op !== 'call'));
  const wl = world({ noOffice: true });
  wl.h.init(); await flush();
  const ncalls = wl.posted('call').length;
  check('office.html\'s script not there yet: no snapshot drawn, the desktop is not asked again and again', wl.posted('ready').length === 0 && wl.updates.length === 0 && ncalls <= 6, ncalls);
  wl.win.Office = { update: s => wl.updates.push(s) };
  await wl.fire(100);
  check('…and once Office.update exists the snapshot is drawn and ready sent', wl.updates.length === 1 && wl.posted('ready').length === 1);
  const ws = world({ updateThrows: true });
  ws.h.init(); await flush();
  check('Office.update throwing before the first drawing: nothing yet, the page gets the grace time', ws.posted('error').length === 0 && ws.posted('ready').length === 0);
  await ws.fire(5000);
  check('…not drawn within it → error script once, with the message', JSON.stringify(ws.posted('error')) === '[{"op":"error","why":"script","message":"boom","source":"","line":0,"col":0}]'
    && ws.posted('ready').length === 0, ws.sent.filter(m => m.op !== 'call'));
  ws.win.Office.update = s => ws.updates.push(s);
  await ws.fire(3000);
  check('…and a later drawing does not send ready after that error (the app was told the page is broken)', ws.posted('ready').length === 0);

  console.log('script errors on a drawn page (a stray error must not take the room away)');
  const we = world();
  we.h.init(); await flush();
  const onErr = ev => we.errors.find(e => e[0] === 'error')[1](ev);
  check('drawn: ready posted', we.posted('ready').length === 1);
  onErr({ message: 'ResizeObserver loop completed with undelivered notifications.', filename: 'sfoffice://office/office.html', lineno: 1, colno: 1 });
  check('WebKit\'s ResizeObserver loop warning is not passed on', we.posted('error').length === 0);
  onErr({ message: "TypeError: undefined is not an object (evaluating 'x.y')", filename: 'sfoffice://office/office.html', lineno: 3001, colno: 17 });
  check('another error after ready: posted with fatal:false, the message, the file and the line',
    JSON.stringify(we.posted('error')) === JSON.stringify([{ op: 'error', why: 'script', fatal: false, message: "TypeError: undefined is not an object (evaluating 'x.y')", source: 'office.html', line: 3001, col: 17 }]), we.posted('error'));
  onErr({ message: "TypeError: undefined is not an object (evaluating 'x.y')", filename: 'sfoffice://office/office.html', lineno: 3001, colno: 17 });
  for (let i = 0; i < 8; i++) onErr({ message: 'E' + i, filename: 'office.html', lineno: i });
  check('…the same message once; at most 5 different ones', we.posted('error').length === 5, we.posted('error').length);
  const callsBefore = we.calls().length;
  await we.fire(3000);
  check('…and the room goes on: still polling, no second ready', we.calls().length > callsBefore && we.posted('ready').length === 1);
  const wh = world({ noOffice: true });
  wh.h.init(); await flush();
  wh.errors.find(e => e[0] === 'error')[1]({ message: 'early', filename: 'office.html', lineno: 5, colno: 1 });
  wh.win.Office = { update: s => wh.updates.push(s) };
  await wh.fire(100);
  check('an error before the drawing that did not stop it: ready, then the error as fatal:false',
    wh.posted('ready').length === 1 && JSON.stringify(wh.posted('error')) === '[{"op":"error","why":"script","fatal":false,"message":"early","source":"office.html","line":5,"col":1}]', wh.sent.filter(m => m.op !== 'call'));
  check('…and its grace timer is gone (no late error)', !wh.live().some(t => t.ms === 5000));

  console.log('the page file: nothing without the app, the globals with it');
  const src = fs.readFileSync(W('office_app_host.js'), 'utf8');
  const bare = { console, document: {}, window: null };
  bare.window = bare; vm.createContext(bare); vm.runInContext(src, bare);
  check('without window.SFOfficeApp it does nothing (the desktop iframe)', bare.SFOfficeHost === undefined);
  const live = { console, SFOfficeApp: { v: 1, lang: 'en', uiScale: 1, idiom: 'phone', prefs: {}, pairedIds: [], caps: { editBot: false, openChat: true } }, setTimeout: () => 0, clearTimeout() {},
                 addEventListener() {}, document: { dispatchEvent() {} }, Event: class { constructor(t) { this.type = t; } }, Date, JSON, Promise };
  live.window = live; vm.createContext(live);
  for (const f of ['bots_kit.js', 'chat_rules.js', 'office_core.js', 'office_app_host.js']) vm.runInContext(fs.readFileSync(W(f), 'utf8'), live);
  check('with it, SFOfficeHost and SFOfficeApp.active are there before any call', typeof live.SFOfficeHost === 'object' && typeof live.SFOfficeApp.active === 'function'
    && live.SFOfficeHost.lang() === 'en' && live.SFOfficeHost.get('seats') === null);
  check('no webkit bridge: the page does not crash (calls fail as offline)', live.SFOfficeHost.board.canRequest() === false);

  console.log(`\n${pass} passed, ${fail} failed`);
  if (fail) process.exit(1);
  console.log('ALL PASS');
})().catch(e => { console.error(e); process.exit(1); });
