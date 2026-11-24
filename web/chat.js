/* ShellBot chat view (settings.ui_style) — see docs/chat-shell.md.
 *
 * A screen over a tab and nothing more. The tab, its process and everything that
 * types into it (sfctl, A2A, Telegram, Frame Link, the phone) stay as they are; the
 * terminal keeps running underneath and comes back exactly as it was left.
 *
 *   reads   chat_view    the phone's data: sfctl `conversation` turns and the
 *                        `_link_decision` question (api_chat.py)
 *   sends   chat_send    the phone's message box (`send` as the person's: goes in while the tab works)
 *           chat_answer  the phone's /link/answer, with the option's own number
 *
 * Words are swapped for display only. An option is answered with the number the CLI
 * printed for it; an option list these rules do not fully know is shown as written,
 * every option of it, because a wrong "Allow" costs more than an English word.
 *
 * RULES (pure; tests_chat_shell.js runs them in node) come first. The screen runs
 * only where there is a document. index.html calls SFChat.onSwitch(sid) at the end
 * of switchTab and asks SFChat.covers() before it forwards a stray Enter to the
 * terminal; web/bots.js asks SFChat.sidebarLine() for a working tab's second line.
 */
(function (root) {
  'use strict';

  // The rules (language, a question and its options, tool summaries, the timeline, preview, safe Markdown) are in
  // web/chat_rules.js (SFChatRules), so a page without this screen can use them; this file re-exports them.
  const RULES = (root && root.SFChatRules) || require('./chat_rules.js');
  const { isZh, CHAT_PROVIDERS, mapDecision, toolSummary, plainDoing, guessProvider, commandNamed, plainName, pastedLabel, buildTimeline, pendingCall, escHtml, inline, md, askContext } = RULES;
  if (typeof module === 'object' && module.exports) module.exports = RULES;
  if (typeof document === 'undefined' || !root) return;

  // ════════════════════════════ the screen ════════════════════════════════
  const host = () => root.__sfHost || null;
  const api = () => (root.pywebview && root.pywebview.api) || null;
  // A tab on another computer is shown the same way: the same calls go to that computer (link_chat →
  // its POST /link/chat → its chat_<action>). Arguments travel by name.
  const REMOTE_ARGS = { view: ['limit', 'rev'], send: ['text', 'clear_draft'], send_now: ['qid'],
                        answer: ['n', 'qid'], login: ['action', 'code'] };
  function call(sid, action, ...args) {
    const a = api();
    const s = (sessions() || {})[sid];
    if (s && s.isRemote) {
      const named = {};
      (REMOTE_ARGS[action] || []).forEach((n, i) => { named[n] = args[i]; });
      return a.link_chat(s.peerId, s.rsid, action, JSON.stringify(named));
    }
    return a['chat_' + action](sid, ...args);
  }
  const remoteOf = sid => { const s = (sessions() || {})[sid]; return s && s.isRemote ? (s.peerName || T('another computer', '另一台電腦')) : ''; };
  const lang = () => (host() && host().lang && host().lang()) || 'en';
  const T = (en, cn) => (isZh(lang()) ? cn : en);
  const sessions = () => (host() && host().sessions && host().sessions()) || {};
  const activeSid = () => (host() && host().active && host().active()) || '';
  const IS_MAC = /MAC/i.test(navigator.platform || navigator.userAgent || '');
  const KEY_LABEL = IS_MAC ? '⌘⇧M' : 'Ctrl+Shift+M';
  const POLL_MS = 1200;
  const HOLD_SENT_MS = 180000;             // the phone's ChatTimeline.holdFor
  const QUEUE_HOLD_MS = 30 * 60000;        // tab_delivery's longest wait
  const LONG_TEXT = 1200;                  // characters before "show all" (the phone's LongText.limit)
  const NO_REPLY_S = 15;                   // seconds of silence after your message before the view says so

  function settings() {
    const h = host();
    const c = h && h.cfg && h.cfg();
    return (c && c.settings) || null;
  }
  // The setting: what a tab opens on. Anything but "chat" is the terminal.
  const globalStyle = () => ((settings() || {}).ui_style === 'chat' ? 'chat' : 'cli');

  // The switch (the button at the top, ⌘⇧M, the view's own Terminal button) changes every tab at once: it is
  // the setting above, saved. Settings → Advanced → "Each tab switches on its own" (view_switch_per_tab, off
  // by default) turns it back to one tab only.
  const perTab = () => (settings() || {}).view_switch_per_tab === true;

  // One rule for the per-tab switch (only used while that option is on, or by setMode): it is kept on that
  // tab (switching tabs does not undo it) until the tab closes or the setting is changed, which puts every
  // tab back on the setting. Kept with the tab's own object, so a reused id starts clean.
  const tabChoice = new Map();
  function modeOf(sid) {
    const e = tabChoice.get(sid);
    if (e && e.obj === sessions()[sid]) return e.mode;
    if (e) tabChoice.delete(sid);
    return globalStyle();
  }

  const views = {};
  function view(sid) {
    return views[sid] || (views[sid] = { rev: '', data: null, provider: '', limit: 120, pending: [],
                                         atBottom: true, open: new Set(), answered: null, note: '' });
  }
  // The name a tab shows in chat style (tab bar, sidebar, the view's header); see commandNamed.
  function label(sid, own) {
    const all = sessions();
    const s = all[sid];
    if (globalStyle() !== 'chat' || !s || s.isRemote || !commandNamed(own, s.cmd)) return own;
    const zh = isZh(lang());
    const base = plainName(s.cmd, zh);
    const order = (host() && host().order && host().order()) || Object.keys(all);
    const same = order.filter(x => all[x] && !all[x].isRemote && commandNamed(all[x].label, all[x].cmd) && plainName(all[x].cmd, zh) === base);
    const i = same.indexOf(sid);
    return i > 0 ? `${base} ${i + 1}` : base;
  }
  function chatCapable(sid) {
    const v = view(sid);
    const s = sessions()[sid];
    const prov = v.provider || (s ? guessProvider(s.cmd) : '');
    return CHAT_PROVIDERS.includes(prov);
  }
  function displayName(s) {
    const sid = Object.keys(sessions()).find(k => sessions()[k] === s) || '';
    return label(sid, String((s && s.label) || '').trim());
  }

  // ── DOM ────────────────────────────────────────────────────────────────
  let layer, list, live, ask, input, sendBtn, noteEl, headEl, notice, sw;
  const ICON_CHAT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3h10a1.5 1.5 0 0 1 1.5 1.5v5A1.5 1.5 0 0 1 13 11H7l-3 2.5V11H3a1.5 1.5 0 0 1-1.5-1.5v-5A1.5 1.5 0 0 1 3 3z"/></svg>';
  const ICON_TERM = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h11v9h-11z"/><path d="M5 7l2 1.5L5 10M8.5 10.5H11"/></svg>';

  function mount() {
    const wrap = document.getElementById('terminal-wrap');
    if (!wrap || layer) return !!layer;
    layer = document.createElement('div');
    layer.id = 'chat-layer';
    layer.hidden = true;
    layer.innerHTML =
      '<div id="chat-head"></div>' +
      '<div id="chat-scroll"><div id="chat-col"><div id="chat-list"></div><div id="chat-live"></div></div></div>' +
      '<div id="chat-ask" hidden></div>' +
      '<div id="chat-compose"><div id="chat-attach" hidden></div><div id="chat-col-in"><textarea id="chat-input" rows="1"></textarea>' +
      '<button id="chat-mic"></button><button id="chat-send"></button></div><div id="chat-note"></div></div>';
    wrap.appendChild(layer);
    headEl = layer.querySelector('#chat-head');
    list = layer.querySelector('#chat-list');
    live = layer.querySelector('#chat-live');
    ask = layer.querySelector('#chat-ask');
    input = layer.querySelector('#chat-input');
    sendBtn = layer.querySelector('#chat-send');
    noteEl = layer.querySelector('#chat-note');
    notice = document.createElement('div');
    notice.id = 'chat-notice';
    notice.hidden = true;
    wrap.appendChild(notice);

    const scroller = layer.querySelector('#chat-scroll');
    scroller.addEventListener('scroll', () => {
      const v = view(activeSid());
      v.atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
    });
    layer.addEventListener('click', onClick);
    notice.addEventListener('click', onClick);
    // Files dropped on the view go through the page's own drop path (index.html), which
    // hands each one to attach() instead of typing it into the CLI. Pasted ones too:
    input.addEventListener('paste', onPaste);
    pickEl = document.createElement('div');
    pickEl.id = 'chat-pick';
    pickEl.hidden = true;
    layer.querySelector('#chat-col-in').appendChild(pickEl);              // over the box, its left edge on the box's
    pickEl.addEventListener('mousedown', e => e.preventDefault());      // the box keeps the caret
    pickEl.addEventListener('click', e => { const it = e.target.closest('[data-i]'); if (it) pickInsert(+it.dataset.i); });
    input.addEventListener('keydown', onPickKey);
    input.addEventListener('input', e => { if (!e.isComposing) pickUpdate(); });
    input.addEventListener('compositionend', () => setTimeout(pickUpdate, 0));
    input.addEventListener('click', pickUpdate);
    input.addEventListener('keyup', e => { if (/^(Arrow(Left|Right)|Home|End)$/.test(e.key)) pickUpdate(); });
    input.addEventListener('blur', () => setTimeout(pickClose, 120));
    input.addEventListener('keydown', e => {
      if (e.key !== 'Enter' || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.isComposing || e.keyCode === 229) return;          // choosing a character, not sending
      e.preventDefault();
      send();
    });
    input.addEventListener('input', autosize);
    sendBtn.addEventListener('click', () => send());
    document.addEventListener('copy', onCopy);
    micBtn = layer.querySelector('#chat-mic');
    micBtn.addEventListener('click', micClick);
    document.addEventListener('keydown', e => {          // Esc while recording: throw the recording away
      if (e.key !== 'Escape' || micState !== 'rec' || !covers()) return;
      e.preventDefault(); e.stopPropagation();
      micCancel();
    }, true);
    // Something focused the terminal hidden under the view (switchTab, a closing
    // dialog): keys would go into the CLI unseen, so the input box takes it instead.
    document.addEventListener('focusin', e => {
      if (!covers()) return;
      const t = e.target;
      if (t && t.classList && t.classList.contains('xterm-helper-textarea') && layer.parentNode.contains(t)) {
        setTimeout(() => { if (covers()) input.focus(); }, 0);
      }
    }, true);
    document.addEventListener('keydown', e => {
      const mod = IS_MAC ? (e.metaKey && !e.ctrlKey) : (e.ctrlKey && !e.metaKey);
      if (!mod || !e.shiftKey || e.altKey || (e.key !== 'm' && e.key !== 'M')) return;
      e.preventDefault(); e.stopPropagation();
      toggle();
    }, true);
    mountSwitch();
    mountSettings();
    mountPerTab();
    mountNewConvo();
    return true;
  }

  function mountSwitch() {
    const bar = document.getElementById('tab-bar');
    if (!bar || document.getElementById('chat-switch')) return;
    sw = document.createElement('div');
    sw.id = 'chat-switch';
    sw.setAttribute('data-guide', 'chat-view');
    sw.innerHTML = `<button data-mode="chat">${ICON_CHAT}<span></span></button>` +
                   `<button data-mode="cli">${ICON_TERM}<span></span></button>`;
    const spacer = bar.querySelector('.tab-spacer');
    bar.insertBefore(sw, spacer ? spacer.nextSibling : null);
    sw.addEventListener('click', e => {
      const b = e.target.closest('button[data-mode]');
      if (b && !b.disabled) switchView(activeSid(), b.dataset.mode);
    });
  }

  // Settings → General: 介面風格 (Interface style), first in the panel.
  function mountSettings() {
    const panel = document.getElementById('settings-panel-general');
    const box = panel && panel.firstElementChild;
    if (!box || document.getElementById('setting-ui-style')) return;
    const row = document.createElement('div');
    row.className = 'cm-setting sx-row';
    row.innerHTML = '<div class="sx-txt"><label class="cm-set-label sx-label"></label><div class="cm-set-hint sx-hint"></div></div>' +
      '<div id="setting-ui-style" class="cm-seg sx-ctl"><button data-style="chat"></button><button data-style="cli"></button></div>';
    box.insertBefore(row, box.firstChild);
    row.addEventListener('click', e => {
      const b = e.target.closest('button[data-style]');
      if (b) setStyle(b.dataset.style);
    });
    const modal = document.getElementById('modal-settings');
    if (modal && root.MutationObserver) {
      new MutationObserver(() => { if (modal.classList.contains('open')) syncSettings(); })
        .observe(modal, { attributes: true, attributeFilter: ['class'] });
    }
    syncSettings();
  }
  // Settings → Advanced: one switch, "each tab switches on its own".
  function mountPerTab() {
    const card = document.querySelector('#settings-panel-advanced .sx-card');
    if (!card || document.getElementById('setting-view-per-tab')) return;
    const row = document.createElement('div');
    row.className = 'sx-row';
    row.innerHTML = '<div class="sx-txt"><div class="sx-label"></div><div class="sx-hint"></div></div>' +
      '<div class="sx-ctl"><button class="toggle-switch" id="setting-view-per-tab" role="switch"></button></div>';
    card.insertBefore(row, card.firstChild);
    row.querySelector('#setting-view-per-tab').addEventListener('click', async () => {
      const st = settings();
      if (!st) return;
      st.view_switch_per_tab = !perTab();
      tabChoice.clear();                       // either way, every tab goes back to the one setting
      syncSettings();
      apply(false);
      try { host().renderSidebar && host().renderSidebar(); host().renderTabs && host().renderTabs(); } catch (_) {}
      try { await api().save_settings(JSON.stringify(st)); } catch (_) { /* the page keeps it; next save carries it */ }
    });
  }
  function syncPerTab() {
    const b = document.getElementById('setting-view-per-tab');
    if (!b) return;
    const row = b.closest('.sx-row');
    row.querySelector('.sx-label').textContent = T('Each tab switches between chat and terminal on its own', '每個分頁各自切換對話／終端機');
    row.querySelector('.sx-hint').textContent = T(
      `Off: the switch at the top and ${KEY_LABEL} change every tab at once, the same as Interface style. On: they change only the tab you are on, and it stays that way until the tab closes or Interface style changes.`,
      `關閉：上方的切換鈕和 ${KEY_LABEL} 會讓全部分頁一起切換，等於改「介面風格」。開啟：只切換目前這個分頁，會一直記在那個分頁上，直到分頁關掉或「介面風格」改了。`);
    b.classList.toggle('on', perTab());
    b.setAttribute('aria-checked', String(perTab()));
    b.setAttribute('aria-label', row.querySelector('.sx-label').textContent);
  }
  function syncSettings() {
    syncPerTab();
    const row = document.querySelector('.cm-setting');
    if (!row) return;
    row.querySelector('.cm-set-label').textContent = T('Interface style', '介面風格');
    const [c, l] = row.querySelectorAll('#setting-ui-style button');
    c.textContent = T('Chat', '對話');
    l.textContent = T('Native CLI', '原生 CLI');
    const style = globalStyle();
    c.classList.toggle('active', style === 'chat');
    l.classList.toggle('active', style === 'cli');
    row.querySelector('.cm-set-hint').textContent = T(
      `What every tab shows, and what a new tab opens on. Chat shows the conversation with buttons for questions; Native CLI shows the terminal as it is. The switch at the top (${KEY_LABEL}) changes this for all tabs at once${perTab() ? ', unless Advanced has each tab switching on its own, which is on now' : ' (Advanced has an option to switch each tab on its own)'}. Claude Code, Codex, OpenCode, Grok, Pi and Cursor tabs have the chat view; Antigravity and terminal tabs always show their terminal.`,
      `全部分頁看到的畫面，新開的分頁也照這個。「對話」顯示對話內容，要你決定的事會變成按鈕；「原生 CLI」就是終端機原本的樣子。上方的切換鈕（或 ${KEY_LABEL}）會讓全部分頁一起切換${perTab() ? '；「進階」裡的「每個分頁各自切換」現在是開著的，切換只影響目前分頁' : '（「進階」裡可以改成每個分頁各自切換）'}。Claude Code、Codex、OpenCode、Grok、Pi、Cursor 分頁有對話畫面；Antigravity 和終端機分頁一律顯示終端機。`);
  }

  async function setStyle(style) {
    const st = settings();
    if (!st || (style !== 'chat' && style !== 'cli')) return;
    st.ui_style = style;
    tabChoice.clear();                          // every tab goes back to the setting
    syncSettings();
    apply(true);                                // at once; the save below only has to keep it
    try { host().renderSidebar && host().renderSidebar(); host().renderTabs && host().renderTabs(); } catch (_) {}
    try { await api().save_settings(JSON.stringify(st)); } catch (_) { /* the page keeps it; next save carries it */ }
  }
  function setMode(sid, mode) {
    const s = sessions()[sid];
    if (!s || (mode !== 'chat' && mode !== 'cli')) return;
    tabChoice.set(sid, { mode, obj: s });
    apply(true);
  }
  // What the person's switch does: every tab (the setting, saved) or, with the Advanced option, this tab only.
  function switchView(sid, mode) {
    if (mode !== 'chat' && mode !== 'cli') return;
    if (perTab()) return setMode(sid, mode);
    return setStyle(mode);
  }
  function toggle() {
    const sid = activeSid();
    if (!sid) return;
    if (perTab()) {
      if (!chatCapable(sid)) return;
      return setMode(sid, modeOf(sid) === 'chat' ? 'cli' : 'chat');
    }
    // all tabs: a tab with no conversation can still send everyone back to the terminals, but "to chat" from it shows nothing
    if (globalStyle() === 'cli' && !chatCapable(sid)) return;
    return setStyle(globalStyle() === 'chat' ? 'cli' : 'chat');
  }

  // ── show / hide ────────────────────────────────────────────────────────
  let shownFor = '';          // the tab the view is showing, '' when the terminal shows
  let lastLang = '';
  function covers() { return !!(layer && !layer.hidden && shownFor && shownFor === activeSid()); }

  // `moveFocus`: the person just switched tabs or modes, so focus goes where they will type.
  function apply(moveFocus) {
    if (!mount()) return;
    const sid = activeSid();
    const s = sessions()[sid];
    const local = !!s;                          // this computer's tab or another's (sfctl link-*): the same view
    const want = local && modeOf(sid) === 'chat';
    const show = want && chatCapable(sid);
    // the guess only says no: ask the server once, or the Chat button stays greyed for good
    if (local && !chatCapable(sid) && !view(sid).provider) refresh(sid, true);
    document.body.classList.toggle('sf-chat-style', globalStyle() === 'chat');
    document.body.classList.toggle('sf-chat-shown', show);
    const was = shownFor;
    layer.hidden = !show;
    shownFor = show ? sid : '';
    renderSwitch(sid, local);
    layout(sid);
    renderNotice(sid, want && !show);
    const mic = document.getElementById('mic-fab');
    layer.classList.toggle('cm-has-mic', !!(mic && getComputedStyle(mic).display !== 'none'));   // position:fixed: no offsetParent
    if (lang() !== lastLang) { lastLang = lang(); syncSettings(); if (show) renderAll(sid); }
    if (show && was !== sid) {
      renderAll(sid);
      refresh(sid, true);
    }
    if (moveFocus || was !== shownFor) {
      if (show) {
        if (!document.querySelector('.modal-overlay.open')) input.focus();
      } else if (was && local && moveFocus) {
        try { host().fitTerm && host().fitTerm(sid); } catch (_) {}
        focusTerminal(sid, s);
      }
    }
  }
  // The terminal is redrawn as the view goes (fitTerm hides its screen for a frame), and a
  // hidden textarea takes no focus: the keys went nowhere. Focus it now and again once the
  // redraw is done, unless the person has moved on by then (another tab, the view back, or
  // focus somewhere of its own, a dialog say: only nowhere or the switch itself is ours).
  function focusTerminal(sid, s) {
    const go = () => {
      const el = document.activeElement;
      // …or still inside the view just hidden (the button that was pressed): WebKit moves focus off a hidden
      // element at once, Chromium (Windows' WebView2) only later, and until then it looked taken (CI caught it)
      const loose = !el || el === document.body || !!(el.closest && (el.closest('#chat-switch') || el.closest('#chat-layer')));
      if (!covers() && activeSid() === sid && loose) { try { s.term.focus(); } catch (_) {} }
    };
    go();
    requestAnimationFrame(() => requestAnimationFrame(go));
  }
  function renderSwitch(sid, local) {
    if (!sw) return;
    const capable = local && chatCapable(sid);
    const mode = local ? modeOf(sid) : 'cli';
    sw.hidden = !local;
    const [c, l] = sw.querySelectorAll('button');
    c.querySelector('span').textContent = T('Chat', '對話');
    l.querySelector('span').textContent = T('Terminal', '終端機');
    c.classList.toggle('active', capable && mode === 'chat');
    l.classList.toggle('active', !capable || mode !== 'chat');
    c.disabled = !capable;
    const all = !perTab();
    c.title = !capable ? T('This tab has no conversation to show: it is a terminal', '這個分頁沒有對話可以顯示：它是終端機')
      : all ? T(`Show all tabs as conversations (${KEY_LABEL})`, `全部分頁看對話（${KEY_LABEL}）`)
      : T(`Show this tab as a conversation (${KEY_LABEL})`, `這個分頁看對話（${KEY_LABEL}）`);
    l.title = all ? T(`Show all tabs as terminals (${KEY_LABEL})`, `全部分頁看終端機（${KEY_LABEL}）`)
      : T(`Show this tab's terminal (${KEY_LABEL})`, `這個分頁看終端機（${KEY_LABEL}）`);
  }

  // ── chat style: no tab bar ───────────────────────────────────────────────
  // The sidebar is the tab list, with 「＋ 新對話」 on top and the bar's other controls (usage,
  // Telegram, loops) at its foot. A tab's terminal gets a header like the view's, holding the
  // Chat/Terminal switch: nothing is laid over the terminal's text, and a tab has one size in
  // both modes, so switching never resizes the CLI. Back in the CLI style all goes home.
  let termHead = null, termLeft = null, dock = null, newBtn = null, lastStyle = '';
  const marks = {};
  function park(el, parent, before) {                  // move el, leaving a mark where it lived
    if (!el || !parent || el.parentNode === parent) return;
    if (!marks[el.id]) { marks[el.id] = document.createComment(el.id); el.parentNode.insertBefore(marks[el.id], el); }
    parent.insertBefore(el, before || null);
  }
  function unpark(el) {
    const m = el && marks[el.id];
    if (m && m.parentNode && el.nextSibling !== m) m.parentNode.insertBefore(el, m);
  }
  function layout(sid) {
    const chat = globalStyle() === 'chat';
    const sb = document.getElementById('sidebar');
    const foot = document.getElementById('sidebar-footer');
    if (chat && sb && !newBtn) {
      newBtn = document.createElement('button');
      newBtn.id = 'sb-new-convo';
      sb.insertBefore(newBtn, sb.firstChild);
      newBtn.addEventListener('click', () => { const b = document.getElementById('btn-new-tab'); if (b) b.click(); });
    }
    if (newBtn) newBtn.innerHTML = SFIcons.html('plus', { cls: 'sfi-lead' }) + ' ' + escHtml(T('New conversation', '新對話'));
    if (chat && foot && !dock) {
      dock = document.createElement('div');
      dock.id = 'sb-dock';
      foot.insertBefore(dock, foot.firstChild);
    }
    if (!termHead) {
      termHead = document.createElement('div');
      termHead.id = 'term-head';
      termHead.innerHTML = '<div class="cm-th-left"></div>';
      termLeft = termHead.firstChild;
      document.getElementById('terminal-wrap').appendChild(termHead);
      termHead.addEventListener('click', onClick);
    }
    for (const id of ['btn-board', 'usage-brief', 'btn-bridge']) {
      const el = document.getElementById(id);
      if (chat) park(el, dock); else unpark(el);
    }
    if (chat) park(sw, termHead); else unpark(sw);
    if (chat && sid) {
      const html = headLeft(sid);
      if (termLeft.dataset.key !== sid + '|' + html) { termLeft.innerHTML = html; termLeft.dataset.key = sid + '|' + html; }
    }
    const style = chat ? 'chat' : 'cli';
    if (lastStyle && style !== lastStyle && sid) {       // the terminal's height changed with the style
      try { host().fitTerm && host().fitTerm(sid); } catch (_) {}
    }
    lastStyle = style;
  }
  // Chat style, but this tab has no conversation the view can draw: say why, once per tab.
  const noticeClosed = new Set();
  function renderNotice(sid, on) {
    if (!notice) return;
    const show = on && !noticeClosed.has(sid);
    notice.hidden = !show;
    if (!show) return;
    const prov = view(sid).provider || guessProvider((sessions()[sid] || {}).cmd);
    if (view(sid).oldPeer) {
      const key = `${sid}|old|${lang()}`;
      if (notice.dataset.key === key) return;
      notice.dataset.key = key;
      notice.innerHTML = `<span>${escHtml(T(`${remoteOf(sid)} runs an older ShellBot: update it to 0.46.1 or later to see its tabs as a conversation. Its terminal shows meanwhile.`,
        `${remoteOf(sid)} 的 ShellBot 比較舊：更新到 0.46.1 以上，才能用對話模式看它的分頁。先顯示終端機。`))}</span>` +
        `<button data-act="notice-close" title="${escHtml(T('Hide', '收起'))}">${SFIcons.html('close')}</button>`;
      return;
    }
    const known = { opencode: 'OpenCode', grok: 'Grok Build', pi: 'Pi', cursor: 'Cursor Agent', agy: 'Antigravity' }[prov];
    const key = `${sid}|${prov}|${lang()}`;
    if (notice.dataset.key === key) return;
    notice.dataset.key = key;
    notice.innerHTML = `<span>${escHtml(prov === 'agy'
      ? T("Antigravity keeps its conversation in a binary format nothing here can read yet, so this tab shows its terminal.",
          'Antigravity 的對話存成二進位格式，這裡還讀不到，所以這個分頁顯示終端機。')
      : known
      ? T(`${known}'s conversation could not be read, so this tab shows its terminal.`,
          `讀不到 ${known} 的對話記錄，所以這個分頁顯示終端機。`)
      : T('This is a terminal tab: there is no conversation to show, so it stays a terminal.',
          '這是終端機分頁，沒有對話可以整理，所以直接顯示終端機。'))}</span>` +
      `<button data-act="notice-close" title="${escHtml(T('Hide', '收起'))}">${SFIcons.html('close')}</button>`;
  }

  // ── data ───────────────────────────────────────────────────────────────
  async function refresh(sid, force) {
    const a = api();
    if (!a || !a.chat_view || !sid) return;
    const v = view(sid);
    if (v.loading) return;
    v.loading = true;
    try {
      const r = JSON.parse(await call(sid, 'view', v.limit, force ? '' : v.rev));
      if (r && r.code === 'old_peer') {          // a computer whose ShellBot has no /link/chat: its terminal, and why
        v.oldPeer = true;
        if (!v.provider) v.provider = 'old_peer';
        apply();
        return;
      }
      if (!r || r.success === false) return;
      if (r.unchanged) return;
      v.rev = r.rev;
      v.data = r;
      if (r.provider) v.provider = r.provider;
      if (v.answered && (!r.decision || r.decision.id !== v.answered.id)) v.answered = null;
      if (sid === shownFor) renderAll(sid);
      else apply();
    } catch (_) { /* the next tick asks again */ } finally { v.loading = false; }
  }

  // ── drawing ────────────────────────────────────────────────────────────
  function peerFace(who) {
    let svg = '';
    try { svg = (root.SFBots && root.SFBots.faceForName && root.SFBots.faceForName(who)) || ''; } catch (_) { svg = ''; }
    return `<span class="cm-face cm-peer-face">${svg}</span>`;
  }
  function faceHtml(sid, state) {
    const B = root.SFBots;
    let svg = '';
    try {
      svg = (B && B.enabled && B.enabled() && B.face(sid)) ||
            (B && B.faceSvg ? B.faceSvg(B.defaultAvatar(sid), { seed: sid }) : '');
    } catch (_) { svg = ''; }
    const cls = { working: 'sig-working', decision: 'sig-decision', stuck: 'sig-stuck', done: 'sig-done' }[state] || '';
    return `<span class="cm-face busy-dot ${cls}">${svg}</span>`;
  }
  function stateOf(sid) {
    const s = sessions()[sid] || {};
    const st = s._serverStatus || {};
    const v = view(sid).data || {};
    return st.state || v.activity || '';
  }

  function stickBottom(sid) {
    if (!view(sid).atBottom) return;
    const sc = layer.querySelector('#chat-scroll');
    sc.scrollTop = sc.scrollHeight;
  }
  function renderAll(sid) {
    renderAttach(sid);
    renderHead(sid);
    renderList(sid);
    renderLive(sid);
    renderAsk(sid);
    stickBottom(sid);
    renderHint(sid);
    sendBtn.textContent = T('Send', '送出');
    renderMic();
    noteEl.textContent = view(sid).note || '';
  }
  // The box's hint names the tab (or the bot). It follows the name as it changes: it was set only
  // when the view was drawn for a tab, so after a rename it kept the old name until the tab was left
  // and come back to. Written only when it differs, so a person's focus or selection is not touched.
  function renderHint(sid) {
    const name = displayName(sessions()[sid]);
    const away = remoteOf(sid);
    const hint = away ? T(`Message ${name} on ${away} — Enter to send, Shift+Enter for a new line`,
                          `傳訊息給 ${away} 上的 ${name}（Enter 送出，Shift+Enter 換行）`)
      : T(`Message ${name} — Enter to send, Shift+Enter for a new line`,
          `傳訊息給 ${name}（Enter 送出，Shift+Enter 換行）`);
    if (input.placeholder !== hint) input.placeholder = hint;
  }
  // A tab was renamed (the dialog, the app's push, a bot's rename): what shows its name follows now,
  // not at the next tick.
  function relabel() {
    if (!layer || !shownFor) return;
    renderHead(shownFor);
    renderHint(shownFor);
  }
  // What a header says under the tab's name: what it is doing, or that it is waiting or ready.
  function stateWord(sid) {
    const s = sessions()[sid] || {};
    const state = stateOf(sid);
    const d = plainDoing(s._serverStatus, lang());
    const asks = !!((view(sid).data || {}).decision);
    return d.text ? (d.detail ? `${d.text} ${d.detail}` : d.text)
      : (asks ? T('Waiting for you', '等你決定') : ({ done: T('Ready', '可以說話'), idle: T('Ready', '可以說話') }[state] || ''));
  }
  // A header's left end: in chat style the sidebar's ☰ (the tab bar is gone), then the face, the
  // name and stateWord. With bots on the face opens this conversation's settings.
  function headLeft(sid) {
    const s = sessions()[sid] || {};
    const state = stateOf(sid);
    const B = root.SFBots;
    const face = B && B.enabled && B.enabled() && B.openSettings
      ? `<button class="cm-face-btn" data-act="settings" data-sfb-settings title="${escHtml(T(
          "This conversation's settings: face, name, bot", '這個對話的設定：頭像、名稱、Bot'))}">${faceHtml(sid, state)}</button>`
      : faceHtml(sid, state);
    const side = globalStyle() === 'chat'
      ? `<button class="cm-side-btn" data-act="sidebar" title="${escHtml(T('Show or hide the sidebar', '顯示或收起側欄'))}">${SFIcons.html('menu')}</button>` : '';
    const away = remoteOf(sid);
    const chip = away ? `<span class="cm-remote" title="${escHtml(T(`This tab runs on ${away}, not on this computer`,
      `這個分頁在 ${away} 上跑，不是這台電腦`))}">${escHtml(T('Remote', '遠端'))} · ${escHtml(away)}</span>` : '';
    return `${side}${face}<div class="cm-who"><span class="cm-name-row"><b>${escHtml(displayName(s))}</b>${chip}</span>` +
      `<span class="cm-state">${escHtml(stateWord(sid))}</span></div>${root.SFTabModel ? root.SFTabModel.chipHtml(sid) : ''}`;   // web/tabmodel.js
  }
  function renderHead(sid) {
    const html = headLeft(sid) +
      `<button class="cm-to-term" data-act="to-term" title="${escHtml(T(`Show the terminal (${KEY_LABEL})`, `看終端機（${KEY_LABEL}）`))}">` +
      `${ICON_TERM}<span>${escHtml(T('Terminal', '終端機'))}</span></button>`;
    if (headEl.dataset.key !== sid + '|' + html) { headEl.innerHTML = html; headEl.dataset.key = sid + '|' + html; }
  }

  function renderList(sid) {
    const v = view(sid);
    const data = v.data;
    const scroller = layer.querySelector('#chat-scroll');
    const atBottom = v.atBottom;
    if (!data) {
      list.innerHTML = `<div class="cm-empty">${escHtml(T('Reading the conversation…', '正在讀對話…'))}</div>`;
      listHtml = '';
      return;
    }
    const red = !!(data.decision && data.decision.id && String(data.decision.id) && data.activity !== 'decision');
    const items = buildTimeline(data.turns, { foldRed: red });
    // What was sent from here and is not in the record yet stays on screen (the phone's rule).
    // A queued one follows the server's queue (chat_view `queue`): while its id waits it says
    // why; once given up it says "not sent" and stays until resent or removed, never just
    // vanishing; once it leaves the queue it went in, and waits for the record like any send.
    const now = Date.now();
    const q = data.queue || {};
    const waiting = new Set(q.queued || []);
    v.busy = q.busy || null;                     // what holds them now, and since when (waitWords)
    const gone = new Map((q.given_up || []).map(g => [g.id, g]));
    v.pending.forEach(p => {
      if (!p.id || p.failed !== undefined) return;
      if (gone.has(p.id)) { p.failed = String(gone.get(p.id).why || ''); note(''); }   // the "it is busy" line no longer holds
      else if (waiting.has(p.id)) p.seen = true;
      // Gone from a queue that listed it: it went in. Not before it was listed: the view's
      // data can be older than the send (measured: the bubble said "sending" at once).
      else if (p.queued && p.seen) { p.queued = false; p.at = now; }
    });
    const held = v.pending.find(p => p.queued && p.failed === undefined);
    if (v.note && v.note === v.waitNote && sid === activeSid())        // the minutes go on; once it went in, the line goes
      note(v.waitNote = held ? waitWords(held, true, sid) : '');
    v.pending = v.pending.filter(p => p.failed !== undefined ||
      (now - p.at < (p.queued ? QUEUE_HOLD_MS : HOLD_SENT_MS) &&
       !items.some(it => it.type === 'user' && it.text.includes(String(p.key || p.text).trim()))));
    let html = '';
    if ((data.turns || []).length >= v.limit && v.limit < 400) {
      html += `<button class="cm-earlier" data-act="earlier">${escHtml(T('Show earlier messages', '載入更早的對話'))}</button>`;
    }
    if (!data.readable) {
      html += `<div class="cm-empty"><b>${escHtml(T('No conversation yet', '還沒有對話'))}</b>` +
        `<span>${escHtml(T('Say something below to start. If nothing shows up here after it answers, its record could not be read: switch to the terminal to see it.',
                           '從下面開始說吧。它回答之後這裡還是空的話，代表讀不到它的對話記錄，可以切到終端機看。'))}</span></div>`;
    }
    items.forEach((it, idx) => { html += itemHtml(sid, it, idx, items); });
    // The last word is yours, it is not working and nothing came back: some failures
    // (measured: Codex refusing a model it is too old for) are only on the screen.
    const last = items[items.length - 1];
    v.lastUserTs = last && last.type === 'user' ? last.ts || 0 : 0;
    v.noReply = noReplyDue(sid);
    if (v.noReply) {
      html += `<div class="cm-row cm-noreply"><span class="cm-face-gap"></span><div>${escHtml(T(
        'No answer came back. If something went wrong, the terminal shows it.', '它沒有回覆。如果出了錯，終端機上看得到。'))}` +
        ` <button class="cm-more-text cm-inline" data-act="to-term">${escHtml(T('Open the terminal', '看終端機'))}</button></div></div>`;
    }
    v.pending.forEach(p => {
      const att = (p.files || []).length ? `<div class="cm-meta">${escHtml(T('Attached: ', '附件：') + p.files.map(f => baseName(f.path)).join('、'))}</div>` : '';
      const meta = p.failed !== undefined
        ? `<div class="cm-meta cm-failed">${escHtml(p.failed ? T(`Not sent: ${p.failed}`, `沒送出：${p.failed}`) : T('Not sent', '沒送出'))}` +
          ` <button class="cm-more-text cm-inline" data-act="resend" data-key="${escHtml(p.uid)}">${escHtml(T('Send again', '重送'))}</button>` +
          ` <button class="cm-more-text cm-inline" data-act="dismiss" data-key="${escHtml(p.uid)}">${escHtml(T('Remove', '移除'))}</button></div>`
        : `<div class="cm-meta">${escHtml(p.queued ? waitWords(p, false, sid) : T('Sending…', '送出中…'))}</div>` +
          (p.queued && p.id ? `<div class="cm-now"><span>${escHtml(NOW_TIP())}</span>` +
            `<button class="cm-now-btn" data-act="send-now" data-key="${escHtml(p.uid)}">${escHtml(T('Send now', '插隊送出'))}</button></div>` : '');
      html += `<div class="cm-row cm-user is-pending${p.failed !== undefined ? ' is-failed' : ''}"><div class="cm-bubble">${md(p.text, isZh(lang()))}</div>${att}${meta}</div>`;
    });
    if (listHtml !== sid + html) {
      const keep = scroller.scrollTop;
      list.innerHTML = html;
      decoratePaths(list);
      listHtml = sid + html;
      if (atBottom) scroller.scrollTop = scroller.scrollHeight;
      else scroller.scrollTop = keep;
    }
  }
  let listHtml = '';
  function noReplyDue(sid) {
    const v = view(sid);
    const data = v.data || {};
    return !!(v.lastUserTs && !v.pending.length && !data.decision && !['working', 'decision'].includes(stateOf(sid)) &&
              Date.now() / 1000 - v.lastUserTs > NO_REPLY_S);
  }
  const itemKey = (it, idx) => `${it.type}:${it.ts || 0}:${idx}`;
  function longText(sid, key, text, cls, markdown) {
    const v = view(sid);
    const long = text.length > LONG_TEXT && !v.open.has(key);
    const shown = long ? text.slice(0, LONG_TEXT).replace(/\s+\S*$/, '') + ' …' : text;
    let copy = '';
    if (cls === 'cm-bubble') {             // the whole message, even while it shows only its start
      (v.copy || (v.copy = {}))[key] = text.replace(/\u2060/g, '');
      copy = `<button class="cm-copy" data-act="copy" data-key="${escHtml(key)}" data-label="${escHtml(T('Copy', '複製'))}" ` +
        `aria-label="${escHtml(T('Copy this message', '複製這則訊息'))}" title="${escHtml(T('Copy this message', '複製這則訊息'))}"></button>`;
    }
    return `<div class="${cls}">${copy}${markdown ? md(shown, isZh(lang())).replace(/⏳\u2060/g, SFIcons.html('hourglass', { cls: 'sfi-lead' })) : escHtml(shown)}` +
      (text.length > LONG_TEXT ? `<button class="cm-more-text" data-act="open" data-key="${escHtml(key)}">${escHtml(
        v.open.has(key) ? T('Show less', '收合') : T(`Show all (${text.length} characters)`, `顯示全部（${text.length} 字）`))}</button>` : '') +
      '</div>';
  }
  // Something the person pasted among their own words: one folded block in their bubble, how many
  // lines and how it starts; the text itself, as it was pasted, one click away.
  function pasteHtml(sid, key, text) {
    const open = view(sid).open.has(key);
    const n = text.split('\n').length;
    const peek = (text.split('\n').find(l => l.trim()) || '').trim();
    return `<div class="cm-paste${open ? ' is-open' : ''}"><button class="cm-paste-sum" data-act="open" data-key="${escHtml(key)}">` +
      `<span class="cm-caret">${SFIcons.html(open ? 'chev-down' : 'chev-right')}</span><span>${escHtml(pastedLabel(n, isZh(lang())))}</span>` +
      (open ? '' : `<span class="cm-paste-peek">${escHtml(peek)}</span>`) + '</button>' +
      (open ? `<div class="cm-paste-text">${escHtml(text)}</div>` : '') + '</div>';
  }
  function itemHtml(sid, it, idx, items) {
    const v = view(sid);
    const key = itemKey(it, idx);
    if (it.type === 'user') {
      const tag = it.tags.map(t => t.kind === 'telegram' ? T('from Telegram', '來自 Telegram')
        : T(`from ${t.who || 'another agent'}`, `來自 ${t.who || '另一個分頁'}`)).join(' · ');
      const bubble = it.parts
        ? `<div class="cm-bubble">${it.parts.map((p, n) => p.paste !== undefined ? pasteHtml(sid, `${key}:paste${n}`, p.paste)
                                                                                : md(p.text, isZh(lang()))).join('')}</div>`
        : longText(sid, key, it.text, 'cm-bubble', true);
      return `<div class="cm-row cm-user">${bubble}` + (tag ? `<div class="cm-meta">${escHtml(tag)}</div>` : '') + '</div>';
    }
    if (it.type === 'peer') {
      // Another agent: on the left with its own face and name, its words in a bubble of
      // their own colour; the same sender again shows them once. A message the chain limit
      // stopped (it came as data only) carries a small tag, on every such bubble.
      const prev = items[idx - 1];
      const again = !!prev && prev.type === 'peer' && prev.who === it.who;
      const name = again ? '' : `<div class="cm-peer-name">${escHtml(it.who || T('Another agent', '另一個 AI'))}</div>`;
      const tag = it.blocked
        ? `<span class="cm-peer-tag" title="${escHtml(T('Stopped by the chain limit: the agent that got it was told it is for reference only and not to pass it on',
                                                       '轉發層數超過上限：收到的一方被告知這只供參考，不要再轉發'))}">` +
          `${escHtml(T('not passed on · for reference', '未轉達・只供參考'))}</span>`
        : '';
      return `<div class="cm-row cm-peer${it.blocked ? ' is-blocked' : ''}" data-who="${escHtml(it.who || '')}">${again ? '<span class="cm-face-gap"></span>' : peerFace(it.who)}` +
        `<div class="cm-peer-col">${name || tag ? `<div class="cm-peer-head">${name}${tag}</div>` : ''}` +
        `${longText(sid, key, it.text, 'cm-bubble', true)}</div></div>`;
    }
    if (it.type === 'bot') {
      let j = idx - 1;
      while (j >= 0 && items[j].type === 'tools') j--;
      const face = j < 0 || items[j].type !== 'bot' ? faceHtml(sid, '') : '<span class="cm-face-gap"></span>';
      return `<div class="cm-row cm-bot">${face}${longText(sid, key, it.text, 'cm-bubble', true)}</div>`;
    }
    if (it.type === 'tools') {
      const open = v.open.has(key);
      const rows = it.calls.map(c => `<div class="cm-call"><span class="cm-call-tool">${escHtml(c.tool)}</span>` +
        `<span class="cm-call-target">${escHtml(c.target)}</span></div>`).join('');
      return `<div class="cm-row cm-tools${open ? ' is-open' : ''}"><span class="cm-face-gap"></span><div class="cm-tools-box">` +
        `<button class="cm-tools-sum" data-act="open" data-key="${escHtml(key)}"><span class="cm-caret">${SFIcons.html(open ? 'chev-down' : 'chev-right')}</span>` +
        `${escHtml(toolSummary(it.calls, lang()))}</button>` +
        (open ? `<div class="cm-calls">${rows}</div>` : '') + '</div></div>';
    }
    if (it.type === 'compact') {
      const open = v.open.has(key);
      return `<div class="cm-row cm-compact${open ? ' is-open' : ''}"><button class="cm-compact-sum" data-act="open" data-key="${escHtml(key)}">` +
        `<span class="cm-caret">${SFIcons.html(open ? 'chev-down' : 'chev-right')}</span>${escHtml(T('Earlier messages were folded into a summary', '較早的對話已整理成摘要'))}</button>` +
        (open ? `<div class="cm-compact-text">${md(it.text, isZh(lang()))}</div>` : '') + '</div>';
    }
    if (it.type === 'memory') {
      const live = idx === items.length - 1 && !!v.data && v.data.activity === 'working';     // still writing
      return `<div class="cm-row cm-memory"><span class="cm-memory-line">${ICON_MEMORY}` +
        `<span>${escHtml(live ? T('Saving memory…', '正在更新記憶…') : T('Memory updated', '已更新記憶'))}</span></span></div>`;
    }
    if (it.type === 'file') {
      return `<div class="cm-row cm-user cm-sent-file"><span class="cm-file" title="${escHtml(it.path)}">${ICON_FILE}` +
        `<span>${escHtml(baseName(it.path))}</span></span></div>`;
    }
    if (it.type === 'error') {
      const times = it.times > 1 ? T(` (${it.times} times)`, `（${it.times} 次）`) : '';
      return `<div class="cm-row cm-error"><span class="cm-face-gap"></span><div>${escHtml(T('Error: ', '出錯了：') + it.text + times)}</div></div>`;
    }
    return '';
  }
  // The live line under the last message while it works: its face and one plain line.
  function renderLive(sid) {
    const s = sessions()[sid] || {};
    const state = stateOf(sid);
    const d = plainDoing(s._serverStatus, lang());
    const html = state === 'working'
      ? `<div class="cm-row cm-live">${faceHtml(sid, 'working')}<div class="cm-live-text"><span>${escHtml(d.text || T('Working…', '正在做…'))}</span>` +
        (d.detail ? `<code>${escHtml(d.detail)}</code>` : '') + '<i class="cm-dots"><b></b><b></b><b></b></i></div></div>'
      : '';
    if (live.dataset.html !== html) {
      live.innerHTML = html;
      live.dataset.html = html;
      stickBottom(sid);
    }
  }
  function renderAsk(sid) {
    const v = view(sid);
    const data = v.data || {};
    const d = data.decision;
    let html = '';
    const m = d && (d.options || []).length ? mapDecision(d, lang()) : null;
    // ✕ puts the card away for this question (handled in the terminal, or the state is late); the next
    // question shows again, and so does this one once the tab is no longer waiting
    const askKey = m ? 'q:' + m.id : (data.activity === 'decision' ? 'term' : '');
    if (!askKey) v.askDismissed = '';
    // [[SF:RED:connect:<service>]] (decision.tag): web/connect.js draws the card, "" for any other question
    const conn = d && root.SFConnect ? root.SFConnect.chatCard(d, { face: faceHtml(sid, 'decision'), options: m ? m.options : [],
      answered: v.answered && v.answered.id === d.id ? T(`Sent: ${v.answered.label}`, `已送出：${v.answered.label}`) : '' }) : '';
    if (askKey && v.askDismissed === askKey) {
      // put away
    } else if (conn) {
      html = conn;
    } else if (m) {
      const call = data.activity === 'decision' ? pendingCall(data.turns) : null;
      const answered = v.answered && v.answered.id === d.id;
      html = `<div class="cm-ask-card">${faceHtml(sid, 'decision')}<div class="cm-ask-body">` + `<button class="cm-ask-x" data-act="ask-dismiss" data-key="${escHtml(askKey)}" title="${escHtml(T('Handled: put it away', '已處理，收起來'))}">${SFIcons.html('close')}</button>` +
        `<div class="cm-ask-title">${escHtml(T('Your call', '要你決定'))}</div>` +
        `<div class="cm-ask-q">${escHtml(m.questionTranslated || !m.translated ? m.question : T('It is asking before it goes on.', '它在繼續之前先問你。'))}</div>` +
        askContext(d, m, call, lang()) +
        `<div class="cm-opts">${m.options.map(o => `<button class="cm-opt${o.tone ? ' tone-' + o.tone : ''}" data-act="answer" ` +
          `data-n="${escHtml(o.n)}" data-id="${escHtml(m.id)}"${answered ? ' disabled' : ''} title="${escHtml(
            T(`Option ${o.n}: ${o.raw}`, `選項 ${o.n}：${o.raw}`))}">${escHtml(o.label)}</button>`).join('')}</div>` +
        (answered ? `<div class="cm-ask-note">${escHtml(T(`Sent: ${v.answered.label}`, `已送出：${v.answered.label}`))}</div>` : '') +
        (!m.translated ? `<div class="cm-ask-note">${escHtml(T('Shown as the CLI wrote it.', '照 CLI 的原文顯示。'))}</div>` : '') +
        '</div></div>';
    } else if (data.activity === 'decision') {
      html = `<div class="cm-ask-card">${faceHtml(sid, 'decision')}<div class="cm-ask-body">` + `<button class="cm-ask-x" data-act="ask-dismiss" data-key="${escHtml(askKey)}" title="${escHtml(T('Handled: put it away', '已處理，收起來'))}">${SFIcons.html('close')}</button>` +
        `<div class="cm-ask-title">${escHtml(T('It is waiting for you in the terminal', '它在終端機上等你決定'))}</div>` +
        `<div class="cm-ask-q">${escHtml(T('This screen could not read the choices, so nothing is answered from here.',
                                            '這個畫面讀不到選項，所以不會從這裡替你選。'))}</div>` +
        `<div class="cm-opts"><button class="cm-opt" data-act="to-term">${escHtml(T('Open the terminal', '切到終端機處理'))}</button></div></div></div>`;
    }
    // Signing in again, step by step, when the tab's sign-in expired (web/tablogin.js, tab_login.py)
    html = (root.SFLogin ? root.SFLogin.card(sid, data, faceHtml(sid, '')) : '') + draftCardHtml(sid) + html;
    ask.hidden = !html;
    if (ask.dataset.html !== html) { ask.innerHTML = html; ask.dataset.html = html; stickBottom(sid); }
  }

  // ── attachments: the page's own save calls, the paths in the message ─────
  const baseName = p => String(p || '').split(/[\\/]/).pop();
  const ICON_MEMORY = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-6.5"/></svg>';
  const ICON_FILE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 1.5h5l3 3v10H4z"/><path d="M9 1.5v3h3"/></svg>';
  function attach(path, dataUrl) {
    const sid = shownFor;
    if (!sid || !path || /^ERROR/.test(String(path))) return;
    const v = view(sid);
    v.files = v.files || [];
    if (!v.files.some(f => f.path === String(path))) {
      v.files.push({ path: String(path), dataUrl: /^data:image\//.test(dataUrl || '') ? dataUrl : '' });
    }
    renderAttach(sid);
    input.focus();
  }
  function renderAttach(sid) {
    const box = layer && layer.querySelector('#chat-attach');
    if (!box) return;
    const files = view(sid).files || [];
    box.hidden = !files.length;
    box.innerHTML = files.map((f, i) => `<span class="cm-file" title="${escHtml(f.path)}">` +
      (f.dataUrl ? `<img src="${escHtml(f.dataUrl)}" alt="">` : ICON_FILE) +
      `<span>${escHtml(baseName(f.path))}</span><button data-act="unattach" data-i="${i}" title="${escHtml(T('Remove', '移除'))}">${SFIcons.html('close')}</button></span>`).join('');
  }
  const readDataUrl = blob => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(blob);
  });
  // A picture or a file pasted into the box: saved by the same calls the terminal's paste
  // uses (save_image, Finder's own paths, save_file_from_clipboard). Text pastes as text.
  async function onPaste(e) {
    const cd = e.clipboardData;
    if (!shownFor || !cd) return;
    const items = Array.from(cd.items || []);
    const images = items.filter(it => it.kind === 'file' && /^image\//.test(it.type)).map(it => it.getAsFile()).filter(Boolean);
    if (!images.length && !Array.from(cd.types || []).includes('Files')) return;
    e.preventDefault();
    e.stopPropagation();
    const a = api();
    try {
      if (images.length) {
        for (const blob of images) { const url = await readDataUrl(blob); attach(await a.save_image(url), url); }
        return;
      }
      const paths = JSON.parse((await a.get_clipboard_files()) || '[]');
      if (paths.length) { paths.forEach(path => attach(path, '')); return; }
      for (const it of items) {
        const blob = it.kind === 'file' ? it.getAsFile() : null;
        if (blob && blob.size > 0) attach(await a.save_file_from_clipboard(await readDataUrl(blob), blob.name || 'file'), '');
      }
    } catch (err) { note(String(err), true); }
  }

  // ── "＋" in chat style: a new conversation, by who to talk to ──────────────
  // The CLI list (and a typed command) stays, folded under "Advanced"; in Native CLI
  // style the dialog is as it was.
  const PURPOSE = {
    claude: ['Code, documents, research: anything', '寫程式、改文件、整理資料，什麼都能聊'],
    codex: ['Code and tests; good for a second opinion', '寫程式、跑測試，適合請它給第二意見'],
    opencode: ['A coding assistant that can also run open models', '也能用開源模型的程式助手'],
    grok: ["xAI's coding assistant", 'xAI 的程式助手'],
    pi: ['A small, quick coding assistant', '輕巧的程式助手'],
    cursor: ["Cursor's coding assistant", 'Cursor 的程式助手'],
    agy: ["Google's coding assistant (no chat view yet: it opens on its terminal)", 'Google 的程式助手（還沒有對話畫面，會顯示終端機）'],
  };
  let newConvo = { ai: [], bots: [] };
  function mountNewConvo() {
    const modal = document.getElementById('modal-new');
    const box = modal && modal.querySelector('.modal');
    if (!box || document.getElementById('chat-new')) return;
    const el = document.createElement('div');
    el.id = 'chat-new';
    box.insertBefore(el, box.children[1] || null);
    el.addEventListener('click', onNewClick);
    if (root.MutationObserver) {
      new MutationObserver(() => { if (modal.classList.contains('open')) renderNewConvo(); })
        .observe(modal, { attributes: true, attributeFilter: ['class'] });
    }
  }
  function aiFace(provider) {
    const B = root.SFBots;
    try { return B && B.faceSvg ? B.faceSvg(B.defaultAvatar('ai:' + provider), { seed: provider }) : ''; } catch (_) { return ''; }
  }
  async function renderNewConvo() {
    const box = document.querySelector('#modal-new .modal');
    const el = document.getElementById('chat-new');
    if (!box || !el) return;
    const h2 = box.querySelector('h2');
    const chat = globalStyle() === 'chat';
    box.classList.toggle('cm-newconvo', chat);
    box.classList.remove('cm-adv-open');
    if (h2 && !h2.dataset.cmOrig) h2.dataset.cmOrig = h2.textContent;
    if (!chat) { el.innerHTML = ''; if (h2) h2.textContent = h2.dataset.cmOrig; return; }
    if (h2) h2.textContent = T('New conversation', '新對話');
    let groups = [];
    try { groups = JSON.parse(await api().preset_groups()) || []; } catch (_) { groups = []; }
    const ai = groups.filter(g => g && g.provider && (g.items || []).length);
    let bots = [];
    try {
      const st = JSON.parse(await api().bots_state());
      if (st && st.enabled) {
        bots = Object.entries(st.roster || {}).filter(([, e]) => e && e.bot && !e.bot.external)
          .map(([role, e]) => ({ role, label: e.label || role, resp: e.responsibility || '', avatar: e.bot.avatar }));
      }
    } catch (_) { bots = []; }
    newConvo = { ai, bots };
    const B = root.SFBots;
    let html = `<div class="cm-new-sec">${escHtml(T('Talk to', '找誰聊'))}</div><div class="cm-new-list">`;
    ai.forEach((g, i) => {
      const why = (PURPOSE[g.provider] || ['', ''])[isZh(lang()) ? 1 : 0];
      html += `<button class="cm-new-item${i === 0 ? ' is-first' : ''}" data-act="new-ai" data-g="${i}">` +
        `<span class="cm-new-face">${aiFace(g.provider)}</span><span class="cm-new-txt"><b>${escHtml(g.title)}` +
        (i === 0 ? `<i>${escHtml(T('default', '預設'))}</i>` : '') + `</b><span>${escHtml(why)}</span></span></button>`;
    });
    html += '</div>' + `<div class="cm-new-note">${escHtml(T('A new conversation is a temporary bot: it forgets when it closes. Keep it (the button beside its name) and it remembers.',
      '新的對話是臨時 bot：關掉就忘了。按名字旁的「記住」，它就會記得。'))}</div>`;     // plan A
    if (bots.length) {
      html += `<div class="cm-new-sec">${escHtml(T('Hand it to a bot', '交給 bot'))}</div><div class="cm-new-list">` +
        bots.map((b, i) => `<button class="cm-new-item" data-act="new-bot" data-b="${i}"><span class="cm-new-face">` +
          `${B && B.faceSvg ? B.faceSvg(b.avatar, { seed: b.role }) : ''}</span><span class="cm-new-txt"><b>${escHtml(b.label)}</b>` +
          `<span>${escHtml(b.resp)}</span></span></button>`).join('') + '</div>';
    }
    html += `<button class="cm-new-adv" data-act="new-adv"><span class="cm-caret">${SFIcons.html('chev-right')}</span>${escHtml(T('Advanced: pick a CLI or type a command', '進階：選 CLI 或自訂指令'))}</button>`;
    el.innerHTML = html;
  }
  function waitAndSwitch(sid, tries) {
    if (sessions()[sid]) { host().switchTab(sid); return; }
    if ((tries || 0) < 40) setTimeout(() => waitAndSwitch(sid, (tries || 0) + 1), 250);
  }
  async function onNewClick(e) {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const box = document.querySelector('#modal-new .modal');
    if (b.dataset.act === 'new-adv') {
      const open = box.classList.toggle('cm-adv-open');
      b.querySelector('.cm-caret').innerHTML = SFIcons.html(open ? 'chev-down' : 'chev-right');
      const list = document.getElementById('preset-list');
      if (open && list && list.scrollIntoView) list.scrollIntoView({ block: 'start', behavior: 'smooth' });
      return;
    }
    if (b.dataset.act === 'new-ai') {
      const g = newConvo.ai[+b.dataset.g];
      const item = g && ((g.items || []).find(it => !it.variant) || g.items[0]);
      if (!item) return;
      host().closeModal();
      host().openSession(item.cmd, item.name, { askName: false });
      return;
    }
    if (b.dataset.act === 'new-bot') {
      const bot = newConvo.bots[+b.dataset.b];
      if (!bot) return;
      host().closeModal();
      try {
        const r = JSON.parse(await api().bots_open(bot.role));
        const sid = r && r.details && r.details.sid;
        if (sid) waitAndSwitch(sid);
      } catch (_) { /* the tab may still come; the sidebar shows it */ }
    }
  }

  // What the pending call works on, named the way a person would.
  // ── actions ────────────────────────────────────────────────────────────
  function note(text, warn) {
    const sid = activeSid();
    if (sid) view(sid).note = text || '';
    if (noteEl) { noteEl.textContent = text || ''; noteEl.classList.toggle('warn', !!warn); }
  }
  function autosize() {
    input.style.height = 'auto';
    // The height is border-box: add the borders, or a single line overflows by 2px and shows a bar.
    input.style.height = Math.min(input.scrollHeight + input.offsetHeight - input.clientHeight, 180) + 'px';
  }

  // ── copying ──────────────────────────────────────────────────────────────
  // A selection in the conversation as plain text, the way a chat app copies: a list item as
  // "• text" or "1. text", a code block as its lines, blocks on lines of their own, messages
  // apart; no buttons, faces, names or labels. ⌘C (the Mac menu's copy:) and Ctrl+C (WebView2)
  // both raise the page's copy event; the input box keeps its own copy.
  function plainOf(node) {
    let out = '';
    const nl = () => { if (out && !out.endsWith('\n')) out += '\n'; };
    const walk = n => {
      if (n.nodeType === 3) { out += n.nodeValue; return; }
      if (n.nodeType === 1) {
        if (n.matches('button, svg, .cm-code-lang, .cm-meta, .cm-face, .cm-face-gap, .cm-peer-name')) return;
        if (n.tagName === 'BR') { out += '\n'; return; }
        if (n.matches('.cm-mark')) { out += n.textContent + ' '; return; }
        if (n.matches('td, th') && n.previousElementSibling) out += '\t';
      } else if (n.nodeType !== 11) return;
      const block = n.nodeType === 1 && n.matches('p, div, blockquote, pre, tr');
      if (block) nl();
      n.childNodes.forEach(walk);
      if (block) nl();
      if (n.nodeType === 1 && n.matches('.cm-row')) out += '\n';          // a blank line between messages
    };
    walk(node);
    return out.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }
  // The conversation's selection, null when nothing of it is selected (the input box is not it).
  // A triple-click on the last message ends its range at the start of the next block, which is
  // outside the conversation: that range is cut back to what is inside it.
  function selectedRange() {
    const sel = root.getSelection && root.getSelection();
    if (!covers() || !sel || sel.isCollapsed || !sel.rangeCount) return null;
    const scroll = layer.querySelector('#chat-scroll');
    const range = sel.getRangeAt(0);
    if (scroll.contains(range.commonAncestorContainer)) return range;
    if (!range.intersectsNode(scroll)) return null;
    const cut = range.cloneRange();
    if (!scroll.contains(cut.startContainer)) cut.setStart(scroll, 0);
    if (!scroll.contains(cut.endContainer)) cut.setEnd(scroll, scroll.childNodes.length);
    return cut;
  }
  function onCopy(e) {
    const range = selectedRange();
    if (!range || !e.clipboardData) return;
    const text = plainOf(range.cloneContents());
    if (!text) return;
    e.clipboardData.setData('text/plain', text);
    e.preventDefault();
  }
  // What a right-click copies: what ⌘C would take, the conversation's selection as plain text
  // or the input box's own; '' when nothing is selected or the view is not showing.
  function selectedText() {
    if (!covers()) return '';
    if (document.activeElement === input && input.selectionStart !== input.selectionEnd) {
      return input.value.slice(input.selectionStart, input.selectionEnd);
    }
    const range = selectedRange();
    return range ? plainOf(range.cloneContents()) : '';
  }
  // A paste from the right-click's menu: into the input box at its cursor, as it is. The
  // terminal under the view never gets it (nobody would see a draft left there).
  function insert(text) {
    const t = String(text == null ? '' : text);
    if (!t || !input) return;
    input.focus();
    const a = input.selectionStart ?? input.value.length, b = input.selectionEnd ?? a;
    input.value = input.value.slice(0, a) + t + input.value.slice(b);
    input.setSelectionRange(a + t.length, a + t.length);
    autosize();
  }
  // A message's or a code block's own button: through the app (copy_text, as the terminal's
  // copy does), the browser's clipboard if that fails; the button says what happened.
  async function copyOut(text, btn) {
    if (!text) return;
    let ok = false;
    try { ok = (await api().copy_text(text, 'chat-view')) === 'ok'; } catch (_) { ok = false; }
    if (!ok) { try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {} }
    if (!btn.dataset.base) btn.dataset.base = btn.dataset.label;     // its word is CSS (::after), out of the bubble's text
    btn.dataset.label = ok ? T('Copied ✓', '已複製 ✓') : T('Could not copy', '複製失敗');
    btn.classList.add('done');
    setTimeout(() => { btn.dataset.label = btn.dataset.base; btn.classList.remove('done'); }, 1500);
  }

  // ── # in the input box: name a bot or a tab, as the terminal's # does ─────
  // Typing # opens a list of the other tabs and the bots (asleep too, and those on the paired
  // machines, bots.js mentionables); letters filter it (a machine's name too), ↑↓ move, Enter or Tab
  // puts "#name " in ("#<machine>/<bot> " for a bot over there), Esc closes. What is sent is
  // that text, as from the terminal: the agent reading it hands the work over, and its
  // delegate wakes a sleeping bot. A # after a letter, a digit or a URL's characters (bots.js
  // MENTION_AFTER), with a space after it (a Markdown heading) or inside `code` opens nothing;
  // while the input method is choosing characters its keys are its own. ＃ works as #.
  const MENTION_AFTER = /[A-Za-z0-9\/&=?_.\-]/;
  let pickEl = null, pick = null;            // pick: {start, items, sel} while the list is open
  function mentionToken() {
    const caret = input.selectionStart;
    if (caret !== input.selectionEnd) return null;
    const before = input.value.slice(0, caret);
    const m = before.match(/[#＃]([^\s#＃]*)$/);
    if (!m) return null;
    const start = caret - m[0].length;
    if (start > 0 && MENTION_AFTER.test(before[start - 1])) return null;
    if ((before.match(/^```/gm) || []).length % 2) return null;                       // in a code fence
    if ((before.slice(before.lastIndexOf('\n') + 1, start).match(/`/g) || []).length % 2) return null;   // in `code`
    return { start, filter: m[1].toLowerCase() };
  }
  function mentionCandidates(filter) {
    const B = root.SFBots;
    const sid = shownFor;
    let all = B && B.mentionables ? B.mentionables(sid) : [];
    if (!B || !B.mentionables) {
      all = Object.keys(sessions()).filter(x => x !== sid && !sessions()[x].isRemote && sessions()[x].label)
        .map(x => ({ name: sessions()[x].label, sid: x, state: 'tab', face: '', also: [] }));
    }
    return all.filter(c => !filter || [c.name, ...(c.also || [])].some(n => String(n || '').toLowerCase().includes(filter)))
      .slice(0, 60);                          // the list scrolls: another machine's bots come after this one's
  }
  function pickUpdate() {
    const tok = layer && covers() ? mentionToken() : null;
    const items = tok ? mentionCandidates(tok.filter) : [];
    if (!items.length) return pickClose();
    const keep = pick && pick.start === tok.start ? pick.sel : 0;
    pick = { start: tok.start, items, sel: Math.min(keep, items.length - 1) };
    const word = { awake: T('awake', '醒著'), asleep: T('asleep: it will be woken', '休息中，會叫醒'),
                   external: T('external', '外部'), tab: T('tab', '分頁') };
    pickEl.innerHTML = items.map((c, i) => `<div class="cm-pick-item${i === pick.sel ? ' sel' : ''}" data-i="${i}">` +
        `<span class="cm-face busy-dot">${c.face || ''}</span><b>${c.machine ? escHtml(c.label) +    // a bot on another machine: its name, then the machine's, dimmer
          `<span class="cm-pick-machine"> · ${escHtml(c.machine)}</span>` : escHtml(c.name)}</b><span class="cm-pick-sub">` +
        `${c.primary ? SFIcons.html('star') + ' ' + escHtml(T('primary', '主要') + ' · ') : ''}${escHtml(word[c.state] || '')}</span></div>`)
      .join('') + `<div class="cm-pick-hint">${escHtml(T('↑↓ choose · Enter put in · Esc close', '↑↓ 選擇 · Enter 放進去 · Esc 收起'))}</div>`;
    pickEl.hidden = false;
    const sel = pickEl.querySelector('.cm-pick-item.sel');
    if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: 'nearest' });
  }
  function pickClose() { pick = null; if (pickEl) pickEl.hidden = true; }
  function pickInsert(i) {
    const c = pick && pick.items[i];
    if (!c) return pickClose();
    const tag = '#' + (c.insert || c.name) + ' ';    // another machine's bot: #<machine>/<bot>, the A2A target
    const caret = input.selectionStart;
    input.value = input.value.slice(0, pick.start) + tag + input.value.slice(caret);
    const at = pick.start + tag.length;
    pickClose();
    input.focus();
    input.setSelectionRange(at, at);
    autosize();
  }
  function onPickKey(e) {
    if (!pick || e.isComposing || e.keyCode === 229) return;       // the input method is choosing: its keys
    const n = pick.items.length;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      pick.sel = (pick.sel + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
      pickEl.querySelectorAll('.cm-pick-item').forEach((el, i) => el.classList.toggle('sel', i === pick.sel));
    } else if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') pickInsert(pick.sel);
    else if (e.key === 'Escape') pickClose();
    else return;
    e.preventDefault();
    e.stopImmediatePropagation();                                  // Enter here picks, it does not send
  }

  // ── the microphone, in the input row ─────────────────────────────────────
  // The page's own recording (mic_record_start/stop: its permission, its speech-to-text), but
  // asked without a tab, so the words come back instead of being typed into the CLI: they go
  // into the input box to be read before sending. Esc throws a recording away; it stops by
  // itself at 5 minutes, as the backend does. The floating mic hides while the view shows.
  let micBtn = null, micState = 'idle', micT0 = 0, micIv = null;      // idle | rec | busy
  function micSet(state) {
    micState = state;
    if (micIv) { clearInterval(micIv); micIv = null; }
    micBtn.classList.toggle('rec', state === 'rec');
    micBtn.classList.toggle('busy', state === 'busy');
    const show = () => {
      const sec = Math.floor((Date.now() - micT0) / 1000);
      micBtn.innerHTML = SFIcons.html('stop') + ` ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
      if (sec >= 300) micClick();
    };
    if (state === 'rec') { micT0 = Date.now(); show(); micIv = setInterval(show, 500); }
    else micBtn.innerHTML = SFIcons.html(state === 'busy' ? 'hourglass' : 'mic');
    micBtn.title = state === 'rec' ? T('Recording: press again to stop, Esc to cancel', '錄音中：再按一次停止，Esc 取消')
      : T('Voice input: the words go into the box, not sent', '語音輸入：轉出來的字放進輸入框，不會直接送出');
    note(state === 'rec' ? T('Recording… press the mic again to stop, Esc to cancel', '錄音中…再按一次麥克風停止，Esc 取消')
      : state === 'busy' ? T('Turning it into words…', '正在轉成文字…') : '');
  }
  function micPut(text) {                     // into the box where the cursor is, for a read before sending
    const t = String(text || '').trim();
    if (!t) return;
    const a = input.selectionStart ?? input.value.length, b = input.selectionEnd ?? a;
    const before = input.value.slice(0, a), after = input.value.slice(b);
    const pad = /[A-Za-z0-9]$/.test(before) && /^[A-Za-z0-9]/.test(t) ? ' ' : '';     // words, not 中文, get a space
    input.value = before + pad + t + after;
    const at = (before + pad + t).length;
    input.focus();
    input.setSelectionRange(at, at);
    autosize();
  }
  async function micClick() {
    const a = api();
    if (!a || micState === 'busy') return;
    let r = {};
    if (micState === 'idle') {
      try { r = JSON.parse(await a.mic_record_start()); } catch (_) { return; }
      if (r.ok) { micSet('rec'); return; }
    } else {
      micSet('busy');
      try { r = JSON.parse(await a.mic_record_stop('')); } catch (_) { micSet('idle'); return; }
      if (r.ok) { micSet('idle'); micPut(r.text); return; }
    }
    micSet('busy');
    try { await host().micGuide(r, micPut); } catch (_) {}      // install ffmpeg / STT, or say why
    micSet('idle');
  }
  async function micCancel() {
    micSet('busy');
    try { await api().mic_record_stop('', true); } catch (_) {}
    micSet('idle');
  }
  function renderMic() {                       // on each draw: shown or not, and its idle face in this language
    if (!micBtn) return;
    micBtn.hidden = ((settings() || {}).stt_mic_button === false);       // the same setting as the floating one
    if (micState !== 'idle') return;
    micBtn.innerHTML = SFIcons.html('mic');
    micBtn.title = T('Voice input: the words go into the box, not sent', '語音輸入：轉出來的字放進輸入框，不會直接送出');
  }
  // A right-click's "Reply": the message under the pointer, its sender and its first lines, quoted into the
  // box as "> " lines above what is there, the cursor after them.
  function quoteOf(target) {
    const bubble = target && target.closest && target.closest('#chat-list .cm-bubble');      // on a message, not beside it
    const row = bubble && bubble.closest('.cm-row.cm-user, .cm-row.cm-peer, .cm-row.cm-bot');
    const text = row ? bubble.innerText.trim() : '';
    if (!text) return null;
    const who = row.classList.contains('cm-peer') ? (row.dataset.who || T('Another agent', '另一個 AI'))
      : row.classList.contains('cm-bot') ? displayName(sessions()[shownFor]) : T('Me', '我');
    return { who, text };
  }
  function reply(q) {
    if (!q || !input) return false;
    const lines = q.text.split('\n').map(l => l.trim()).filter(Boolean);
    const quote = [`> ${q.who}${T(': ', '：')}`, ...lines.slice(0, 6).map(l => '> ' + l), ...(lines.length > 6 ? ['> …'] : [])]
      .join('\n') + '\n\n';
    input.value = quote + input.value;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    autosize();
    return true;
  }
  // `clearDraft`: the person answered the draft card with "clear it and send".
  async function send(clearDraft) {
    const sid = shownFor;
    const text = input.value;
    const v = sid ? view(sid) : null;
    const files = v ? (v.files || []).slice() : [];
    if (!sid || (!text.trim() && !files.length)) return;
    // Attached files go in as their paths, ahead of the words: what the terminal types
    // for an attachment, and what a paired phone sends for a photo.
    const body = files.length ? files.map(f => f.path).join('\n') + (text.trim() ? '\n\n' + text : '') : text;
    const shown = text.trim() ? text : files.map(f => baseName(f.path)).join('\n');
    v.files = [];
    renderAttach(sid);
    input.value = '';
    autosize();
    const r = await sendBody(sid, { text: shown, body, key: text.trim() || baseName(files[0].path), files }, !!clearDraft);
    if (!r || !r.success) {                                     // nothing went in: the words stay in the box
      if (!input.value) { input.value = text; autosize(); }
      v.files = files.concat(v.files || []);
      renderAttach(sid);
    }
  }
  // One send: a pending bubble until the record has it. The server may say the terminal's
  // input box holds a draft (the card asks what to do), or that it queued the text (its id
  // is followed in chat_view's `queue`: still waiting, or given up → "not sent", resend).
  async function sendBody(sid, msg, clearDraft) {
    const v = view(sid);
    const item = { text: msg.text, body: msg.body, key: msg.key, files: msg.files || [], at: Date.now(),
                   queued: false, uid: Math.random().toString(36).slice(2) };
    v.pending.push(item);
    v.draftCard = null;
    renderAsk(sid);
    v.atBottom = true;
    renderList(sid);
    note('');
    let r = null;
    try {
      r = JSON.parse(await call(sid, 'send', msg.body, !!clearDraft));
      if (r.code === 'draft') {
        v.pending = v.pending.filter(p => p !== item);
        v.draftCard = { draft: String(r.draft || '') };
        renderAsk(sid);
      } else if (!r.success) {
        v.pending = v.pending.filter(p => p !== item);
        note(r.message || T('Not sent.', '沒有送出。'), true);
      } else if (r.queued) {
        Object.assign(item, { queued: true, id: r.id || '', reason: r.reason || '' });
        note(v.waitNote = waitWords(item, true, sid));
      }
    } catch (e) {
      v.pending = v.pending.filter(p => p !== item);
      note(String(e), true);
    }
    renderList(sid);
    refresh(sid, true);
    return r;
  }
  // Why a queued message has not gone in yet, in plain words: what holds it now (chat_view's queue.busy,
  // tab_delivery.busy_reason) and for how long its turn has run, else why it was queued.
  function waitWords(p, long, sid) {
    const b = (sid && view(sid).busy) || {};
    const reason = b.reason || p.reason;
    if (reason === 'telegram' || reason === 'working') {
      const who = displayName(sessions()[sid]) || T('It', '它');
      const mins = b.since ? Math.floor((Date.now() / 1000 - b.since) / 60) : 0;
      const bits = [reason === 'telegram' ? T('from Telegram', '從 Telegram 來的') : '',
                    mins >= 1 ? T(`${mins} min so far`, `已經 ${mins} 分鐘`) : ''].filter(Boolean);
      return reason === 'telegram'
        ? T(`${who} is still on the previous message (${bits.join(', ')}); yours goes in right after.`,
            `${who}還在處理上一則（${bits.join('，')}），處理完會接著送。`)
        : T(`${who} is still working${bits.length ? ` (${bits.join(', ')})` : ''}; yours goes in right after.`,
            `${who}還在做上一件事${bits.length ? `（${bits.join('，')}）` : ''}，做完會接著送。`);
    }
    switch (reason) {
      case 'model': return T('Waiting for its model switch to finish', '等它換完模型再送進去');
      case 'decision': return T('Waiting for you to answer its question first', '等你先回答它的問題，再送進去');
      case 'telegram': return T('Waiting for the message from Telegram to be taken', '等 Telegram 那一則處理完再送進去');
      case 'queued': return T('In line behind an earlier message', '排在前一則訊息後面');
      case 'draft': return T('Waiting for the draft in the terminal to be cleared', '等終端機裡的草稿清掉再送進去');
      default: return long ? T('It is busy: your message goes in as soon as it finishes this step.', '它正在忙，這一步做完就會收到你的訊息。')
                           : T('Waiting for it to finish this step', '等它做完這一步就送進去');
    }
  }
  const NOW_TIP = () => T('Send now types it in at once and may interrupt what it is doing.',
                          '插隊會馬上送進去，可能打斷它手上正在做的事。');
  // The bubble's "send now" (sfctl send --now): out of the queue and into the tab at once.
  async function sendNow(sid, key) {
    const p = view(sid).pending.find(x => x.uid === key);
    if (!p || !p.queued || !p.id) return;
    let r;
    try { r = JSON.parse(await call(sid, 'send_now', p.id)); } catch (e) { r = { success: false, message: String(e) }; }
    if (r.success) { p.queued = false; p.at = Date.now(); note(''); }
    else note(r.message || T('Not sent.', '沒有送出。'), true);
    renderList(sid);
    refresh(sid, true);
  }
  // The card for a draft in the terminal: what it says, and clear-and-send or not now.
  function draftCardHtml(sid) {
    const c = view(sid).draftCard;
    if (!c) return '';
    return `<div class="cm-ask-card cm-draft-card">${faceHtml(sid, '')}<div class="cm-ask-body">` +
      `<div class="cm-ask-title">${escHtml(T('Not sent yet', '還沒送出'))}</div>` +
      `<div class="cm-ask-q">${escHtml(T('The terminal has text that was never sent:', '終端機裡有一段還沒送出的字：'))}</div>` +
      `<div class="cm-ask-ctx"><code>${escHtml(c.draft)}</code></div>` +
      `<div class="cm-ask-note">${escHtml(T('Your message would wait behind it. Clear it and send yours, or keep yours here for now.',
                                            '你的訊息會一直排在它後面。要清掉它再送出你的，還是先把你的留在這裡？'))}</div>` +
      `<div class="cm-opts"><button class="cm-opt" data-act="draft-clear">${escHtml(T('Clear it and send', '清掉後送出'))}</button>` +
      `<button class="cm-opt" data-act="draft-keep">${escHtml(T('Not now', '先不送'))}</button></div></div></div>`;
  }
  async function answer(btn) {
    const sid = shownFor;
    if (!sid) return;
    const n = parseInt(btn.dataset.n, 10);
    const id = btn.dataset.id;
    const v = view(sid);
    ask.querySelectorAll('.cm-opt').forEach(b => { b.disabled = true; });
    try {
      const r = JSON.parse(await call(sid, 'answer', n, id));
      if (r.success) {
        v.answered = { id, n, label: btn.textContent };
        note('');
      } else {
        v.answered = null;
        note(r.code === 'stale'
          ? T('That question was already answered or has changed; nothing was sent.', '這題已經回答過或換題了，沒有送出。')
          : (r.message || T('Not sent.', '沒有送出。')), true);
      }
    } catch (e) { note(String(e), true); }
    v.rev = '';
    renderAsk(sid);
    refresh(sid, true);
  }
  // ── a path in a message ───────────────────────────────────────────────
  // An inline `code` that is a path (/…, ~/…, C:\…) becomes a link: a picture shows under it as a thumbnail
  // (a click shows it full size), anything else is shown in Finder / Explorer. Each path is asked about
  // once; the answer is kept, so a redraw does not read the file again.
  const PATH_RE = /^(?:~\/|\/|[A-Za-z]:\\)[^\s<>"'`|]+$/;
  const IMG_RE = /\.(png|jpe?g|gif|webp|bmp)$/i;
  const pathInfo = new Map();
  const infoOf = p => {
    if (!pathInfo.has(p)) pathInfo.set(p, Promise.resolve(api().chat_file_preview(p)).then(r => JSON.parse(r)).catch(() => ({})));
    return pathInfo.get(p);
  };
  const IS_WIN = /Win/.test(navigator.platform || '');
  function decoratePaths(root) {
    if (remoteOf(shownFor)) return;               // a path on another computer: nothing here to show
    root.querySelectorAll('.cm-bubble code').forEach(code => {
      const p = code.textContent.trim();
      if (code.closest('pre') || !PATH_RE.test(p)) return;
      code.dataset.path = p;
      code.classList.add('cm-path');
      code.title = IMG_RE.test(p) ? T('Click to see it full size', '點一下看大圖')
        : IS_WIN ? T('Show in Explorer', '在檔案總管中顯示') : T('Show in Finder', '在 Finder 中顯示');
      if (!IMG_RE.test(p)) return;
      infoOf(p).then(info => {
        const bubble = code.closest('.cm-bubble');
        if (!info || info.kind !== 'image' || !bubble || !bubble.isConnected) return;
        let row = bubble.querySelector(':scope > .cm-thumbs');          // the pictures go under the words, not in them
        if (!row) { row = document.createElement('div'); row.className = 'cm-thumbs'; bubble.appendChild(row); }
        if ([...row.children].some(x => x.dataset.path === p)) return;
        const img = document.createElement('img');
        Object.assign(img, { className: 'cm-thumb', src: info.data, alt: info.name || '', title: code.title });
        img.dataset.path = p;
        row.appendChild(img);
      });
    });
  }
  async function openPath(p) {
    const info = IMG_RE.test(p) ? await infoOf(p) : null;
    if (info && info.kind === 'image') {
      const ov = document.createElement('div');
      ov.className = 'cm-lightbox';
      ov.innerHTML = `<img src="${info.data}" alt="">`;
      const close = () => { ov.remove(); document.removeEventListener('keydown', onKey, true); };
      const onKey = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); close(); } };
      ov.addEventListener('click', close);
      document.addEventListener('keydown', onKey, true);
      document.body.appendChild(ov);
      return;
    }
    let r = {};
    try { r = JSON.parse(await api().chat_reveal(p)); } catch (_) { /* no backend */ }
    if (!r.success) note(r.code === 'missing' ? T('That file is not there', '找不到這個檔案') : (r.message || ''), true);
  }
  function onClick(e) {
    const pathEl = e.target.closest('.cm-path, .cm-thumb');
    if (pathEl && pathEl.dataset.path) { e.preventDefault(); openPath(pathEl.dataset.path); return; }
    const el = e.target.closest('[data-act], .cm-link');
    if (!el) return;
    e.preventDefault();
    const sid = activeSid();
    const act = el.dataset.act;
    if (el.classList.contains('cm-link') && el.dataset.url) {
      if (/^https?:\/\//i.test(el.dataset.url)) { try { api().open_url(el.dataset.url); } catch (_) {} }
      return;
    }
    if (act === 'answer') return answer(el);
    if (act === 'connect') {                               // open the wizard, and tell the bot option 1 so it waits
      try { root.SFConnect.openFromChat(el.dataset.service, { tag: el.dataset.tag, from_sid: sid }); } catch (_) { /* the answer still goes */ }
      return /^\d+$/.test(el.dataset.n || '') ? answer(el) : undefined;
    }
    if (act && act.startsWith('login-') && root.SFLogin) {
      return root.SFLogin.click(el, sid, view(sid).data, () => { view(sid).rev = ''; renderAsk(sid); refresh(sid, true); });
    }
    if (act === 'draft-clear') return send(true);
    if (act === 'draft-keep') { view(sid).draftCard = null; renderAsk(sid); input.focus(); return; }
    if (act === 'send-now') return sendNow(sid, el.dataset.key);
    if (act === 'resend' || act === 'dismiss') {
      const v = view(sid);
      const p = v.pending.find(x => x.uid === el.dataset.key);
      v.pending = v.pending.filter(x => x !== p);
      if (p && act === 'resend') return sendBody(sid, p, false);
      renderList(sid);
      return;
    }
    if (act === 'unattach') { const v = view(sid); (v.files || []).splice(+el.dataset.i, 1); renderAttach(sid); return; }
    if (act === 'to-term') return switchView(sid, 'cli');
    if (act === 'model') { if (root.SFTabModel) root.SFTabModel.open(sid, el); return; }
    if (act === 'ask-dismiss') { view(sid).askDismissed = el.dataset.key; return renderAsk(sid); }
    if (act === 'sidebar') { const b = document.getElementById('btn-sidebar'); if (b) b.click(); return; }
    if (act === 'copy') return copyOut((view(sid).copy || {})[el.dataset.key] || '', el);
    if (act === 'copy-code') { const c = el.closest('.cm-code'); return copyOut(c ? c.querySelector('pre').textContent : '', el); }
    if (act === 'settings') {        // the header's face: this conversation's settings (bots.js), open past bots.js's outside-click
      e.stopPropagation();
      if (root.SFBots && root.SFBots.openSettings) root.SFBots.openSettings(sid, el);
      return;
    }
    if (act === 'notice-close') { noticeClosed.add(sid); renderNotice(sid, false); return; }
    if (act === 'earlier') { const v = view(sid); v.limit = Math.min(400, v.limit * 2); v.rev = ''; v.atBottom = false; refresh(sid, true); return; }
    if (act === 'code-more') { el.parentNode.classList.toggle('is-open'); return; }
    if (act === 'open') {
      const v = view(sid);
      const key = el.dataset.key;
      if (v.open.has(key)) v.open.delete(key); else v.open.add(key);
      v.atBottom = false;
      renderList(sid);
    }
  }

  // ── wiring ─────────────────────────────────────────────────────────────
  function onSwitch() { apply(true); }
  function tick() {
    if (!mount()) return;
    apply(false);
    const sid = shownFor;
    if (!sid || document.hidden) return;
    renderHead(sid);
    renderHint(sid);
    renderLive(sid);
    if (noReplyDue(sid) !== !!view(sid).noReply) renderList(sid);
    refresh(sid, false);
  }
  // A working tab's second line in the sidebar (web/bots.js previewOf), in plain words
  // while the setting is on chat. '' leaves bots.js's own line.
  function sidebarLine(sid, st) {
    if (globalStyle() !== 'chat' || !st || !['working', 'decision', 'stuck'].includes(st.state)) return '';
    const d = plainDoing(st, lang());
    return d.text ? (d.detail ? `${d.text} · ${d.detail}` : d.text) : '';
  }

  root.SFChat = { call, onSwitch, covers, sidebarLine, apply, rules: RULES, modeOf, setStyle, setMode, switchView, toggle, label, attach, selectedText, insert, relabel, quoteOf, reply };
  const boot = () => { mount(); apply(false); setInterval(tick, POLL_MS); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(typeof window !== 'undefined' ? window : null);
