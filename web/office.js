/* ShellBot office (settings.experimental_office, on for a new install) — see docs/ai-skill.md §4.10.
 *
 * The same overlay also opens the whiteboard on its own (SFOffice.openBoard, "board-only": office.html draws no floor, only
 * the board sheet, and closing the sheet closes the overlay). That does not need the office switch: web/board.js puts a board
 * button (with the count of decisions waiting on the person) in the entry row at the top of the sidebar of both views and a keyboard shortcut.
 *
 * Draws the bots (staff, 正職), the AI tabs that are not bots (interns, 實習生) and the subagents a
 * Claude Code tab has launched (part-timers, 工讀生) as one office floor. The floor itself is
 * web/office.html (Canvas2D, no network), loaded in an <iframe> over the whole window so its CSS
 * and DOM cannot clash with this page. This file:
 *
 *   - puts the switch next to the other experimental switches (Settings → Office) and, while it is
 *     on, the 辦公室 button (#btn-office, web/entry.js's row) opens it. Off: that button opens an intro card instead; no iframe, no timer.
 *   - builds the office's snapshot from what the page already holds: the sidebar's dots
 *     (sessions[sid]._dotClass, the same light the tab shows), bots.js's roster and its paired
 *     machines' bots (SFBots.state / peerBots: already polled), plus two cheap calls made only
 *     while the office is open: tasks_overview (one small SQLite read, at most every 9 s) and
 *     office_helpers (list + stat of the open Claude tabs' subagents folders; office_helpers.py).
 *     The floor itself reads no screen and no transcript.
 *     Each tab's `loop` (a /loop or cron routine: agent_status.detect_schedules puts it on the tab's
 *     status whether or not the Loops panel is on) comes from the sessions the page already holds.
 *     The lobby's task board and calendar views ask SFOffice.host.board for what they open: task_detail
 *     (a task's history, only when its card is opened), task_user_act (the person's 「我做好了」 / 「留言」 on that card)
 *     and schedules_list (this computer's schedules, only while the calendar view is showing, at most once a minute:
 *     office.html paces it). The office writes little itself: task_user_act, and setEnabled (schedule_set_enabled)
 *     switching one of this computer's schedules on or off; every other schedule change is a message to the schedule's
 *     owner agent (board.request, the chat panel's send).
 *   - gives the panel of the person being followed their conversation and a message box
 *     (SFOffice.host.chat): the chat view's own routes for that one tab (chat_view every 2 s while
 *     the panel's Conversation tab is showing and the window is in front, chat_send, chat_answer),
 *     shaped by chatShape with web/chat.js's rules. A paired computer's bot or intern with a tab is
 *     reached through that computer's POST /link/chat (link_chat), as the chat view reaches a remote
 *     tab. A bot with no tab (here or there) and a part-timer get no box.
 *   - pushes it every 3 s while the office is open and the window is in front:
 *     iframe.contentWindow.Office.update(snapshot).
 *   - keeps the office's seats, accessories and view mode for this computer: pywebview starts
 *     in private mode, which clears localStorage on every start, so office.html stores them
 *     through SFOffice.host and this file saves them with office_prefs_save (office.json in
 *     the state folder). config.json is not touched.
 *
 * The mapping (build, track, tabState, peerState) is pure, lives in web/office_core.js and runs in node: tests_office_snapshot.js.
 * Where the snapshot's data comes from is one object here (`source`: the main window's state and the pywebview api); the
 * phone app's office (web/office_app_host.js) feeds the same build() / track() from the desktop's /link/office/* instead.
 * The overlay is covered by tests_office_ui.py.
 */
(function (root) {
  'use strict';

  // The mapping (build, track, tabState, peerState…) is pure and lives in web/office_core.js (SFOfficeCore); this file is the page.
  const pure = (root && root.SFOfficeCore) || require('./office_core.js');
  const { build, track, tabState, peerState, cliName, avatar, chatShape, queueWords, CHAT_LIMIT, loopOf, boardOf, personKey, schedShape, whoOf } = pure;
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; return; }

  // ── the page: switch, button, overlay ────────────────────────────────────
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  // ── the data source ───────────────────────────────────────────────────────
  // Everything the snapshot is made of, in one place, so the same build() / track() can sit on another source
  // (web/office_app_host.js: the phone app, which has no main window and asks the desktop over /link/office/*).
  // This one is the main window's own state and the pywebview api. A source gives:
  //   state()  {enabled, roster, primary, peers}   bots.js's state (SFBots.enabled / state / peerBots)
  //   tabs()   this computer's tabs in sidebar order (pageTabs below)
  //   kit()    {palette, shapes, defaultAvatar}    the faces' parts (SFBotsKit, through SFBots)
  //   has(m)   whether the answer to `m` can be asked for (an older app lacks some)
  //   call(m, ...args)  the answer of js_api method `m`, parsed; rejects when it fails (callers keep what they had)
  const source = {
    state() {
      const B = root.SFBots || {}, st = (B.state && B.state()) || {};
      return { enabled: !!(B.enabled && B.enabled()), roster: st.roster || {}, primary: st.primary || '', peers: (B.peerBots && B.peerBots()) || {} };
    },
    tabs: () => pageTabs(),
    kit() { const B = root.SFBots || {}; return { palette: B.PALETTE || {}, shapes: Object.keys(B.SHAPES || {}), defaultAvatar: B.defaultAvatar }; },
    has(m) { const a = api(); return !!(a && a[m]); },
    async call(m, ...args) { const r = await api()[m](...args); return typeof r === 'string' ? JSON.parse(r) : r; },
  };
  const isZh = () => /^zh/i.test((host() && host().lang && host().lang()) || navigator.language || '');
  const t = (en, zh) => (isZh() ? zh : en);
  const cfg = () => (host() && host().cfg()) || null;
  const enabled = () => { const c = cfg(); return !!(c && c.settings && c.settings.experimental_office === true); };

  let prefs = null, saveTimer = 0;
  const tzName = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) { return ''; } };
  // The office's prefs as office.json has them. A key set before the office was ever opened would otherwise be saved alone over the
  // person's seats (office_prefs_save writes the whole file): false when they could not be read, and then nothing is written.
  async function loadPrefs() {
    if (prefs) return true;
    if (!source.has('office_prefs')) return false;
    try { const r = await source.call('office_prefs'); if (!r || r.success === false || typeof r.prefs !== 'object') return false; prefs = prefs || r.prefs || {}; return true; } catch (_) { return false; }
  }
  const clone = v => (v == null ? v : JSON.parse(JSON.stringify(v)));
  function savePrefs(now) {
    clearTimeout(saveTimer);
    const go = () => { saveTimer = 0; const a = api(); if (a && a.office_prefs_save && prefs) a.office_prefs_save(JSON.stringify(prefs)).catch(() => {}); };
    if (now) go(); else saveTimer = setTimeout(go, 400);
  }
  // The tab a person in the office is: 'b:<role>' → the bot's tab on this computer (the first in the sidebar's
  // order, as build() picks), 't:<sid>' → that tab. '' for anyone without one here (asleep, part-timer, a
  // paired computer's). Looked up at every call: build() leaves sids out of the snapshot, and a relaunch
  // can give a bot a new tab.
  function sidOf(key) {
    const k = String(key || ''), H = host(), S = (H && H.sessions()) || {};
    if (k.startsWith('t:')) { const sid = k.slice(2); return S[sid] && !S[sid].isRemote ? sid : ''; }
    if (k.startsWith('b:')) {
      if (!source.state().enabled) return '';
      const hit = source.tabs().find(x => x.role === k.slice(2));
      return hit ? hit.sid : '';
    }
    return '';
  }
  // 'p:<pid>/<role>' → a paired computer's bot's tab there, 'q:<pid>/<rsid>' → one of its interns: {peer, rsid},
  // from the rows bots.js already polls (SFBots.peerBots). null for one asleep (no tab) or not listed.
  function peerOf(key) {
    const m = /^([pq]):([^/]+)\/(.+)$/.exec(String(key || ''));
    if (!m) return null;
    const p = source.state().peers[m[2]];
    if (!p) return null;
    if (m[1] === 'q') return (p.tabs || {})[m[3]] ? { peer: m[2], rsid: m[3] } : null;
    const b = (p.bots || []).find(x => x && String(x.role) === m[3]);
    return b && b.sid && b.state !== 'asleep' ? { peer: m[2], rsid: String(b.sid), label: String(b.label || b.role) } : null;
  }
  const targetOf = key => { const sid = sidOf(key); return sid ? { sid } : peerOf(key); };
  // The chat view's calls, on this computer's tab or a paired one's (its POST /link/chat, arguments by name,
  // as chat.js sends them for a remote tab).
  const REMOTE_ARGS = { view: ['limit', 'rev'], send: ['text', 'clear_draft'], answer: ['n', 'qid'] };
  function callOn(tg, action, ...args) {
    if (tg.sid) return chatOf().call(tg.sid, action, ...args);
    const named = {};
    (REMOTE_ARGS[action] || []).forEach((n, i) => { named[n] = args[i]; });
    return api().link_chat(tg.peer, tg.rsid, action, JSON.stringify(named));
  }
  const lang = () => (host() && host().lang && host().lang()) || 'en';
  const chatOf = () => { const C = root.SFChat; return C && C.call && C.rules ? C : null; };
  // What the panel's conversation calls: the chat view's own routes (chat_view / chat_send / chat_answer),
  // for one person's tab. Answers are plain objects; nothing throws.
  const chatHost = {
    async view(key, rev, name) {
      const tg = targetOf(key), C = chatOf();
      if (!tg) return { ok: false, why: 'no_tab' };
      if (!C) return { ok: false, why: 'no_chat' };
      let r = null;
      try { r = JSON.parse(await callOn(tg, 'view', CHAT_LIMIT, rev || '')); } catch (_) { r = null; }
      if (!r || r.success === false) return { ok: false, why: (r && r.code) || 'error' };   // old_peer, unreachable…
      // what it is doing: this computer's status monitor; a paired one's tab, the activity its chat_view gives
      const st = tg.sid ? ((host().sessions() || {})[tg.sid] || {})._serverStatus : (r.unchanged ? null : { state: r.activity });
      const doing = st ? C.rules.plainDoing(st, lang()) : null;
      if (r.unchanged) return { ok: true, unchanged: true, rev: r.rev, doing };
      return Object.assign({ ok: true, doing, remote: !tg.sid }, chatShape(r, C.rules, lang(), name || '', Date.now()));
    },
    async send(key, text, clearDraft) {
      const tg = targetOf(key);
      if (!tg || !chatOf()) return { success: false, code: 'no_tab' };
      try { return JSON.parse(await callOn(tg, 'send', String(text || ''), !!clearDraft)); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    async answer(key, n, qid) {
      const tg = targetOf(key);
      if (!tg || !chatOf()) return { success: false, code: 'no_tab' };
      try { return JSON.parse(await callOn(tg, 'answer', n, qid)); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    has: key => !!targetOf(key),
    // The tab itself, in the main window: the office closes and the sidebar switches to it (a paired
    // computer's tab opens as the sidebar's remote row opens it).
    open(key) {
      const tg = targetOf(key), H = host();
      if (!tg || !H) return false;
      if (tg.sid) {
        if (!H.switchTab) return false;
        close();
        H.switchTab(tg.sid);
        return true;
      }
      if (!H.openRemote || !H.openRemote(tg.peer, tg.rsid, tg.label)) return false;   // not reachable or not ours to control
      close();
      return true;
    },
    openUrl(url) { if (/^https?:\/\//i.test(String(url || ''))) { try { api().open_url(String(url)); } catch (_) {} } },
    // A picture (or a file copied in Finder) pasted into the panel's box: saved as the terminal's paste saves it
    // (save_image / get_clipboard_files); for a paired computer's tab the file is sent there first (link_send_file:
    // that computer's own path comes back), since a path on this computer means nothing there. [{path, name}] or
    // {ok: false, message}.
    async attach(key, dataUrl, name) {
      const tg = targetOf(key), a = api();
      if (!tg || !a) return { ok: false, message: '' };
      let paths = [];
      try {
        if (dataUrl) { const p = await a.save_image(dataUrl); if (p && !/^ERROR/.test(String(p))) paths = [String(p)]; }
        else paths = JSON.parse((await a.get_clipboard_files()) || '[]');
      } catch (e) { return { ok: false, message: String(e) }; }
      if (!paths.length) return { ok: false, message: '' };
      if (tg.sid) return { ok: true, files: paths.map(p => ({ path: p, name: name || p.split(/[\\/]/).pop() })) };
      const out = [];
      for (const p of paths) {
        let r = null;
        try { r = JSON.parse(await a.link_send_file(tg.peer, p)); } catch (_) { r = null; }
        if (!r || !r.success || !r.saved) return { ok: false, message: (r && r.message) || '' , remote: true };
        out.push({ path: String(r.saved), name: name || p.split(/[\\/]/).pop() });
      }
      return { ok: true, files: out };
    },
    // An intern asked to leave: its tab is closed, as its ✕ does (here: the sidebar's close; a paired computer's:
    // that computer closes it, as the sidebar's remote ✕ does). Only an intern (a tab that is no bot); the office
    // asks first in its own panel (the page's own confirm would sit under the office).
    async dismiss(key) {
      const k = String(key || ''), H = host();
      if (k.startsWith('t:')) {
        const sid = sidOf(k);
        if (!sid || !H || !H.closeTab) return { ok: false };
        try { await H.closeTab(sid); return { ok: true }; } catch (e) { return { ok: false, message: String(e) }; }
      }
      const m = /^q:([^/]+)\/(.+)$/.exec(k);
      if (!m || !peerOf(k)) return { ok: false };
      try { const r = JSON.parse(await api().link_remote_close(m[1], m[2])); return { ok: r && r.success !== false, message: (r && r.message) || '' }; }
      catch (e) { return { ok: false, message: String(e) }; }
    },
  };
  // The task board's and the schedule calendar's calls (office.html asks only while that view is showing).
  // detail: one task and its events newest first (task_detail); schedules: this computer's own schedules (schedules_list,
  // asked at most once a minute by the view), shaped by schedShape. The calendar changes only two things itself:
  // setEnabled flips one of this computer's schedules on or off (schedule_set_enabled: the answer says what launchd did).
  // Anything else is a request to the schedule's owner agent: request() puts a message in that agent's tab as the person
  // typed it (the chat panel's send; never clears the person's unsent draft), canRequest says whether it has a tab to reach.
  const boardHost = {
    async detail(id) {
      try { return await source.call('task_detail', String(id)); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    // The person's own hand on a task (task_user_act): "done" or "note". After a success the next tick asks the store again
    // at once (not in 9 s), so the board shows it within a second or two. No optimistic change: the answer is the store's.
    async act(id, action, text) {
      try {
        const r = JSON.parse(await api().task_user_act(String(id), String(action), String(text || '')));
        if (r && r.success) { tasksAt = 0; setTimeout(() => { tick(); }, 250); }
        return r;
      } catch (e) { return { success: false, message: String(e) }; }
    },
    // The line a decision's answer (or a done / note) sends to the bot that asked: office.html's reply box and the pending list's
    // (web/pending.js) both use it, so the two read the same to that bot. mode: 'reply' | 'done' | 'note'.
    lineOf(mode, id, title, text) {
      const ttl = String(title || '').slice(0, 40), body = String(text || '');
      return mode === 'reply' ? `（看板）#${id}「${ttl}」我的回覆：${body}` : `（看板）#${id}「${ttl}」${mode === 'done' ? '我做好了' : '留言'}${body ? '：' + body : ''}`;
    },
    // Who asked a decision (the tab to tell and what to call it), from the page's own roster and tabs: null when nobody here can be
    // told (no tab, a paired computer's bot we cannot reach, the person themselves).
    askerOf(task) {
      const row = boardRows({ decisions: [task] })[0], k = row && row.requester_key;
      if (!k || k === 'user' || !chatHost.has(k)) return null;
      return { key: k, name: nameOfKey(k) || String(task.requester || '') };
    },
    // Someone asked but cannot be told here (askerOf is null): {name, why} in the page's language, so the answer box says so
    // instead of leaving the line out. null when there is nobody to tell (no requester, the person themselves) or they can be told.
    askerMiss(task) {
      const raw = String((task && task.requester) || '');
      if (!raw || raw === 'user' || boardHost.askerOf(task)) return null;
      const row = boardRows({ decisions: [task] })[0], k = row && row.requester_key;
      return k ? { name: nameOfKey(k) || raw, why: t('no tab of theirs is open (resting)', '他現在沒有開著的分頁（在休息）') }
        : { name: raw, why: t('no bot or tab here has that name', '這台找不到這個名字的 bot 或分頁') };
    },
    // Answer a decision from the pending list: task_user_act(id, 'done', text), then, when asked to, one line to whoever asked.
    // Nothing is sent without text. {success, message, told: true | false | null (null: not asked to)}.
    async reply(task, text, tell) {
      const body = String(text || '').trim();
      if (!body) return { success: false, code: 'empty', message: t('Write your answer first.', '要先寫下你的決定或回答。') };
      const r = await boardHost.act(task.id, 'done', body);
      if (!r || !r.success) return r || { success: false };
      let told = null;
      if (tell) {
        const a = boardHost.askerOf(task);
        told = false;
        if (a) { const q = await chatHost.send(a.key, boardHost.lineOf('reply', task.id, task.title, body), false); told = !!(q && q.success !== false); }
      }
      return Object.assign({}, r, { told });
    },
    // The whiteboard on a card of this computer's, or on a paired computer's (web/pending.js: 「在白板上看」, 「隔壁：X 有 N 件等你」).
    // office.html takes it (focus) once the board shows.
    takeFocus() { const f = boardFocus; boardFocus = null; return f; },
    async schedules() {
      try { return schedShape(await source.call('schedules_list')); }
      catch (e) { return { ok: false, unsupported: false, configured: true, launchd_ok: true, rows: [], at: Date.now() }; }
    },
    async setEnabled(label, on) {
      try { return JSON.parse(await api().schedule_set_enabled(String(label), !!on)); }
      catch (e) { return { success: false, message: String(e) }; }
    },
    canRequest: key => chatHost.has(key),
    request: (key, text) => chatHost.send(key, text, false),
  };
  // The board's rows (boardOf: each task once, with who it is as the office keys people) for a tasks_overview-shaped list, from the page's
  // own roster and tabs: what the pending list (web/pending.js) needs to know who asked, without the office being open.
  function boardRows(tasks) {
    const st = source.state(), on = st.enabled;
    const roster = on ? st.roster : {};
    const roles = Object.keys(roster).filter(r => roster[r] && roster[r].bot && !roster[r].bot.external);
    const tabs = source.tabs().map(x => Object.assign({}, x, on ? {} : { role: '', kind: x.kind === 'bot' ? 'temp' : x.kind }));
    return boardOf(Object.assign({ open: [], stuck: [], decisions: [] }, tasks), whoOf(roles, roster, tabs), Date.now()).all;
  }
  // 'b:<role>' → the bot's name, 't:<sid>' → the tab's, as the office would call them.
  function nameOfKey(key) {
    const k = String(key || ''), st = source.state();
    if (k.startsWith('b:')) { const e = st.roster[k.slice(2)]; return String((e && e.label) || k.slice(2)); }
    if (k.startsWith('t:')) { const s = ((host() && host().sessions()) || {})[k.slice(2)]; return String((s && s.label) || k.slice(2)); }
    return '';
  }
  // The office's objects (furniture on the lobby wall: the bookshelf, the personnel cabinet; office_objects.py, web/office_objects.js):
  // `objs` are their declarations with the badge (office_objects, asked at open and at most every 30 s, never for the board alone); a
  // popup's list / entry / action are the backend's calls, one per use, in the page's language. A `{}` or failed answer is "no objects".
  // md: an entry's Markdown through the chat view's own safe renderer (null when the chat view is not there: the popup shows it plain).
  const OBJECTS_MS = 30000;
  const objectsHost = {
    async call(name, ...args) {
      if (!source.has(name)) return { success: false };
      try { return (await source.call(name, ...args, isZh() ? 'zh' : 'en')) || { success: false }; } catch (e) { return { success: false, message: String(e) }; }
    },
    list: (id, q) => objectsHost.call('office_object_list', String(id), JSON.stringify(q || {})),
    item: (id, iid) => objectsHost.call('office_object_item', String(id), String(iid)),
    action: (id, iid, aid, args) => objectsHost.call('office_object_action', String(id), String(iid), String(aid), JSON.stringify(args || {})),
    // The ops board's window (web/office_ops.js): its sections and widgets, and the "I am looking" signal (no lang: it carries none)
    view: id => objectsHost.call('office_object_view', String(id)),
    async live(id, on) {
      if (!source.has('office_object_live')) return { success: false };
      try { return (await source.call('office_object_live', String(id), !!on)) || { success: false }; } catch (e) { return { success: false }; }
    },
    // A paired computer's ops window (ops_peer_view / ops_peer_live: the peer's own answer, cleaned here; `sort` is the top five's order for that answer)
    async peerView(pid, sort) {
      if (!source.has('ops_peer_view')) return { success: false, code: 'old' };
      try { return (await source.call('ops_peer_view', String(pid), isZh() ? 'zh' : 'en', String(sort || ''))) || { success: false }; } catch (e) { return { success: false, code: 'offline' }; }
    },
    async peerLive(pid, on) {
      if (!source.has('ops_peer_live')) return { success: false };
      try { return (await source.call('ops_peer_live', String(pid), !!on)) || { success: false }; } catch (e) { return { success: false }; }
    },
  };
  // The deliverables' counts ride on tasks_overview (the 9 s read above): the desks and the toolbar number follow without waiting for the
  // 30 s object refresh. Only the numbers: the object's list and entries are asked of the backend when the popup opens.
  function noteDeliv(d) {
    const o = d && objs.find(x => x.id === 'deliverables');
    if (!o) return;
    o.badge = Number(d.new) || 0; o.by_task = d.by_task || {};
    o.by_owner = {};
    for (const k of Object.keys(d.by_owner || {})) { const v = d.by_owner[k]; o.by_owner[k] = { label: v.label, count: v.count, new: v.new, closed: !!v.closed, types: v.types || [] }; }
  }
  async function refreshObjects(force) {
    if (!source.has('office_objects') || boardMode || (!force && Date.now() - objsAt < OBJECTS_MS)) return;
    objsAt = Date.now();
    const r = await objectsHost.call('office_objects');
    objs = r && r.success && Array.isArray(r.objects) ? r.objects : [];
  }
  // What office.html's `store` calls (same origin, synchronous), and its conversation panel (promises).
  const officeHost = {
    get: k => (prefs && Object.prototype.hasOwnProperty.call(prefs, k) ? clone(prefs[k]) : null),
    set: (k, v) => { prefs = prefs || {}; if (v == null) delete prefs[k]; else prefs[k] = clone(v); savePrefs(false); },
    lang: () => lang(),
    close: () => close(),
    // True while the overlay is the whiteboard alone (SFOffice.openBoard): office.html draws no floor and no office-only helper is asked.
    boardOnly: () => boardMode,
    // Whether a key is the board shortcut (web/board.js): office.html asks, as the focus is in its frame while it is open.
    isBoardKey: e => !!(root.SFBoard && root.SFBoard.isKey && root.SFBoard.isKey(e)),
    // Settings' Interface size (small / medium / large: index.html's --ui-scale); the office's words follow it
    uiScale: () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale')) || 1,
    chat: chatHost,
    board: boardHost,
    objects: objectsHost,
    // after an action in the popup that changed the lists (keep, take, accept…): the numbers on the desks and the toolbar button follow at once
    objectsChanged() { objsAt = 0; refreshObjects(true).then(() => { if (root.SFBoard && root.SFBoard.poll) root.SFBoard.poll(); }); },
    md: text => { const C = chatOf(); return C ? C.rules.md(String(text || ''), isZh()) : null; },
    // The window's sky (office_sky.py): where this computer is (from its time zone, which only this window knows on Windows) and the
    // weather there. office.html asks only while a theme with a view outside is on, at most every 10 minutes. null from an older app.
    sky: () => (source.has('office_sky') ? source.call('office_sky', tzName(), true).catch(() => null) : Promise.resolve(null)),
    // A bot's settings editor (web/bots.js): the office closes first, the editor opens in the main window. false when there is none.
    editBot(role) {
      const B = root.SFBots;
      if (!(B && B.editBot && role)) return false;
      close();
      B.editBot(String(role));
      return true;
    },
  };

  let overlay = null, frame = null, timer = 0, busy = false, lastPush = null, boardMode = false, boardFocus = null;
  let objs = [], objsAt = 0;
  let tasks = { open: [], stuck: [], decisions: [] }, tasksAt = 0, helpers = {}, peerTasks = {}, peerTasksAt = 0, peerOps = {}, peerOpsAt = 0, peerOpsBusy = false;
  const seen = {}, mem = {};
  const TICK_MS = 3000, TASKS_MS = 9000;

  // What the tab itself last said (the status monitor's `narration`), one plain line: what a 做完了 event shows. Never the
  // person's message (`task`), which the sidebar's line falls back to: a bot woken by its own background work gets no new one.
  function saidOf(s) {
    const n = String(((s && s._serverStatus) || {}).narration || ''), R = root.SFChat && root.SFChat.rules;
    try { return R && R.preview ? R.preview(n, 140, { agent: true, lang: lang() }) : n; } catch (_) { return n; }
  }
  function pageTabs() {
    const H = host(), S = (H && H.sessions()) || {}, B = root.SFBots;
    const order = ((H && H.order && H.order()) || Object.keys(S)).filter(sid => S[sid]);
    for (const sid of Object.keys(S)) if (order.indexOf(sid) < 0) order.push(sid);
    return order.filter(sid => !S[sid].isRemote).map(sid => {
      const s = S[sid] || {};
      let role = '', kind = 'terminal', line = '';
      try { role = (B && B.botOf && B.botOf(sid)) || ''; kind = (B && B.kindOf && B.kindOf(sid)) || 'terminal'; line = (B && B.previewOf && B.previewOf(sid)) || ''; } catch (_) {}
      return { sid, label: s.label || '', kind, role, dot: s._dotClass || '', cmd: s.cmd || '', line, said: saidOf(s), loop: (s._serverStatus || {}).loop || null };
    });
  }
  function snapshot() {
    const st = source.state();
    const snap = build({
      botsOn: st.enabled, roster: st.roster, primary: st.primary,
      tabs: source.tabs(), tasks, peers: st.peers, peerTasks, peerOps, helpers, seen,
      kit: source.kit(),
      now: Date.now(),
    });
    snap.objects = clone(objs);
    return track(mem, snap, Date.now());
  }
  // The paired computers' ops summaries (their small TVs on the room walls): each one asked once a minute while the office is open, only while
  // this computer's own ops board is on (it is in the objects) and never for the board alone. Not awaited by the tick: a computer that does not
  // answer takes up to 6 s, and the 3 s push must not wait for it. A failure keeps the last good summary for 150 s, then the TV says why it is grey.
  const peerOpsTiming = { every: 60000, keep: 150 };                  // a test shortens them
  function peerOpsState(prev, r, now) {
    if (r && r.success) return Object.assign({ state: 'ok', at: now }, r);
    const code = r && r.code, state = code === 'old' || code === 'denied' || code === 'offline' ? code : 'error';
    if ((state === 'offline' || state === 'error') && prev && prev.state === 'ok' && now - prev.at < peerOpsTiming.keep) return prev;     // a blip: the last summary stays
    return { state: state === 'error' ? 'offline' : state, at: now };
  }
  function refreshPeerOps() {
    if (!source.has('ops_peer') || boardMode || peerOpsBusy || Date.now() - peerOpsAt < peerOpsTiming.every || !objs.some(o => o.id === 'ops')) return;
    const pids = Object.keys(source.state().peers);
    peerOpsAt = Date.now(); peerOpsBusy = true;
    Promise.all(pids.map(async pid => {
      let r = null;
      try { r = await source.call('ops_peer', pid, isZh() ? 'zh' : 'en'); } catch (_) { r = null; }
      if (!overlay || (r && r.code === 'off')) return;               // closed meanwhile, or this computer's own switch went off
      peerOps[pid] = peerOpsState(peerOps[pid], r, Date.now() / 1000);
    })).then(() => { for (const pid of Object.keys(peerOps)) if (pids.indexOf(pid) < 0) delete peerOps[pid]; }).catch(() => {}).then(() => { peerOpsBusy = false; });
  }
  async function refreshData() {
    if (!api()) return;
    if (source.has('tasks_overview') && Date.now() - tasksAt >= TASKS_MS) {
      tasksAt = Date.now();
      try {
        const r = await source.call('tasks_overview');
        if (r && r.success) { tasks = { open: r.open || [], stuck: r.stuck || [], decisions: r.decisions || [] }; if (root.SFBoard) { root.SFBoard.note(tasks.decisions.length, tasks.decisions); if (root.SFBoard.noteDeliv) root.SFBoard.noteDeliv(r.deliverables); } noteDeliv(r.deliverables); }
      } catch (_) {}
    }
    // The paired computers' boards (the calls an older app does not have are skipped: no peer boards, no error). Same cadence
    // as the local tasks, never on the 3 s tick; the answer is the host's own (it caches what it read from the other computers).
    if (source.has('link_peer_tasks') && Date.now() - peerTasksAt >= TASKS_MS) {
      peerTasksAt = Date.now();
      try { const r = await source.call('link_peer_tasks'); if (r && r.peers && typeof r.peers === 'object') { peerTasks = r.peers; if (root.SFBoard && root.SFBoard.notePeers) root.SFBoard.notePeers(r.peers); } } catch (_) {}
    }
    await refreshObjects(false);
    refreshPeerOps();
    if (source.has('office_helpers') && !boardMode) {          // the floor's part-timers: not asked for by the board alone (and refused with the switch off)
      try { const r = await source.call('office_helpers'); if (r && r.success) helpers = r.helpers || {}; } catch (_) {}
    }
  }
  function push() {
    if (!frame) return false;
    let O = null;
    try { O = frame.contentWindow && frame.contentWindow.Office; } catch (_) { O = null; }
    if (!O || typeof O.update !== 'function') return false;
    lastPush = snapshot();
    try { O.update(lastPush); } catch (e) { try { api().js_debug('office', 'update failed: ' + e); } catch (_) {} }
    return true;
  }
  async function tick() {
    if (!overlay || busy || document.hidden) return;
    busy = true;
    try { await refreshData(); if (overlay) push(); } finally { busy = false; }
  }
  function onKey(e) {
    if (!overlay) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (!overlay.contains(e.target)) e.stopPropagation();   // keys meant for the office never reach the terminal under it
  }
  function onVisible() { if (!document.hidden && overlay) tick(); }

  // open(): the office (needs the switch). open({board: true}): the whiteboard alone, whatever the switch says.
  async function open(opts) {
    const bo = !!(opts && opts.board);
    if ((!bo && !enabled()) || overlay) return;
    if (!prefs && source.has('office_prefs')) {      // also for the board alone: a set() would otherwise save a lone key over the person's seats
      try { const r = await source.call('office_prefs'); prefs = (r && r.prefs) || {}; } catch (_) { prefs = {}; }
    }
    if (overlay || (!bo && !enabled())) return;
    boardMode = bo;
    tasksAt = 0; peerTasksAt = 0; peerTasks = {}; peerOps = {}; peerOpsAt = 0; objsAt = 0; objs = [];   // a freshly opened office asks again, not on the last open's answer
    overlay = document.createElement('div');
    overlay.id = 'sfo-overlay';
    overlay.tabIndex = -1;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    const nm = bo ? t('Board', '看板') : t('Office', '辦公室');
    overlay.setAttribute('aria-label', nm);
    if (bo) overlay.classList.add('sfo-board');
    overlay.innerHTML = `<iframe class="sfo-frame" src="office.html" title="${nm}"></iframe>` +
      (bo ? '' : `<button class="sfo-close" type="button" aria-label="${t('Close the office (Esc)', '關閉辦公室（Esc）')}" title="${t('Close (Esc)', '關閉（Esc）')}">${SFIcons.html('close')}</button>`);
    document.body.appendChild(overlay);
    frame = overlay.querySelector('iframe');
    const x = overlay.querySelector('.sfo-close');
    if (x) x.addEventListener('click', () => close());
    frame.addEventListener('load', async () => {
      try { frame.contentWindow.focus(); } catch (_) {}
      busy = true;
      try { await refreshData(); } finally { busy = false; }
      push();
    });
    root.addEventListener('keydown', onKey, true);
    document.addEventListener('visibilitychange', onVisible);
    timer = setInterval(tick, TICK_MS);
    try { overlay.focus(); } catch (_) {}
  }
  function close() {
    if (!overlay) return;
    clearInterval(timer); timer = 0;
    root.removeEventListener('keydown', onKey, true);
    document.removeEventListener('visibilitychange', onVisible);
    overlay.remove();                     // the iframe goes with it: no canvas, no timer left behind
    overlay = null; frame = null; busy = false; boardMode = false;
    if (saveTimer) savePrefs(true);
    try { const H = host(), s = H && H.sessions()[H.active()]; if (s && s.term) s.term.focus(); } catch (_) {}
  }

  // The office's button is the 辦公室 one in web/entry.js's row (always there; dashed while the switch is off). With the switch off
  // the office itself is closed here; the row repaints.
  function placeButton() {
    if (!enabled() && !boardMode) close();
    try { if (root.SFEntry) root.SFEntry.paint(); } catch (_) {}
  }

  // ── the switch (Settings → Office) ──────────────────────────────────────────
  function injectSettings() {
    let sw = document.getElementById('setting-experimental-office');
    if (!sw) {
      const box = document.getElementById('sx-office');
      if (!box) return;
      const wrap = document.createElement('div');
      wrap.id = 'sfo-setting';
      wrap.className = 'sx-row';
      wrap.innerHTML =
        '<div class="sx-txt"><div class="sx-label sfo-label"></div><div class="sx-hint sfo-hint"></div></div>' +
        '<div class="sx-ctl"><button class="toggle-switch" id="setting-experimental-office" data-guide="office"></button></div>';
      box.appendChild(wrap);
      sw = wrap.querySelector('#setting-experimental-office');
      sw.addEventListener('click', async function () {
        this.classList.toggle('on');
        const c = cfg();
        if (!c) return;
        c.settings = c.settings || {};
        c.settings.experimental_office = this.classList.contains('on');
        try { await api().save_settings(JSON.stringify(c.settings)); } catch (_) {}
        placeButton();
      });
    }
    const wrap = document.getElementById('sfo-setting');
    wrap.querySelector('.sfo-label').textContent = t('Office: your bots at their desks (experimental)', '辦公室：bot 坐在自己的桌子前（實驗性）');
    wrap.querySelector('.sfo-hint').textContent = t(
      'The 辦公室 button at the top of the sidebar opens an office floor: bots (staff) at their desks, AI tabs that are not bots (interns) at a long table, a Claude Code tab\'s subagents (part-timers) beside it; who is busy, waiting on you or stuck. On for a new install.',
      '側欄最上面的「辦公室」鈕會打開一層辦公室：bot（正職）坐自己的桌子，不是 bot 的 AI 分頁（實習生）坐長桌，Claude Code 分頁派出去的 subagent（工讀生）站在旁邊；誰在忙、誰在等你、誰卡住，看圖就知道。新安裝預設打開。');
    sw.classList.toggle('on', enabled());
    injectTheme();
    injectWeather();
    injectShare();
  }

  // ── 辦公室風格 (Settings → Office): office.json's `theme`; the phone app shows the desktop's ─────────────────────────
  // The names here and office.html's THEMES are the same five; night is the original look and is saved as no key at all.
  const THEMES = [['night', 'Night blue', '夜藍'], ['wood', 'Warm wood', '暖木'], ['loft', 'Industrial loft', '工業風'], ['green', 'Greenhouse', '綠意'], ['nordic', 'Nordic light', '北歐淺色']];
  const themeNow = () => { const v = prefs && prefs.theme; return THEMES.some(x => x[0] === v) ? v : 'night'; };
  async function setTheme(id) {
    if (!THEMES.some(x => x[0] === id)) return false;
    if (!(await loadPrefs())) return false;            // never a lone key over the seats
    if (id === 'night') delete prefs.theme; else prefs.theme = id;
    savePrefs(true);
    try { if (frame && frame.contentWindow && frame.contentWindow.Office && frame.contentWindow.Office.setTheme) frame.contentWindow.Office.setTheme(id); } catch (_) {}
    injectWeather();
    return true;
  }
  function injectTheme() {
    const box = document.getElementById('sx-office');
    if (!box) return;
    let row = document.getElementById('sfo-theme');
    if (!row) {
      row = document.createElement('div');
      row.id = 'sfo-theme';
      row.className = 'sx-row';
      row.innerHTML = '<div class="sx-txt"><div class="sx-label"></div><div class="sx-hint"></div></div>' +
        '<div class="sx-ctl"><select id="setting-office-theme" class="sx-input"></select></div>';
      box.appendChild(row);
      row.querySelector('select').addEventListener('change', async function () {
        const want = this.value;
        if (!(await setTheme(want))) this.value = themeNow();
      });
      loadPrefs().then(() => { const sel = document.getElementById('setting-office-theme'); if (sel) sel.value = themeNow(); });
    }
    row.querySelector('.sx-label').textContent = t('Office style', '辦公室風格');
    row.querySelector('.sx-hint').textContent = t(
      'Changes only the look: floor, walls, furniture and the view outside the windows. Seats and what the colours of the screens, the red ! and the signs mean stay the same. The phone app shows the same style.',
      '只換外觀：地板、牆、家具和窗外的景。座位，以及螢幕顏色、紅色 !、牌子代表的意思都不變。手機 App 看到的是同一個風格。');
    const sel = row.querySelector('select');
    sel.innerHTML = THEMES.map(([id, en, zh]) => `<option value="${id}">${t(en, zh)}</option>`).join('');
    sel.value = themeNow();
    sel.setAttribute('aria-label', t('Office style', '辦公室風格'));
  }

  // 窗外天氣 (settings.office_weather, on by default): the styles with a view outside show the weather where this computer is.
  const weatherOn = () => { const c = cfg(); return !(c && c.settings && c.settings.office_weather === false); };
  let placeWords = '', placeAsked = false;
  function injectWeather() {
    const box = document.getElementById('sx-office');
    if (!box) return;
    let row = document.getElementById('sfo-weather');
    if (!row) {
      row = document.createElement('div');
      row.id = 'sfo-weather';
      row.className = 'sx-row';
      row.innerHTML = '<div class="sx-txt"><div class="sx-label"></div><div class="sx-hint"></div></div>' +
        '<div class="sx-ctl"><button type="button" class="toggle-switch" id="setting-office-weather" role="switch"></button></div>';
      box.appendChild(row);
      row.querySelector('button').addEventListener('click', async function () {
        const c = cfg(), a = api();
        if (!c || !a) return;
        c.settings = c.settings || {};
        c.settings.office_weather = !weatherOn();
        injectWeather();
        try { await a.save_settings(JSON.stringify(c.settings)); } catch (_) { /* the page keeps it; the next save carries it */ }
      });
    }
    if (!placeAsked && source.has('office_sky')) {      // the place only: asking it never fetches weather
      placeAsked = true;
      source.call('office_sky', tzName(), false).then(r => {
        const p = r && r.place;
        if (p && isFinite(p.lat) && isFinite(p.lon)) {
          const ns = p.lat >= 0 ? t('N', '北緯') : t('S', '南緯'), ew = p.lon >= 0 ? t('E', '東經') : t('W', '西經');
          placeWords = isZh() ? `${p.tz || ''}（${ns} ${Math.abs(p.lat).toFixed(1)}°、${ew} ${Math.abs(p.lon).toFixed(1)}°）` : `${p.tz || ''} (${Math.abs(p.lat).toFixed(1)}° ${ns}, ${Math.abs(p.lon).toFixed(1)}° ${ew})`;
          injectWeather();
        }
      }).catch(() => {});
    }
    row.querySelector('.sx-label').textContent = t('Weather outside the windows', '窗外天氣');
    const where = placeWords ? t(`Where: ${placeWords}, from this computer's time zone. `, `位置：${placeWords}，由這台電腦的時區估算。`) : '';
    row.querySelector('.sx-hint').textContent = t(
      `In every style but Night blue, the sun and moon outside rise in the east and set in the west at this computer's place and time. With this on, the windows also show the weather there (clouds, rain, snow, fog, thunder). ${where}Weather from MET Norway (CC BY 4.0): ShellBot asks at most every 30 minutes, only while the office is open with such a style, and sends only this approximate place.`,
      `夜藍以外的風格，窗外的太陽、月亮依這台電腦的位置和時間東升西落。打開時窗外也照當地的天氣畫（雲、雨、雪、霧、打雷）。${where}天氣資料來自 MET Norway（挪威氣象局，CC BY 4.0）：只在辦公室開著、用這類風格時才問，最多每 30 分鐘一次，只送這個大約的位置。`);
    const sw = row.querySelector('.toggle-switch');
    sw.classList.toggle('on', weatherOn());
    sw.setAttribute('aria-checked', String(weatherOn()));
    sw.setAttribute('aria-label', row.querySelector('.sx-label').textContent);
  }

  // 「不分享任務給隔壁電腦」 (settings.link_share_tasks_off, default off = shared as before): a paired computer asking for
  // this one's tasks is refused (/link/tasks answers 403), so its office's next room and its board say this one does not share.
  function shareOff() { const c = cfg(); return !!(c && c.settings && c.settings.link_share_tasks_off === true); }
  function injectShare() {
    const box = document.getElementById('sx-office');
    if (!box) return;
    let row = document.getElementById('sfo-share');
    if (!row) {
      row = document.createElement('div');
      row.id = 'sfo-share';
      row.className = 'sx-row';
      row.innerHTML = '<div class="sx-txt"><div class="sx-label"></div><div class="sx-hint"></div></div>' +
        '<div class="sx-ctl"><button type="button" class="toggle-switch" id="setting-link-share-off" role="switch"></button></div>';
      box.appendChild(row);
      row.querySelector('#setting-link-share-off').addEventListener('click', async function () {
        const c = cfg(), a = api();
        if (!c || !a) return;
        c.settings = c.settings || {};
        c.settings.link_share_tasks_off = !shareOff();           // through the page's own settings copy: save_settings replaces the whole dict
        injectShare();
        try { await a.save_settings(JSON.stringify(c.settings)); } catch (_) { /* the page keeps it; the next save carries it */ }
      });
    }
    row.querySelector('.sx-label').textContent = t("Don't share tasks with paired computers", '不分享任務給隔壁電腦');
    row.querySelector('.sx-hint').textContent = t(
      "A paired computer's office and board can't see this one's whiteboard; they say it is not open to them.",
      '開啟後，配對的電腦在辦公室和看板上看不到這台的白板，會顯示「這台沒有開放」。');
    const sw = row.querySelector('.toggle-switch');
    sw.classList.toggle('on', shareOff());
    sw.setAttribute('aria-checked', String(shareOff()));
    sw.setAttribute('aria-label', row.querySelector('.sx-label').textContent);
  }

  function boot(tries) {
    const H = host();
    if (!H || !H.cfg() || !api()) {
      if (tries < 240) setTimeout(() => boot(tries + 1), 250);
      return;
    }
    injectSettings();
    placeButton();
    const s = document.getElementById('btn-settings');
    if (s) s.addEventListener('click', () => setTimeout(() => { injectSettings(); placeButton(); }, 0));
  }

  // While the office is open its lobby whiteboard is what the board shortcut opens or closes (office.html's Office.board).
  function toggleBoard(focus) { try { frame.contentWindow.Office.board(focus); } catch (_) {} }
  // The board on a card (`focus.task`: id) or on a paired computer's (`focus.peer`: its id): the board alone when nothing is open (office.html
  // takes the focus once its sheet shows), else the open office's / board's sheet.
  function openBoard(focus) {
    const f = focus && (focus.task != null || focus.peer) ? { task: focus.task != null ? Number(focus.task) : null, peer: focus.peer ? String(focus.peer) : '' } : null;
    if (!overlay) { boardFocus = f; return open({ board: true }); }
    if (f) toggleBoard(f);
    return undefined;
  }
  root.SFOffice = Object.assign({ host: officeHost, open, close, isOpen: () => !!overlay, isBoard: () => !!overlay && boardMode,
                                  openBoard, toggleBoard, refresh: tick, boardRows,
                                  lastSnapshot: () => clone(lastPush), placeButton, peerOpsTiming }, pure);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => boot(0));
  else boot(0);
})(typeof window !== 'undefined' ? window : globalThis);
