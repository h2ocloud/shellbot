/* The office's snapshot, as pure functions (no DOM, no network): what a page knows → what office.html's Office.update takes.
 *
 * Split out of web/office.js so the phone app's office (web/office_app_host.js, in a WKWebView with no main window) builds
 * its snapshot with the very same build() / track(): one mapping, two data sources (office.js: the main window's own
 * state; office_app_host.js: the desktop's /link/office/* answers). Runs in node: tests_office_snapshot.js (through
 * office.js, which re-exports it) and tests_office_app_host.js. The input of build() is described above it. */
(function (root) {
  'use strict';

  // ── pure: what the page knows → the office's snapshot ──────────────────────
  const DOT_STATE = { 'sig-working': 'working', 'sig-decision': 'decision', 'sig-stuck': 'stuck', 'sig-done': 'done' };
  // A tab's light (index.html's 400 ms loop) as the office's state. 'on attention' is the screen's
  // own "a menu is up" guess: the light shows it the way it shows a question.
  function tabState(dot) {
    const d = String(dot || '');
    if (DOT_STATE[d]) return DOT_STATE[d];
    if (/\battention\b/.test(d)) return 'decision';
    return 'idle';
  }
  // Another machine's bot or tab, from its GET /link/bots row (link_bots.py keeps it).
  function peerState(row) {
    const r = row || {};
    if (r.decision || r.activity === 'decision') return 'decision';
    if (r.blocked || r.activity === 'stuck') return 'stuck';
    if (r.activity === 'working' || r.activity === 'done') return r.activity;
    return 'idle';
  }
  const CLI_NAMES = { claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode', pi: 'pi', grok: 'Grok Build',
                      cursor: 'Cursor Agent', 'cursor-agent': 'Cursor Agent', agy: 'Antigravity', gemini: 'Gemini CLI' };
  // The program a tab's command runs, past `cd … &&`, `env`, `VAR=…` and a `sf-` launcher prefix.
  function cliName(cmd) {
    let words = String(cmd || '').trim();
    const amp = words.lastIndexOf('&&');
    if (amp >= 0) words = words.slice(amp + 2);
    const parts = words.trim().split(/\s+/).filter(w => w && w !== 'env' && w !== 'exec' && !/^[A-Za-z_][A-Za-z0-9_]*=/.test(w));
    const base = String(parts[0] || '').split(/[\\/]/).pop().replace(/\.(exe|cmd)$/i, '').toLowerCase().replace(/^sf-/, '');
    if (!base) return '';
    const key = Object.keys(CLI_NAMES).find(k => base === k || base.startsWith(k + '-'));
    return key ? CLI_NAMES[key] : base;
  }
  const ROSTER_EPOCH = Date.UTC(2026, 0, 1);
  const iso = ms => new Date(ms).toISOString();
  const clip = (s, n) => { const t = String(s || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
  // A face as the office draws it: a shape it knows and a colour as #rrggbb (bots.js's palette keys
  // differ from the prototype's, which would turn every unknown name blue).
  function avatar(av, seed, kit) {
    const k = kit || {};
    const d = k.defaultAvatar ? k.defaultAvatar(seed) : { shape: 'round', color: 'gray' };
    const shape = av && k.shapes && k.shapes.indexOf(av.shape) >= 0 ? av.shape : (av && av.shape && !k.shapes ? av.shape : d.shape);
    const want = av && av.color ? av.color : d.color;
    const hex = c => (/^#[0-9a-f]{6}$/i.test(c || '') ? c : (k.palette || {})[c] || '');
    return { shape, color: hex(want) || hex(d.color) || '#8b90a0' };
  }
  const STATUS_RANK = { doing: 0, waiting: 1, todo: 2 };
  // The open task a person owns: what is being done before what waits, before what is to do; the
  // most recently touched first. `stale` is the store's own verdict (sfctl task stuck).
  function taskFor(owners, tasks, known) {
    const open = ((tasks && tasks.open) || []).filter(x => x && owners.indexOf(String(x.owner || '')) >= 0);
    if (!open.length) return null;
    open.sort((a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) || (b.updated_at || 0) - (a.updated_at || 0));
    const x = open[0];
    const stuck = new Set(((tasks && tasks.stuck) || []).map(s => s && s.id));
    const ref = who => (who === 'user' || known.has(who) ? who : '');
    const waiting = String(x.waiting_on || '');
    const from = String(x.requester || '');
    return { id: '#' + x.id, title: String(x.title || ''), status: x.status, next: String(x.next_step || ''),
             waiting_on: waiting === 'user' || known.has(waiting) ? waiting : (waiting || null),
             from: from && owners.indexOf(from) < 0 ? (ref(from) || null) : null,
             updated_at: iso(Number(x.updated_at || 0) * 1000), stale: stuck.has(x.id) };
  }
  // A loop or cron routine a tab runs, as agent_status.detect_schedules leaves it on the tab's status
  // (`loop`: kind loop|cron, status, next_ts in seconds, delay in seconds, reason, rounds, crons). null for
  // none, and for a /loop whose next wake passed more than LOOP_GRACE_S ago: the status behind it can be
  // up to 25 s old, so the page does not trust "sleeping" forever.
  const LOOP_GRACE_S = 1800;
  function loopOf(raw, now) {
    const r = raw && typeof raw === 'object' ? raw : null;
    if (!r || (r.kind !== 'loop' && r.kind !== 'cron')) return null;
    const next = Number(r.next_ts) > 0 ? Number(r.next_ts) : 0;
    if (r.kind === 'loop' && (!next || next + LOOP_GRACE_S < (now || Date.now()) / 1000)) return null;
    const crons = (Array.isArray(r.crons) ? r.crons : []).slice(0, 5).map(c => ({ desc: clip(c && c.desc, 60), schedule: clip(c && c.schedule, 40) }));
    return { kind: r.kind, status: r.status === 'running' ? 'running' : r.kind === 'cron' ? 'scheduled' : 'sleeping', next_ts: next,
             delay: Number(r.delay) > 0 ? Number(r.delay) : 0, reason: clip(r.reason, 90), rounds: Number(r.rounds) || 0, crons };
  }
  // A person's name for a task's owner / waiting-on / requester → the office's key ('user', 'b:<role>', 't:<sid>'),
  // by role, by the bot's label, or by the title or sid of a tab that is no bot. '' when nobody here has that name.
  function personKey(name, who) {
    const n = String(name || '');
    if (!n) return '';
    if (n === 'user') return 'user';
    if (who.roles.has(n)) return 'b:' + n;
    if (who.labels[n]) return 'b:' + who.labels[n];
    return who.temps[n] ? 't:' + who.temps[n] : '';
  }
  const TASK_KEYS = ['id', 'title', 'status', 'kind', 'owner', 'requester', 'waiting_on', 'next_step', 'due_at', 'updated_at', 'idle_s'];
  // The task board: the person's decisions (kind decision, owned by `user`), the stuck ones, then every other open
  // task. Each task is in ONE list, the first that has it (decision, stuck, open), so a stuck decision is not three
  // rows; `all` is that order and is what the sticky notes follow. A stuck row from an older overview with only
  // {id, idle_s} borrows its text from the open list. Times stay in seconds (the store's), as `age` needs them.
  function boardOf(tasks, who, now) {
    const T = tasks || {}, W = who || { roles: new Set(), labels: {}, temps: {} };
    const byId = new Map(((T.open) || []).filter(x => x && x.id != null).map(x => [x.id, x]));
    const seen = new Set();
    const row = (x, extra) => {
      const o = {};
      for (const k of TASK_KEYS) o[k] = x[k] == null ? '' : x[k];
      o.id = Number(x.id); o.title = clip(x.title, 200); o.next_step = clip(x.next_step, 200);
      o.due_at = Number(x.due_at) || 0; o.updated_at = Number(x.updated_at) || 0; o.idle_s = Number(x.idle_s) || 0;
      o.owner_key = personKey(x.owner, W); o.waiting_key = personKey(x.waiting_on, W); o.requester_key = personKey(x.requester, W);
      return Object.assign(o, extra);
    };
    const take = (list, extra) => list.filter(x => x && x.id != null && !seen.has(x.id)).map(x => { seen.add(x.id); return row(Object.assign({}, byId.get(x.id) || {}, x), extra); });
    const decisions = take(T.decisions || [], { decision: true });
    const stuck = take(T.stuck || [], { stuck: true });
    const open = take(T.open || [], {});
    return { decisions, stuck, open, all: decisions.concat(stuck, open) };
  }
  // The computer's own schedules (api_schedules.schedules_list) as the office lists them: a title, when, on or off, and
  // what the calendar needs to place and change them: `slots` (sched_view's numbers: an interval, calendar entries; bounded),
  // `owner` (the bot that looks after it, from schedules.json), `toggle` (false: no on/off switch for this one). The
  // commands are left out: the office shows when something runs, not what.
  function slotsOf(s) {
    const o = s && typeof s === 'object' ? s : {}, iv = Number(o.interval);
    const cal = (Array.isArray(o.calendar) ? o.calendar : []).slice(0, 200).map(c => {
      const r = {};
      for (const k of ['Month', 'Day', 'Weekday', 'Hour', 'Minute']) { const v = c && c[k]; if (Number.isInteger(v) && v >= 0 && v <= 366) r[k] = v; }
      return r;
    });
    return { interval: iv > 0 ? Math.min(iv, 31536000) : null, calendar: cal, partial: !!o.partial };
  }
  function schedShape(raw) {
    const r = raw && typeof raw === 'object' ? raw : {};
    const rows = (Array.isArray(r.schedules) ? r.schedules : []).map(x => ({
      id: String(x.id || ''), title: String(x.title || x.id || ''), when: x.when && typeof x.when === 'object' ? { en: String(x.when.en || ''), zh: String(x.when.zh || '') } : { en: String(x.when || ''), zh: String(x.when || '') },
      enabled: !!x.enabled, running: !!x.running, kind: String(x.kind || ''), count: Array.isArray(x.members) ? x.members.length : 0,
      owner: clip(x.owner, 60), toggle: x.toggle !== false, state: clip(x.state, 12), slots: slotsOf(x.slots) }));
    return { ok: !r.error, unsupported: !!r.unsupported, configured: r.configured !== false, launchd_ok: r.launchd_ok !== false, rows, at: Date.now() };
  }
  function runsOf(e) {
    const pin = e.pin || {};
    return [(e.cli && e.cli.label) || '', pin.model ? (pin.label || pin.model) : 'CLI 預設'].filter(Boolean).join(' · ');
  }

  // A paired computer's task board (link_peer_tasks: its decisions, stuck and open tasks, rows shaped like the local ones) as
  // the office's board: the same lists boardOf makes, owners as that computer's people ('p:<pid>/<role>' for a bot,
  // 'q:<pid>/<tab>' for an intern; 'user' is whoever sits at that computer), plus `boardState` (ok | pending | old | denied |
  // error) and `boardAt` (seconds; when the rows were last read fresh). Nothing for a computer the call did not answer for.
  const PEER_STATES = ['ok', 'pending', 'old', 'denied', 'error'];
  function peerBoardOf(pid, p, pt, now) {
    if (!pt || typeof pt !== 'object') return {};
    const who = { roles: new Set(), labels: {}, temps: {} };
    for (const b of p.bots || []) if (b && b.role && b.state !== 'external') { who.roles.add(String(b.role)); if (b.label) who.labels[String(b.label)] = String(b.role); }
    for (const b of p.bots || []) if (b && b.role && b.state !== 'external') formerNames(who, b.former_names, String(b.role));
    for (const r of Object.keys(p.tabs || {})) { const tb = p.tabs[r]; if (tb && tb.kind === 'temp') { who.temps[String(tb.name || r)] = r; who.temps[r] = r; } }
    const b = boardOf({ open: pt.open, stuck: pt.stuck, decisions: pt.decisions }, who, now);
    const key = k => (k.startsWith('b:') ? `p:${pid}/${k.slice(2)}` : k.startsWith('t:') ? `q:${pid}/${k.slice(2)}` : k);
    for (const r of b.all) { r.owner_key = key(r.owner_key); r.waiting_key = key(r.waiting_key); r.requester_key = key(r.requester_key); }
    return { board: b, boardState: PEER_STATES.indexOf(pt.state) >= 0 ? pt.state : 'error', boardAt: Number(pt.at) > 0 ? Number(pt.at) : 0 };
  }

  /* input:
       botsOn    bots are on (settings.experimental_bots); off = every AI tab is an intern
       roster    bots_state().roster: role → {label, responsibility, bot: {avatar, primary, memory, external}, cli, pin}
       primary   the primary bot's role
       tabs      this computer's tabs in sidebar order: [{sid, label, kind: bot|temp|terminal, role, dot, cmd, line, loop}]
                 (loop: the tab's status `loop`, a /loop or cron routine it runs, or null)
       tasks     tasks_overview(): {decisions: [...], open: [...], stuck: [...]}
       peers     SFBots.peerBots(): pid → {name, primary, bots: [/link/bots rows], tabs: {rsid: tab}}
       peerOps    pid → what refreshPeerOps kept of ops_peer ({state, level, reason, since, muted_until, updated, face, at}); a peer listed there carries `ops`
       peerTasks link_peer_tasks's `peers`: pid → {state, at, decisions, stuck, open}; a peer listed there carries board, boardState, boardAt
       helpers   office_helpers().helpers: sid → [{id, description, model, agent_type, state, started_at, last_at}]
       seen      sid → first time this page saw the tab (ms); filled in for new ones (an intern's opened_at)
       kit       {palette, shapes, defaultAvatar} (SFBots)
       now       ms
     output: the snapshot office.html's Office.update takes (its §15; every bot / intern carries `tab`: it has a tab to open, here or on the paired
     computer; the sid itself is left out), with `board` (the task board: decisions, stuck,
     open, all; boardOf) and `loops` (every tab with a loop or cron, who it is and when it wakes next); a bot or an
     intern with one carries `loop` too. */
  // A renamed bot is still who a task names by its old name (bots.rename_in_config keeps them in former_names, and the store keeps
  // the name a task was written with): an old name means that bot unless it is another bot's role or label now.
  function formerNames(who, names, role) {
    for (const n of Array.isArray(names) ? names : []) { const k = String(n || ''); if (k && !who.roles.has(k) && !who.labels[k]) who.labels[k] = role; }
  }
  // Who a task's owner / requester name can mean: the bots' roles, labels and former names, and the tabs that are no bot (build() and the page's rows).
  function whoOf(roles, roster, tabs) {
    const known = new Set(roles), who = { roles: known, labels: {}, temps: {} };
    for (const r of roles) if (roster[r].label) who.labels[String(roster[r].label)] = r;
    for (const r of roles) formerNames(who, roster[r].former_names, r);
    for (const x of tabs) if (x.kind === 'temp' && !(x.role && known.has(x.role))) { who.temps[String(x.label || '')] = x.sid; who.temps[x.sid] = x.sid; }
    return who;
  }
  function build(inp) {
    const I = inp || {}, now = I.now || Date.now(), seen = I.seen || {}, kit = I.kit || {};
    const roster = I.botsOn ? (I.roster || {}) : {};
    const roles = Object.keys(roster).filter(r => roster[r] && roster[r].bot && !roster[r].bot.external);
    const known = new Set(roles);
    const tabs = (I.tabs || []).map(x => Object.assign({}, x, I.botsOn ? {} : { role: '', kind: x.kind === 'bot' ? 'temp' : x.kind }));
    const tabOf = {};
    for (const x of tabs) if (x.role && known.has(x.role) && !tabOf[x.role]) tabOf[x.role] = x;
    const order = Object.keys(roster);
    const bots = roles.map(role => {
      const e = roster[role], b = e.bot || {}, tab = tabOf[role];
      return { id: role, name: String(e.label || role), avatar: avatar(b.avatar, role, kit), primary: role === I.primary,
               duty: String(e.responsibility || ''), created_at: iso(ROSTER_EPOCH + order.indexOf(role) * 60000),
               state: tab ? tabState(tab.dot) : 'asleep', tab: !!tab, task: taskFor([role], I.tasks, known),
               runs: runsOf(e), memory: b.memory !== false, cli: String((e.cli && e.cli.label) || cliName(e.cmd) || ''),
               doing: tab ? clip(tab.line, 120) : '', sid: tab ? tab.sid : '', line: tab ? clip(tab.line, 120) : '', said: tab ? clip(tab.said, 120) : '' };
    });
    let k = 0;
    const temps = tabs.filter(x => x.kind === 'temp' && !(x.role && known.has(x.role))).map(x => {
      if (!seen[x.sid]) seen[x.sid] = now + (k++);
      return { id: x.sid, name: String(x.label || x.sid), cli: cliName(x.cmd) || '?', state: tabState(x.dot), tab: true,
               task: taskFor([String(x.label || ''), x.sid].filter(Boolean), I.tasks, known),
               opened_at: iso(seen[x.sid]), doing: clip(x.line, 120), line: clip(x.line, 120), said: clip(x.said, 120) };
    });
    for (const sid of Object.keys(seen)) if (!tabs.some(x => x.sid === sid)) delete seen[sid];
    const peers = Object.keys(I.peers || {}).map(pid => {
      const p = I.peers[pid] || {};
      return Object.assign({
        id: pid, name: String(p.name || pid.slice(0, 8)),
        bots: (p.bots || []).filter(b => b && b.role && b.state !== 'external').map((b, i) => ({
          id: String(b.role), name: String(b.label || b.role), avatar: avatar(b.avatar, `${p.name || ''}/${b.role}`, kit),
          primary: !!b.primary, duty: String(b.responsibility || ''), created_at: iso(ROSTER_EPOCH + i * 60000),
          state: b.state === 'asleep' ? 'asleep' : peerState(b), tab: !!b.sid && b.state !== 'asleep', runs: String(b.runs_on || ''), memory: b.memory !== false,
          doing: b.state === 'asleep' ? '' : clip(b.latest, 120) })),
        temps: Object.keys(p.tabs || {}).filter(r => (p.tabs[r] || {}).kind === 'temp').map((r, i) => {
          const tb = p.tabs[r];
          return { id: r, name: String(tb.name || r), cli: CLI_NAMES[tb.cli] || String(tb.cli || '?'), state: peerState(tb), tab: true,
                   doing: clip(tb.latest, 120), opened_at: iso(ROSTER_EPOCH + i * 1000) };
        }),
      }, peerBoardOf(pid, p, (I.peerTasks || {})[pid], now), (I.peerOps || {})[pid] ? { ops: I.peerOps[pid] } : {});
    });
    const helpers = [];
    const parentOf = {};
    for (const b of bots) if (b.sid) parentOf[b.sid] = { kind: 'bot', id: b.id, name: b.name };
    for (const x of temps) parentOf[x.id] = { kind: 'temp', id: x.id, name: x.name };
    for (const sid of Object.keys(I.helpers || {})) {
      const par = parentOf[sid];
      if (!par) continue;                          // a helper of a tab the office does not show
      for (const h of I.helpers[sid] || []) {
        if (!h || !h.id) continue;
        helpers.push({ id: `${sid}/${h.id}`, parent: par.id, parent_kind: par.kind, parent_name: par.name,
                       description: String(h.description || ''), model: String(h.model || ''), agent_type: String(h.agent_type || ''),
                       state: h.state === 'stuck' || h.state === 'working' ? h.state : 'idle',
                       started_at: iso(Number(h.started_at || 0) * 1000), last_at: iso(Number(h.last_at || 0) * 1000) });
      }
    }
    const loops = [];
    const loopOn = (tab, key, name) => {
      const lp = loopOf(tab && tab.loop, now);
      if (lp) loops.push(Object.assign({ key, name }, lp));
      return lp;
    };
    for (const b of bots) { const tab = tabOf[b.id]; if (tab) b.loop = loopOn(tab, 'b:' + b.id, b.name); }
    for (const x of temps) { const tab = tabs.find(q => q.sid === x.id); x.loop = loopOn(tab, 't:' + x.id, x.name); }
    for (const tab of tabs) if (tab.loop && !tab.role && tab.kind !== 'temp') loopOn(tab, '', String(tab.label || tab.sid));   // a tab no one sits at
    const who = whoOf(roles, roster, tabs);
    for (const b of bots) { delete b.sid; }
    return { bots, temps, peers, helpers, handoffs: [], board: boardOf(I.tasks, who, now), loops };
  }

  // What happened, as the office's panel lists it: each person's state changes this page has seen
  // (kept while the app runs; nothing is read to make them). mem: key → {st, events}.
  const EV_KIND = { working: 'work', decision: 'decision', stuck: 'stuck', done: 'done', idle: 'idle', asleep: 'sleep' };
  const EV_WORDS = { working: '在忙', decision: '等你決定', stuck: '卡住了', done: '做完了', idle: '閒下來', asleep: '休息（沒有開分頁）' };
  function track(mem, snap, now) {
    const alive = new Set();
    const one = (key, p, first) => {
      alive.add(key);
      const m = mem[key] || (mem[key] = { st: null, events: [] });
      if (m.st !== p.state) {
        // 做完了 says what it did (its own last words), 閒下來 and 休息 say nothing more; the rest say what it is on
        const said = p.state === 'done' ? p.said : p.state === 'idle' || p.state === 'asleep' ? '' : p.line;
        const kind = m.st == null ? 'arrive' : EV_KIND[p.state] || 'note';
        const text = (m.st == null ? first : EV_WORDS[p.state] || p.state) + (m.st == null || !said ? '' : '：' + said);
        // woken again and again (its own background work ending, a worker reporting): done / idle / done… within a minute.
        // The same event as one of the last few in the same minute is that line again: counted (×N) and moved to the end.
        const same = m.st == null ? null : m.events.slice(-4).reverse().find(v => v.kind === kind && v.text === text && Math.floor(v.t / 60000) === Math.floor(now / 60000));
        if (same) { m.events.splice(m.events.indexOf(same), 1); same.n = (same.n || 1) + 1; same.t = now; m.events.push(same); }
        else m.events.push({ t: now, kind, text });
        if (m.events.length > 30) m.events.splice(0, m.events.length - 30);
        m.st = p.state;
      }
      p.events = m.events.slice();
    };
    for (const b of snap.bots || []) one('b:' + b.id, b, '在辦公室看到他：' + (EV_WORDS[b.state] || b.state));
    for (const x of snap.temps || []) one('t:' + x.id, x, '開了這個分頁（實習生）：' + (EV_WORDS[x.state] || x.state));
    for (const h of snap.helpers || []) one('h:' + h.id, h, `${h.parent_name} 派出去：${clip(h.description, 60)}`);
    for (const key of Object.keys(mem)) if (!alive.has(key)) delete mem[key];
    for (const b of snap.bots || []) { delete b.line; delete b.said; }
    for (const x of snap.temps || []) { delete x.line; delete x.said; }
    return snap;
  }

  // ── pure: one person's conversation, for the office's panel ─────────────────
  // r is chat_view's answer for the tab (the chat view's own call: the phone's `conversation` turns and
  // `_link_decision`), R is web/chat.js's rules (SFChat.rules; pure, and what node's require gives).
  // Agent text reaches HTML only through R.md / R.escHtml, as in the chat view.
  const CHAT_LIMIT = 100, CHAT_SHOW = 30;       // turns asked for (tool calls are turns too); items drawn
  const baseName = p => String(p || '').split(/[\\/]/).pop();
  // Messages of the person's still waiting to go in, or given up, in plain words (chat_view's `queue`).
  function queueWords(q, name, zh, now) {
    const T = (en, cn) => (zh ? cn : en), out = [];
    const waiting = ((q && q.queued) || []).length, gone = (q && q.given_up) || [];
    if (waiting) {
      const b = q.busy || {}, mins = b.since ? Math.floor((now / 1000 - b.since) / 60) : 0;
      const why = {
        working: T(`${name} is still on the previous step${mins >= 1 ? ` (${mins} min so far)` : ''}`, `${name}還在做上一步${mins >= 1 ? `（已經 ${mins} 分鐘）` : ''}`),
        telegram: T('a message from Telegram is still being handled', '還在處理從 Telegram 來的那一則'),
        decision: T('it is waiting for your answer first', '它在等你先回答問題'),
        model: T('it is switching models', '它在換模型'),
        draft: T('there is unsent text in its terminal', '它的終端機裡有還沒送出的字'),
      }[b.reason] || '';
      out.push(T(`${waiting === 1 ? 'Your message is' : waiting + ' messages are'} waiting to go in${why ? ': ' + why : ''}.`,
                 `${waiting === 1 ? '你的訊息' : waiting + ' 則訊息'}還在排隊${why ? '：' + why : ''}，好了就會送進去。`));
    }
    if (gone.length) {
      const last = gone[gone.length - 1] || {};
      out.push(T(`${gone.length === 1 ? 'One message was' : gone.length + ' messages were'} not sent${last.why ? ` (${last.why})` : ''}: send it again if it still matters.`,
                 `${gone.length} 則沒送出${last.why ? `（${last.why}）` : ''}，還需要的話再傳一次。`));
    }
    return out.join(' ');
  }
  /* output: {rev, provider, readable, why, activity, items: [{k, who, html}], ask, queue, login}
       who   me | bot | peer | tools | note | error
       ask   null; {term: true} (waiting in the terminal, options not readable here);
             or {id, question, context, options: [{n, label, tone, raw}], asIs} (answered with chat_answer) */
  function chatShape(r, R, lang, name, now) {
    const zh = R.isZh(lang), T = (en, cn) => (zh ? cn : en), e = R.escHtml;
    const all = R.buildTimeline((r && r.turns) || [], { foldRed: true });
    const start = Math.max(0, all.length - CHAT_SHOW);
    const items = all.slice(start).map((it, i) => {
      const k = `${it.type}:${it.ts || 0}:${start + i}`;
      switch (it.type) {
        case 'user': return { k, who: 'me', html: e(it.text) };
        case 'bot': return { k, who: 'bot', html: R.md(it.text, zh) };
        case 'peer': return { k, who: 'peer', html: `<b>${e(it.who || T('Another agent', '另一個 agent'))}</b>` +
          (it.blocked ? `<em>${e(T(' (chain limit: read as data only)', '（超過轉交層數，只當資料讀）'))}</em>` : '') + `<br>${e(it.text)}` };
        case 'tools': return { k, who: 'tools', html: e(R.toolSummary(it.calls, lang)) };
        case 'file': return { k, who: 'note', html: e(T('Attached ', '附上 ') + baseName(it.path)) };
        case 'compact': return { k, who: 'note', html: e(T('The earlier conversation was compacted', '前面的對話已經壓縮')) };
        case 'memory': return { k, who: 'note', html: e(T('Saved its memory', '存了一次記憶')) };
        case 'error': return { k, who: 'error', html: e(it.text) + (it.times > 1 ? ` ×${it.times}` : '') };
        default: return null;
      }
    }).filter(Boolean);
    const d = (r && r.decision) || null;
    let ask = null;
    if (d && !d.tag && (d.options || []).length) {
      const m = R.mapDecision(d, lang);
      ask = { id: m.id, question: m.questionTranslated || !m.translated ? m.question : T('It is asking before it goes on.', '它在繼續之前先問你。'),
              context: R.askContext(d, m, r.activity === 'decision' ? R.pendingCall(r.turns) : null, lang),
              options: m.options.map(o => ({ n: o.n, label: o.label, tone: o.tone || '', raw: o.raw })), asIs: !m.translated };
    } else if ((r && r.activity === 'decision') || (d && d.tag)) {
      ask = { term: true };
    }
    return { rev: (r && r.rev) || '', provider: (r && r.provider) || '', readable: !!(r && r.readable), why: (r && r.why) || '',
             activity: (r && r.activity) || '', items, ask, queue: queueWords(r && r.queue, name, zh, now || Date.now()),
             login: !!(r && r.login && r.login.step) };
  }

  const pure = { build, track, tabState, peerState, cliName, avatar, chatShape, queueWords, CHAT_LIMIT, loopOf, boardOf, personKey, schedShape, whoOf };
  if (typeof module === 'object' && module.exports) module.exports = pure;
  root.SFOfficeCore = pure;
})(typeof window !== 'undefined' ? window : globalThis);
