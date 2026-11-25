/* The office in the phone app (ShellBot's iPad / iPhone app: a WKWebView over sfoffice://office/office.html).
 *
 * office.html runs here unchanged and finds `window.SFOfficeHost` (the same object web/office.js hands it on the desktop,
 * in an iframe). This file makes that object where there is no main window and no pywebview: the data comes from the
 * desktop over the app's bridge, the snapshot is built by the very same build() / track() (web/office_core.js), and it is
 * READ ONLY: nothing here sends text to a bot, answers a decision, moves a seat or writes office.json. A person who wants
 * to talk to a bot is handed to the app (the native action `openChat`).
 *
 * It does nothing unless the app injected `window.SFOfficeApp` before the page's scripts ({v, lang, uiScale, idiom, prefs,
 * pairedIds, caps:{editBot, openChat}}), so the desktop's iframe, which loads the same office.html, is not touched.
 *
 * ── the app's bridge: window.webkit.messageHandlers.sfoffice.postMessage(msg) ────────────────────────────────────
 *   {op:'call', method:'POST', path:'/link/office/api', body:{method, args, lang}}  → a Promise of the signed JSON answer
 *        {success:true, result}  (result: what the desktop's js_api method answers; an object, or JSON text)
 *        {success:false, code}   code: offline | timeout | bad_signature | not_allowed | http (+ status) from the app;
 *                                off | no_method | bad_args | readonly | 403 … from the desktop. The Promise never rejects.
 *      The app lets only paths under /link/office/ through; the desktop decides which `method` it answers (a whitelist).
 *   {op:'native', name, args}   fire and forget, the app does it:
 *        name 'openChat'  args {key, sid, peer, rsid}  that person's tab: sid for this computer's (key b:… / t:…), peer (the
 *                                                       other computer's frame id) and rsid for p:… / q:…
 *        name 'editBot'   args {role}                   that bot's settings editor
 *        name 'openUrl'   args {url}                    an http(s) address, in the browser
 *        name 'close'     args {}                       leave the office
 *   {op:'ready', v}      the first snapshot was drawn
 *   {op:'error', why}    why: app_too_old (SFOfficeApp.v < BRIDGE) | no_snapshot (none after 30 s) | script | off (the office is
 *                        switched off on the desktop)
 * And the page gives the app `SFOfficeApp.active(bool)`: false stops the polling (background), true polls at once again.
 *
 * ── what it asks the desktop (all POST /link/office/api; lang = SFOfficeApp.lang) ───────────────────────────────────
 *   every 3 s     office_tabs, link_peer_bots (+ bots_state at the start and every 30 s)
 *   every 9 s     tasks_overview, link_peer_tasks
 *   every 30 s    office_objects
 *   on demand     office_sky (host.sky(): the desktop's place and weather for the themes' windows; only when the page calls
 *                 it, never from the rounds above; answered from the last good answer for 10 min, a failed ask is retried
 *                 after 1 min), task_detail, schedules_list, office_object_list, office_object_item, office_ops_view;
 *                 office_ops_live (the ops window's 60 s "I am looking" heartbeat, and false when the app goes to the background)
 * One round at a time; a failure keeps the last answer (offline / timeout back the rounds off, up to 30 s).
 *
 * ── the web/*.js modules it brings, instead of the main window's globals ───────────────────────────────────────────
 *   SFOfficeCore  build / track / schedShape …        (office_core.js, pure)
 *   SFChatRules   md, preview                         (chat_rules.js, pure: the whole rules object, not the chat screen)
 *   SFBotsKit     PALETTE, SHAPES, defaultAvatar      (bots_kit.js, pure: the faces' parts, not bots.js's sidebar)
 *
 * Runs in node too: create(env) takes the window, the timers and the clock (tests_office_app_host.js). */
(function (root) {
  'use strict';

  const BRIDGE = 1;                         // the bridge version this file speaks (/link/ping office.bridge)
  const TICK_MS = 3000, TASKS_MS = 9000, OBJECTS_MS = 30000, BOTS_MS = 30000, BACKOFF_MAX = 30000, FIRST_MS = 30000, SCRIPT_GRACE_MS = 5000, SKY_MS = 600000, SKY_RETRY_MS = 60000;
  const SHARED_KEYS = ['seats', 'items', 'groups', 'arrange'];   // this computer's own, read only here
  const clone = v => (v == null ? v : JSON.parse(JSON.stringify(v)));
  const isObj = v => !!v && typeof v === 'object' && !Array.isArray(v);

  function create(env) {
    const win = env.window, core = env.core, rules = env.rules, kit = env.kit;
    const now = env.now || (() => Date.now());
    const setT = env.setTimeout || ((f, ms) => setTimeout(f, ms)), clearT = env.clearTimeout || (id => clearTimeout(id));
    const warn = env.warn || (() => {});
    const app = () => win.SFOfficeApp || {};
    const sets = [];                        // a store.set on a shared key (a control that should have been hidden), for the tests

    // ── the bridge ────────────────────────────────────────────────────────────
    const channel = () => { try { return win.webkit.messageHandlers.sfoffice; } catch (_) { return null; } };
    function post(msg) {
      const c = channel();
      if (!c) return Promise.resolve({ success: false, code: 'offline' });
      try { return Promise.resolve(c.postMessage(msg)).then(r => r, () => ({ success: false, code: 'offline' })); }
      catch (_) { return Promise.resolve({ success: false, code: 'offline' }); }
    }
    const native = (name, args) => { post({ op: 'native', name, args: args || {} }); };
    const lang = () => (/^zh/i.test(String(app().lang || '')) ? 'zh' : 'en');
    const isZh = () => lang() === 'zh';
    const parse = v => { if (typeof v !== 'string') return v; try { return JSON.parse(v); } catch (_) { return null; } };
    // The desktop's js_api method `method`: {ok, data} or {ok: false, code, status}. Never throws.
    async function api(method, args) {
      let r = null;
      try { r = parse(await post({ op: 'call', method: 'POST', path: '/link/office/api', body: { method, args: args || [], lang: lang() } })); }
      catch (_) { r = null; }
      if (!isObj(r)) return { ok: false, code: 'offline' };
      if (r.success === false) return { ok: false, code: String(r.code || 'error'), status: r.status };
      const data = 'result' in r ? parse(r.result) : r;
      if (!isObj(data)) return { ok: false, code: 'bad_answer' };
      if (data.success === false && data.code === 'off') return { ok: false, code: 'off' };
      return { ok: true, data };
    }

    // ── what the polling keeps ────────────────────────────────────────────────
    let botsState = { enabled: false, roster: {}, primary: '' }, tabsAns = null, peers = {}, peerTasks = {};
    let tasks = { open: [], stuck: [], decisions: [] }, objs = [], deliv = null;
    let botsAt = 0, tasksAt = 0, objsAt = 0, failures = 0;
    const seen = {}, mem = {};
    let lastSnap = null, keyTab = {};         // the last snapshot drawn; key → {sid} for the people of this computer
    let timer = 0, readyTimer = 0, running = false, busy = false, wasLive = '', ready = false, failed = false;

    // ── the same four sync answers office.html asks at its top level ──────────
    const prefsNow = () => { const p = app().prefs; return isObj(p && p.prefs) ? p.prefs : isObj(p) ? p : {}; };
    const local = {};                         // the display keys this page changes for itself (zoning, mode, legendMin, panelTab, calMode)
    const store = {
      get: k => (Object.prototype.hasOwnProperty.call(local, k) ? clone(local[k]) : Object.prototype.hasOwnProperty.call(prefsNow(), k) ? clone(prefsNow()[k]) : null),
      set(k, v) {
        if (SHARED_KEYS.indexOf(k) >= 0) { sets.push(k); warn('office app: set(' + k + ') ignored: read only'); return; }
        if (v == null) delete local[k]; else local[k] = clone(v);
      },
    };

    // ── people ────────────────────────────────────────────────────────────────
    const paired = pid => Array.isArray(app().pairedIds) && app().pairedIds.indexOf(pid) >= 0;
    // 'b:<role>' / 't:<sid>' → {sid}; 'p:<pid>/<role>' / 'q:<pid>/<rsid>' → {peer, rsid}; null for one with no tab, or not ours to open
    function targetOf(key) {
      const k = String(key || ''), snap = lastSnap;
      if (!snap) return null;
      if (k.startsWith('b:') || k.startsWith('t:')) return snap && keyTab[k] ? { sid: keyTab[k] } : null;
      const m = /^([pq]):([^/]+)\/(.+)$/.exec(k);
      if (!m || !paired(m[2])) return null;
      const p = peers[m[2]];
      if (!p) return null;
      if (m[1] === 'q') return (p.tabs || {})[m[3]] ? { peer: m[2], rsid: m[3] } : null;
      const b = (p.bots || []).find(x => x && String(x.role) === m[3]);
      return b && b.sid && b.state !== 'asleep' ? { peer: m[2], rsid: String(b.sid) } : null;
    }
    // the last snapshot's word: does this person have a tab (the build() `tab` flag; a bot asleep has none)
    function has(key) {
      const k = String(key || ''), snap = lastSnap;
      if (!snap || !(app().caps && app().caps.openChat)) return false;
      if (k.startsWith('b:')) return !!(snap.bots || []).find(b => 'b:' + b.id === k && b.tab);
      if (k.startsWith('t:')) return !!(snap.temps || []).find(x => 't:' + x.id === k);
      const m = /^([pq]):([^/]+)\/(.+)$/.exec(k);
      if (!m || !paired(m[2])) return false;
      const p = (snap.peers || []).find(x => x.id === m[2]);
      if (!p) return false;
      return m[1] === 'q' ? !!(p.temps || []).find(x => x.id === m[3]) : !!(p.bots || []).find(b => b.id === m[3] && b.tab);
    }
    const chat = {
      has,
      open(key) {
        const tg = has(key) && targetOf(key);
        if (!tg) return false;
        native('openChat', { key: String(key), sid: tg.sid || '', peer: tg.peer || '', rsid: tg.rsid || '' });
        return true;
      },
      openUrl(url) { if (/^https?:\/\//i.test(String(url || ''))) native('openUrl', { url: String(url) }); },
    };

    // ── the board and the calendar: read only ─────────────────────────────────
    const board = {
      async detail(id) {
        const r = await api('task_detail', [/^\d+$/.test(String(id)) ? Number(id) : String(id)]);
        return r.ok ? r.data : { success: false, code: r.code };
      },
      async schedules() {
        const r = await api('schedules_list');
        return r.ok ? core.schedShape(r.data) : { ok: false, unsupported: false, configured: true, launchd_ok: true, rows: [], at: now() };
      },
      canRequest: () => false,
      takeFocus: () => null,
    };

    // ── the objects (bookshelf, personnel cabinet, deliverables, the ops board) ───
    // item.actions → only the one that asks the desktop for nothing: 編輯執掌 (the personnel cabinet) hands the bot to the app's editor.
    const OWN_ACTION = { personnel: ['edit_duty'] };
    const roleOfItem = iid => { const m = /^bot:(.+)$/.exec(String(iid || '')); return m ? m[1] : ''; };
    let liveId = '';
    const objects = {
      async list(id, q) {
        const r = await api('office_object_list', [String(id), JSON.stringify(q || {})]);
        return r.ok ? r.data : { success: false, code: r.code };
      },
      async item(id, iid) {
        const r = await api('office_object_item', [String(id), String(iid)]);
        if (!r.ok) return { success: false, code: r.code };
        const d = r.data;
        if (d && d.item) d.item.actions = (d.item.actions || []).filter(a => (OWN_ACTION[id] || []).indexOf(a) >= 0);
        return d;
      },
      async action(id, iid, aid) {
        const role = roleOfItem(iid);
        if (id === 'personnel' && aid === 'edit_duty' && role && botsState.roster[role]) return { success: true, ok: true, go: { settings: role } };
        return { success: false, ok: false, readonly: true };
      },
      async view(id) {
        const r = await api('office_ops_view', ['']);
        return r.ok ? r.data : { success: false, code: r.code };
      },
      async live(id, on) {
        liveId = on ? String(id) : '';
        if (!running && on) return { success: true };            // in the background: asked again when the app comes back
        const r = await api('office_ops_live', [!!on]);
        return r.ok ? r.data : { success: false };
      },
      async peerView() { return { success: false, code: 'old' }; },        // a paired computer's TV: not in this version
      async peerLive() { return { success: false }; },
    };

    // ── the sky (the desktop's place and weather): asked only when the page calls host.sky(), at most every 10 min ──
    let skyAns = null, skyNext = 0, skyJob = null;
    function sky() {
      if (now() < skyNext) return Promise.resolve(skyAns);
      if (!skyJob) {
        skyJob = api('office_sky').then(r => {
          const good = r.ok && r.data.success !== false;
          if (good) skyAns = r.data;                              // a failure keeps the last answer
          skyNext = now() + (good ? SKY_MS : SKY_RETRY_MS);
          skyJob = null;
          return skyAns;
        });
      }
      return skyJob;
    }

    // ── the host office.html is given ─────────────────────────────────────────
    const host = {
      get: store.get, set: store.set,
      lang: () => lang(),
      close: () => { native('close'); },
      boardOnly: () => false,
      isBoardKey: () => false,
      uiScale: () => { const u = Number(app().uiScale); return u >= 0.7 && u <= 1.6 ? u : 1; },
      chat, board, objects, sky,
      objectsChanged() { objsAt = 0; tasksAt = 0; tick(); },
      md: text => (rules && rules.md ? rules.md(String(text || ''), isZh()) : null),
      // a bot's settings editor is the app's: the page keeps open under it (it is the app that decides to leave)
      editBot(role) {
        if (!(app().caps && app().caps.editBot) || !role) return false;
        native('editBot', { role: String(role) });
        return true;
      },
    };

    // ── the snapshot ──────────────────────────────────────────────────────────
    const DOT = { working: 'sig-working', decision: 'sig-decision', stuck: 'sig-stuck', done: 'sig-done' };
    function tabsFor() {
      return ((tabsAns && tabsAns.tabs) || []).map(x => {
        let said = String(x.narration || '');
        try { said = rules && rules.preview ? rules.preview(said, 140, { agent: true, lang: lang() }) : said; } catch (_) { /* the raw line */ }
        return { sid: String(x.sid), label: x.label || '', kind: x.kind || 'terminal', role: x.role || '', dot: DOT[x.state] || '',
                 cmd: x.cmd || '', line: x.line || '', said, loop: x.loop || null };
      });
    }
    function noteDeliv() {
      const o = deliv && objs.find(x => x.id === 'deliverables');
      if (!o) return;
      o.badge = Number(deliv.new) || 0; o.by_task = deliv.by_task || {};
      o.by_owner = {};
      for (const k of Object.keys(deliv.by_owner || {})) { const v = deliv.by_owner[k]; o.by_owner[k] = { label: v.label, count: v.count, new: v.new, closed: !!v.closed, types: v.types || [] }; }
    }
    function snapshot() {
      const tabs = tabsFor(), t = now();
      const on = !!botsState.enabled;
      const snap = core.build({
        botsOn: on, roster: botsState.roster, primary: botsState.primary, tabs, tasks, peers, peerTasks, peerOps: {},
        helpers: (tabsAns && tabsAns.helpers) || {}, seen, now: t,
        kit: { palette: kit.PALETTE || {}, shapes: Object.keys(kit.SHAPES || {}), defaultAvatar: kit.defaultAvatar },
      });
      // key → sid, as build() picks it (the first tab of a bot's role; every other AI tab is an intern); build() leaves sids out
      const known = new Set(Object.keys(botsState.roster).filter(r => botsState.roster[r] && botsState.roster[r].bot && !botsState.roster[r].bot.external));
      keyTab = {};
      for (const x of tabs) {
        const role = on ? x.role : '', kind = on ? x.kind : (x.kind === 'bot' ? 'temp' : x.kind);
        if (role && known.has(role)) { if (!keyTab['b:' + role]) keyTab['b:' + role] = x.sid; }
        else if (kind === 'temp') keyTab['t:' + x.sid] = x.sid;
      }
      noteDeliv();
      snap.objects = clone(objs);
      return core.track(mem, snap, t);
    }
    function push() {
      let O = null;
      try { O = win.Office; } catch (_) { O = null; }
      if (!O || typeof O.update !== 'function') return false;
      const snap = snapshot();
      try { O.update(snap); } catch (e) { scriptError({ message: e && e.message, error: e }); return true; }
      lastSnap = snap;
      if (!ready && !failed) {
        ready = true; clearT(readyTimer); readyTimer = 0; clearT(heldTimer); heldTimer = 0;
        post({ op: 'ready', v: app().v });
        if (held) { tell(held); held = null; }          // an error before the drawing that did not stop it
      }
      return true;
    }
    // The end of the page for the app: once, and never followed by a `ready` (the app would draw a page it was told is broken).
    function fail(why, detail) {
      if (failed) return;
      failed = true;
      clearT(readyTimer); readyTimer = 0;
      post(Object.assign({ op: 'error', why }, detail || {}));
      if (why === 'off' || why === 'app_too_old') stop();
    }
    // A script error on the page is reported, and only ends the page when the room cannot be drawn. A stray error (a
    // handler, a timer, something the app injected) leaves the room on screen: once ready, each is posted with fatal:false
    // (at most 5 different messages, for the app's debug view). Before the first drawing the page gets SCRIPT_GRACE_MS to
    // draw anyway (the next round tries again); if it has not, that first error ends it. WebKit's "ResizeObserver loop
    // completed with undelivered notifications" is a warning about layout, not an error, and is not passed on at all.
    const said = [];
    let held = null, heldTimer = 0;
    function scriptError(ev) {
      const msg = String((ev && ev.message) || (ev && ev.error && ev.error.message) || '').slice(0, 300);
      if (/ResizeObserver loop/i.test(msg) || failed) return;
      const src = String((ev && ev.filename) || '').split(/[\\/]/).pop().slice(0, 80);
      const detail = { message: msg, source: src, line: Number(ev && ev.lineno) || 0, col: Number(ev && ev.colno) || 0 };
      if (!ready) {
        if (!held) held = detail;
        if (!heldTimer) heldTimer = setT(() => { heldTimer = 0; if (!ready) fail('script', held); }, SCRIPT_GRACE_MS);
        return;
      }
      tell(detail);
    }
    function tell(detail) {
      if (failed || said.length >= 5 || said.indexOf(detail.message) >= 0) return;
      said.push(detail.message);
      post(Object.assign({ op: 'error', why: 'script', fatal: false }, detail));
    }

    // ── the rounds ────────────────────────────────────────────────────────────
    async function round() {
      const t = now(), jobs = {};
      jobs.tabs = api('office_tabs');
      jobs.peers = api('link_peer_bots');
      if (t - botsAt >= BOTS_MS) jobs.bots = api('bots_state');
      if (t - tasksAt >= TASKS_MS) { jobs.tasks = api('tasks_overview'); jobs.peerTasks = api('link_peer_tasks'); }
      if (t - objsAt >= OBJECTS_MS) jobs.objs = api('office_objects');
      const names = Object.keys(jobs), got = await Promise.all(names.map(n => jobs[n])), r = {};
      names.forEach((n, i) => { r[n] = got[i]; });
      if (!running) return;
      if (names.some(n => r[n].code === 'off' && (n === 'tabs' || n === 'objs'))) { fail('off'); return; }
      if (r.bots && r.bots.ok) { botsState = { enabled: !!r.bots.data.enabled, roster: r.bots.data.roster || {}, primary: r.bots.data.primary || '' }; botsAt = t; }
      else if (r.bots) botsAt = t;                                   // asked: ask again in 30 s, not every 3
      if (r.peers && r.peers.ok && isObj(r.peers.data.peers)) peers = r.peers.data.peers;
      if (r.tasks) {
        tasksAt = t;
        if (r.tasks.ok && r.tasks.data.success !== false) { const d = r.tasks.data; tasks = { open: d.open || [], stuck: d.stuck || [], decisions: d.decisions || [] }; deliv = d.deliverables || null; }
      }
      if (r.peerTasks && r.peerTasks.ok && isObj(r.peerTasks.data.peers)) peerTasks = r.peerTasks.data.peers;
      if (r.objs) {
        objsAt = t;
        if (r.objs.ok && r.objs.data.success !== false && Array.isArray(r.objs.data.objects)) objs = r.objs.data.objects;
      }
      if (r.tabs.ok && r.tabs.data.success !== false) {
        tabsAns = r.tabs.data;
        if (!r.bots && !botsAt && typeof tabsAns.bots_on === 'boolean') botsState.enabled = tabsAns.bots_on;
        failures = 0;
        if (!push()) waitOffice();
      } else if (r.tabs.code === 'offline' || r.tabs.code === 'timeout') failures = Math.min(failures + 1, 4);
    }
    // office.html's own script (which defines Office.update) runs after this file: wait for it without asking the desktop again
    let waiting = false;
    function waitOffice() {
      if (waiting) return;
      waiting = true;
      const look = () => { if (!running) { waiting = false; return; } if (push()) { waiting = false; return; } setT(look, 100); };
      setT(look, 100);
    }
    async function tick() {
      if (!running || busy) return;
      busy = true;
      try { await round(); } catch (_) { /* what was kept stays */ } finally { busy = false; }
    }
    function again(ms) {
      clearT(timer);
      timer = setT(() => { timer = 0; tick().then(() => { if (running && !timer) again(Math.min(TICK_MS * Math.pow(2, failures), BACKOFF_MAX)); }); }, ms);
    }
    function start() {
      if (running) return;
      running = true;
      botsAt = tasksAt = objsAt = 0;
      tick().then(() => { if (running && !timer) again(Math.min(TICK_MS * Math.pow(2, failures), BACKOFF_MAX)); });
    }
    function stop() { running = false; clearT(timer); timer = 0; }

    // the app's side: background / foreground
    function active(on) {
      const go = on !== false;
      win.__sfInactive = !go;                                       // office.html's hidden() looks at this too (WKWebView's document.hidden is not trusted)
      try { win.document.dispatchEvent(new win.Event('visibilitychange')); } catch (_) { /* no document */ }
      if (!go) {
        wasLive = liveId;
        stop();
        if (wasLive) api('office_ops_live', [false]);
        return;
      }
      if (running) return;
      running = true;
      if (wasLive) { liveId = wasLive; api('office_ops_live', [true]); wasLive = ''; }
      tick().then(() => { if (running && !timer) again(Math.min(TICK_MS * Math.pow(2, failures), BACKOFF_MAX)); });
    }

    function init() {
      const a = win.SFOfficeApp;
      if (!a) return false;
      a.active = active;
      if (!(Number(a.v) >= BRIDGE)) { fail('app_too_old'); return false; }
      try { win.addEventListener('error', ev => scriptError(ev)); } catch (_) { /* no events */ }
      readyTimer = setT(() => { if (!ready) fail('no_snapshot'); }, FIRST_MS);
      start();
      return true;
    }
    return { host, init, start, stop, active, tick, snapshot: () => lastSnap, sets, constants: { BRIDGE, TICK_MS, TASKS_MS, OBJECTS_MS } };
  }

  if (typeof module === 'object' && module.exports) { module.exports = { create, BRIDGE }; }
  if (root && root.SFOfficeApp && !root.SFOfficeHost && root.SFOfficeCore) {
    const h = create({ window: root, core: root.SFOfficeCore, rules: root.SFChatRules, kit: root.SFBotsKit || {},
                       warn: m => { try { root.console.warn(m); } catch (_) { /* no console */ } } });
    root.SFOfficeHost = h.host;
    root.SFOfficeAppHost = h;
    h.init();
  }
})(typeof window !== 'undefined' ? window : null);
