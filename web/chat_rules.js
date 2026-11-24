/* ShellBot chat view: the rules (pure functions; no DOM, no network).
 *
 * Split out of web/chat.js so a page that has no chat screen (the phone app's office, web/office_app_host.js) can use the
 * same words and the same safe Markdown: chat.js takes them from SFChatRules; web/office.js chatShape and the app office
 * get `md` / `preview` / `plainDoing` from here. tests_chat_shell.js runs them in node (through chat.js, which re-exports
 * them). Text for the page leaves this file only through escHtml / md / inline. */
(function (root) {
  'use strict';

  // ── rules: language ──────────────────────────────────────────────────────
  const isZh = lang => /^zh/i.test(String(lang || ''));
  const CHAT_PROVIDERS = ['claude', 'codex', 'opencode', 'grok', 'cursor', 'pi'];   // api_chat.CHAT_PROVIDERS
  const PROVIDER_NAME = { claude: 'Claude', codex: 'Codex', opencode: 'OpenCode', grok: 'Grok', pi: 'Pi',
                          cursor: 'Cursor', agy: 'Antigravity' };

  // ── rules: a question and its options ────────────────────────────────────
  // A key hint at the end of an option: Claude's "(esc)" / "(shift+tab)", Codex's "(y)".
  const KEY_HINT = /\s*\((?:esc|tab|shift\+tab|enter|[a-z])\)\s*$/i;
  // [pattern, English, 中文, tone]. Anchored; a captured part is kept as written.
  // Wording measured in Claude Code 2.1.322 and Codex 0.142.1 (their own strings).
  const OPTION_RULES = [
    // Claude Code
    [/^Yes$/, 'Allow', '允許', 'yes'],
    [/^No$/, "Don't allow", '不允許', 'no'],
    [/^No, and tell Claude what to do differently$/, "Don't allow, and tell it what to do instead", '不允許，告訴它要怎麼改', 'no'],
    [/^Yes, allow all edits during this session$/, 'Allow, and allow every edit this session', '允許，這次工作階段的修改都不再問', 'yes'],
    // 2.1.322 in manual mode, an edit's second option (it wraps on an 80-column tab)
    [/^Yes, and switch to accept edits \(auto-approve file edits and common file commands\) for this session$/,
      'Allow, and allow edits and common file commands this session', '允許，這次工作階段的修改和常用檔案指令都不再問', 'yes'],
    [/^Yes, and don't ask again for (.+) commands in (.+)$/, "Allow, and don't ask again for $1 commands in $2", '允許，之後在 $2 執行 $1 指令都不再問', 'yes'],
    [/^Yes, and don't ask again for (.+) commands on (.+)$/, "Allow, and don't ask again for $1 commands on $2", '允許，之後在 $2 執行 $1 指令都不再問', 'yes'],
    [/^Yes, and always allow access to (.+) from this project$/, 'Allow, and let this project always reach $1', '允許，而且這個專案以後都能存取 $1', 'yes'],
    [/^Yes, allow reading from (.+) from this project$/, 'Allow, and let this project always read $1', '允許，而且這個專案以後都能讀取 $1', 'yes'],
    [/^Yes, and always allow access to (.+) for this session$/, 'Allow, and let it reach $1 this session', '允許，這次工作階段都能存取 $1', 'yes'],
    // 2.1.322 adds this to its permission prompts (with " · auto mode handles these prompts for you")
    [/^Yes, and switch to auto mode(?: · .*)?$/, 'Allow, and switch to auto mode (it decides these itself from now on)', '允許，並切到自動模式（之後這類詢問由它自己判斷）', 'yes'],
    // Codex
    [/^Yes, proceed$/, 'Allow', '允許', 'yes'],
    [/^Yes, just this once$/, 'Allow this once', '只允許這一次', 'yes'],
    [/^Yes, and don't ask again for this command in this session$/, "Allow, and don't ask again for this command this session", '允許，這次工作階段這個指令不再問', 'yes'],
    [/^Yes, and don't ask again for commands that start with `(.+)`$/, "Allow, and don't ask again for commands starting with $1", '允許，之後 $1 開頭的指令都不再問', 'yes'],
    [/^Yes, and don't ask again for these files$/, "Allow, and don't ask again for these files", '允許，這些檔案之後都不再問', 'yes'],
    [/^Yes, and allow this host for this conversation$/, 'Allow, and allow this site for this conversation', '允許，這次對話都能連這個網站', 'yes'],
    [/^Yes, and allow this host in the future$/, 'Allow, and always allow this site', '允許，以後都能連這個網站', 'yes'],
    [/^No, and block this host in the future$/, "Don't allow, and always block this site", '不允許，以後都擋掉這個網站', 'no'],
    [/^Yes, (?:and allow|grant) these permissions for this session$/, 'Allow, and keep these permissions this session', '允許，這次工作階段都給這些權限', 'yes'],
    [/^No, continue without running it$/, "Don't run it, and let it go on", '不執行，讓它繼續', 'no'],
    [/^No, continue without permissions$/, "Don't grant them, and let it go on", '不給權限，讓它繼續', 'no'],
    [/^No, and tell Codex what to do differently$/, "Don't allow, and tell it what to do instead", '不允許，告訴它要怎麼改', 'no'],
  ];
  const QUESTION_RULES = [
    [/^Do you want to proceed\?$/, 'Let it go ahead?', '要讓它繼續嗎？'],
    [/^Do you want to make this edit to (.+)\?$/, 'Let it edit $1?', '要讓它修改 $1 嗎？'],
    [/^Do you want to create (.+)\?$/, 'Let it create $1?', '要讓它建立 $1 嗎？'],
    [/^Do you want to allow Claude to fetch this content\?$/, 'Let it read this web page?', '要讓它讀取這個網頁嗎？'],
    [/^Would you like to run the following command\?$/, 'Let it run this command?', '要讓它執行這個指令嗎？'],
    [/^Would you like to make the following edits\?$/, 'Let it make these edits?', '要讓它做這些修改嗎？'],
    [/^Would you like to grant these permissions\?$/, 'Give it these permissions?', '要給它這些權限嗎？'],
    [/^Do you want to approve network access to "(.+)"\?$/, 'Let it connect to $1?', '要讓它連到 $1 嗎？'],
  ];
  const fill = (tpl, m) => tpl.replace(/\$(\d)/g, (_, i) => m[+i] || '');

  // One option's plain words, or null when no rule knows it.
  function mapOption(text, zh) {
    const core = String(text || '').replace(KEY_HINT, '').trim();
    for (const [re, en, cn, tone] of OPTION_RULES) {
      const m = core.match(re);
      if (m) return { label: fill(zh ? cn : en, m), tone };
    }
    return null;
  }
  function mapQuestion(text, zh) {
    const core = String(text || '').trim();
    for (const [re, en, cn] of QUESTION_RULES) {
      const m = core.match(re);
      if (m) return fill(zh ? cn : en, m);
    }
    return '';
  }
  // decision: {id, reason, options: [{n, text}]} as chat_view gives it.
  // Every option known → plain words; any one unknown → every option as written.
  // `n` is always the option's own number, never its position on screen. Which menu, and
  // whether it was read whole, is the shared reader's call (bridge_telegram.menu_block).
  function mapDecision(decision, lang) {
    const zh = isZh(lang);
    const d = decision || {};
    const raw = (d.options || []).map(o => ({ n: o.n, raw: String(o.text == null ? '' : o.text) }));
    const known = raw.map(o => mapOption(o.raw, zh));
    const translated = raw.length > 0 && known.every(Boolean);
    const question = mapQuestion(d.reason, zh);
    return {
      id: d.id || '',
      question: question || String(d.reason || ''),
      questionTranslated: !!question,
      translated,
      options: raw.map((o, i) => ({ n: o.n, raw: o.raw,
                                    label: translated ? known[i].label : o.raw,
                                    tone: translated ? known[i].tone : '' })),
    };
  }

  // ── rules: tool calls ────────────────────────────────────────────────────
  const TOOL_KIND = {
    Read: 'read', NotebookRead: 'read', read_file: 'read', view_image: 'read',
    Edit: 'edit', MultiEdit: 'edit', Write: 'edit', NotebookEdit: 'edit', apply_patch: 'edit',
    Bash: 'run', BashOutput: 'run', KillShell: 'run', exec_command: 'run', shell: 'run',
    local_shell: 'run', write_stdin: 'run',
    Grep: 'search', Glob: 'search', LS: 'search', tool_search: 'search',
    WebSearch: 'web', WebFetch: 'web', web_search: 'web',
    Task: 'agent', Agent: 'agent',
    TodoWrite: 'plan', update_plan: 'plan', ExitPlanMode: 'plan',
    AskUserQuestion: 'ask', mcp: 'mcp', image_generation_call: 'image',
  };
  const TOOL_KIND_LC = {
    bash: 'run', shell: 'run', run_terminal_cmd: 'run', run_terminal_command: 'run', run_command: 'run', execute: 'run',
    read: 'read', read_file: 'read', view: 'read', cat: 'read',
    write: 'edit', edit: 'edit', multiedit: 'edit', patch: 'edit', edit_file: 'edit', write_file: 'edit',
    str_replace: 'edit', str_replace_editor: 'edit', search_replace: 'edit', create_file: 'edit', delete_file: 'edit',
    grep: 'search', glob: 'search', list: 'search', ls: 'search', list_dir: 'search', list_directory: 'search',
    file_search: 'search', codebase_search: 'search', grep_search: 'search', find: 'search',
    webfetch: 'web', websearch: 'web', fetch: 'web', web_fetch: 'web', fetch_url: 'web',
    task: 'agent', todowrite: 'plan', todoread: 'plan', todo_write: 'plan',
  };
  function toolKind(name) {
    if (!name) return 'other';
    if (TOOL_KIND[name]) return TOOL_KIND[name];
    if (TOOL_KIND_LC[String(name).toLowerCase()]) return TOOL_KIND_LC[String(name).toLowerCase()];
    return /^mcp__/.test(name) ? 'mcp' : 'other';
  }
  // How many of each, in plain words: "查了 3 個檔案、跑了 1 個指令".
  const SUMMARY_ORDER = ['read', 'search', 'web', 'edit', 'run', 'agent', 'mcp', 'image', 'ask', 'other', 'plan'];
  const SUMMARY = {
    edit:   [n => `changed ${n} file${n > 1 ? 's' : ''}`, n => `改了 ${n} 個檔案`],
    run:    [n => `ran ${n} command${n > 1 ? 's' : ''}`, n => `跑了 ${n} 個指令`],
    read:   [n => `looked at ${n} file${n > 1 ? 's' : ''}`, n => `查了 ${n} 個檔案`],
    search: [n => `searched ${n} time${n > 1 ? 's' : ''}`, n => `搜尋了 ${n} 次`],
    web:    [n => `checked ${n} web page${n > 1 ? 's' : ''}`, n => `查了 ${n} 個網頁`],
    agent:  [n => `handed off ${n} sub-task${n > 1 ? 's' : ''}`, n => `派出 ${n} 個子任務`],
    mcp:    [n => `used ${n} connected tool${n > 1 ? 's' : ''}`, n => `用了 ${n} 個外部工具`],
    image:  [n => `made ${n} image${n > 1 ? 's' : ''}`, n => `做了 ${n} 張圖`],
    ask:    [() => 'asked you', () => '問了你'],
    other:  [n => `used ${n} tool${n > 1 ? 's' : ''}`, n => `用了 ${n} 個工具`],
    plan:   [() => 'updated its plan', () => '更新了計畫'],
  };
  function toolSummary(calls, lang) {
    const zh = isZh(lang);
    // Files are counted once each (read twice is still one file); a call that names
    // no file (Codex's apply_patch) counts on its own.
    const counts = {};
    const files = { read: new Set(), edit: new Set() };
    for (const c of calls || []) {
      const k = toolKind(c.tool);
      if (files[k] && c.target) {
        if (files[k].has(c.target)) continue;
        files[k].add(c.target);
      }
      counts[k] = (counts[k] || 0) + 1;
    }
    const parts = SUMMARY_ORDER.filter(k => counts[k]).map(k => SUMMARY[k][zh ? 1 : 0](counts[k]));
    const s = parts.join(zh ? '、' : ', ');
    return zh ? s : s.charAt(0).toUpperCase() + s.slice(1);
  }

  // ── rules: what it is doing right now ────────────────────────────────────
  // agent_status.VERB words, for a status that only has its `action` line.
  const VERB_KIND = { Reading: 'read', Editing: 'edit', Writing: 'edit', Running: 'run', Searching: 'search',
                      Fetching: 'web', Delegating: 'agent', Planning: 'plan', Asking: 'ask', Calling: 'mcp',
                      Generating: 'image' };
  const RUN_KINDS = [
    [/(?:^|[\s;&|(])(?:(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test|pytest|go\s+test|cargo\s+test|jest|vitest|run_tests|make\s+test)\b|\btests?_\w+\.(?:py|js)\b/i, 'test'],
    [/(?:^|[\s;&|(])(?:(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?build|cargo\s+build|go\s+build|make|xcodebuild|gradle|mvn|tsc)\b/i, 'build'],
    [/(?:^|[\s;&|(])(?:(?:npm|pnpm|yarn|bun)\s+(?:install|add|ci|i)|pip3?\s+install|brew\s+install|uv\s+(?:pip|add|sync))\b/i, 'install'],
    [/(?:^|[\s;&|(])git\s+\w/i, 'git'],
    [/(?:^|[\s;&|(])(?:curl|wget)\b/i, 'net'],
    [/^(?:ls|cat|head|tail|find|grep|rg|wc|tree|sed\s+-n)\b/i, 'look'],
  ];
  const RUN_WORDS = {
    test: ['Running the tests', '正在跑測試'], build: ['Building', '正在建置'],
    install: ['Installing packages', '正在安裝套件'], git: ['Working with git', '正在處理 git'],
    net: ['Fetching from the network', '正在連網路'], look: ['Looking through files', '正在翻檔案'],
  };
  function runWords(cmd, zh) {
    const c = String(cmd || '').trim();
    for (const [re, kind] of RUN_KINDS) if (re.test(c)) return RUN_WORDS[kind][zh ? 1 : 0];
    return zh ? '正在執行指令' : 'Running a command';
  }
  // st: the status monitor's verdict for a tab (index.html's s._serverStatus).
  // {text, detail}: the plain line, and the raw thing it is about (shown dimmer).
  function plainDoing(st, lang) {
    const zh = isZh(lang);
    const v = st || {};
    if (v.state === 'decision') return { text: zh ? '等你決定' : 'Waiting for you', detail: '' };
    if (v.state === 'stuck') return { text: zh ? '好像卡住了' : 'Seems stuck', detail: '' };
    if (v.state !== 'working') return { text: '', detail: '' };
    const act = v.activity || {};
    let kind = act.tool ? toolKind(act.tool) : '';
    let target = act.target || '';
    if (!kind && v.action) {
      const [verb, ...rest] = String(v.action).split(' ');
      kind = VERB_KIND[verb] || '';
      target = rest.join(' ');
    }
    const t = (en, cn) => ({ text: zh ? cn : en, detail: target });
    switch (kind) {
      case 'read': return t('Reading', '正在看');
      case 'edit': return t('Editing', '正在改');
      case 'run': return { text: runWords(target, zh), detail: target };
      case 'search': return t('Searching', '正在搜尋');
      case 'web': return t('Looking it up online', '正在查網路');
      case 'agent': return t('Handing off a sub-task', '正在派子任務');
      case 'plan': return { text: zh ? '正在整理待辦' : 'Updating its plan', detail: '' };
      case 'ask': return { text: zh ? '正在問你問題' : 'Asking you something', detail: '' };
      case 'mcp': return t('Using a connected tool', '正在用外部工具');
      case 'image': return t('Making an image', '正在做圖');
      case 'other': return { text: zh ? '正在用工具' : 'Using a tool', detail: act.tool || '' };
      default: return { text: zh ? '正在想…' : 'Thinking…', detail: '' };
    }
  }

  // ── rules: a tab's command ───────────────────────────────────────────────
  // Which CLI a launch command runs, before chat_view has said (only so a bash tab does
  // not flash the chat view; the backend's answer replaces it).
  // The program it starts: past `cd … &&`, VAR=… and env with its options
  // (`env -u CLAUDE_CONFIG_DIR claude --resume …` starts claude).
  function launched(cmd) {
    const words = String(cmd || '').split(/&&|;/).pop().trim().split(/\s+/);
    let i = 0;
    for (let env = false; i < words.length; i++) {
      const w = words[i];
      if (/(^|\/)env$/.test(w)) env = true;
      else if (env && /^(-[uCPS]|--unset|--chdir)$/.test(w)) i++;      // its value is the next word
      else if (!(env && w.startsWith('-')) && !/^\w+=/.test(w)) break;
    }
    return (words[i] || '').split(/[\\/]/).pop().replace(/\.(cmd|exe|ps1)$/i, '').toLowerCase();
  }
  function guessProvider(cmd) {
    const base = launched(cmd);
    if (/claude/.test(base)) return 'claude';
    if (/codex/.test(base)) return 'codex';
    if (base === 'cursor-agent') return 'cursor';
    return base;
  }
  const SHELLS = new Set(['bash', 'zsh', 'sh', 'fish', 'powershell', 'pwsh', 'cmd', 'nu']);
  // A tab still called by its command ("claude", "codex", "bash"): in chat style it shows
  // as "Claude 對話" / "終端機", numbered when there are several. Display only: the tab's
  // own name (sfctl, Telegram, A2A) is untouched.
  function commandNamed(label, cmd) {
    const l = String(label || '').trim().toLowerCase();
    if (!l) return true;
    const first = launched(cmd);
    return l === first || l === guessProvider(cmd) || (SHELLS.has(l) && SHELLS.has(first));
  }
  function plainName(cmd, zh) {
    const prov = guessProvider(cmd);
    if (PROVIDER_NAME[prov]) return zh ? `${PROVIDER_NAME[prov]} 對話` : `${PROVIDER_NAME[prov]} chat`;
    return zh ? '終端機' : 'Terminal';
  }

  // ── rules: what a turn shows ─────────────────────────────────────────────
  // The phone's own test for a compaction summary (ChatTimeline.isCompactionSummary).
  const COMPACT_PREFIX = 'This session is being continued from a previous conversation';
  const isCompaction = t => !!t && t.kind === 'user_msg' && String(t.text || '').startsWith(COMPACT_PREFIX);
  const TG_TOKEN = /(?:\[\[|<<)\/?TG_REPLY_[0-9a-fA-F]+(?:\]\]|>>)/;
  // Context Codex writes as a message of its own, not something anyone said.
  const CODEX_CONTEXT = /^\s*(?:<environment_context>|<user_instructions>|<permissions instructions>|# AGENTS\.md instructions)/;
  const RED_MARK = /^[\s>❯›⏺•*-]*\[\[\s*SF\s*:\s*RED\s*(?::[^\]]*)?\]\]\s*$/i;     // bridge_home._RED_LINE
  // Claude Code keeps a pasted block as <pasted_content id="…">…</pasted_content id="…">. ShellBot
  // types a long message into a tab as a paste (another agent's, one from Telegram, the chat view's
  // own send), so a wrapper that is the whole message is only how it went in: its words are the
  // message. A wrapper among the person's own words is something they pasted: a folded block of its
  // own. The tags are never shown, and the CLI keeps the text as it got it. (agent_link.unwrap_paste
  // is the same rule for who sent a message.)
  const PASTE_TAG = /<pasted_content(?:\s[^>]*)?>\n?|\n?<\/pasted_content(?:\s[^>]*)?>/g;
  const PASTE_OPEN = /<pasted_content(?:\s[^>]*)?>/g;
  const PASTE_BLOCK = /<pasted_content(?:\s[^>]*)?>\n?([\s\S]*?)\n?<\/pasted_content(?:\s[^>]*)?>/g;
  const PASTE_WHOLE = /^\s*<pasted_content(?:\s[^>]*)?>\n?([\s\S]*?)\n?<\/pasted_content(?:\s[^>]*)?>\s*$/;
  // → { inner: the words without tags, parts: [{text} | {paste}] when something was pasted among them }
  function pasteParts(t) {
    const opens = (t.match(PASTE_OPEN) || []).length;
    const whole = opens === 1 && t.match(PASTE_WHOLE);
    if (!opens || whole) return { inner: whole ? whole[1] : t, parts: null };
    const parts = [];
    let at = 0;
    for (const m of t.matchAll(PASTE_BLOCK)) {
      if (t.slice(at, m.index).trim()) parts.push({ text: t.slice(at, m.index).trim() });
      parts.push({ paste: m[1] });
      at = m.index + m[0].length;
    }
    if (t.slice(at).trim()) parts.push({ text: t.slice(at).trim() });
    return { inner: t.replace(PASTE_TAG, ''), parts: parts.some(x => x.paste !== undefined) ? parts : null };
  }

  // The [[A2A…]] header an agent message starts with (agent_link.format_delivery /
  // format_handoff, or format_blocked: `[[A2A（未轉達）：` / `[[A2A (not passed on):`, a message the
  // chain limit stopped, which still came, for reference only). It can hold a [[SF:TO:…]] of its own,
  // so it ends at the ]] that closes its own [[, not at the first one. null when there is none, or it
  // never closes. (agent_link.header_parts is the same rule.)
  function a2aHeader(t) {
    const open = t.match(/^\s*\[\[A2A(?:[:：]|\s*[（(](?:未轉達|not passed on)[)）]\s*[:：])/);
    if (!open) return null;
    let depth = 1, i = open[0].length;
    while (i < t.length && depth) {
      if (t.startsWith('[[', i)) { depth++; i += 2; } else if (t.startsWith(']]', i)) { depth--; i += 2; } else i++;
    }
    if (depth) return null;
    const rest = t.slice(i).replace(/^\s+/, '');
    return { inner: t.slice(open[0].length, i - 2), length: t.length - rest.length, blocked: /未轉達|not passed on/.test(open[0]) };
  }

  const pastedLabel = (n, zh) => (zh ? `貼上的內容 · ${n} 行` : `Pasted · ${n} ${n === 1 ? 'line' : 'lines'}`);

  // A user turn: the words, minus what ShellBot itself put around them.
  function userView(text) {
    const pasted = pasteParts(String(text || ''));
    let t = pasted.inner;
    const tags = [];
    const a2a = pasted.parts ? null : a2aHeader(t);     // a header in something the person pasted is theirs
    if (a2a) {
      const who = a2a.inner.match(/「([^」]+)」|"([^"]+)"/) || [];
      const tag = { kind: 'a2a', who: who[1] || who[2] || '' };
      if (a2a.blocked) tag.blocked = true;                // came as data: the chain limit stopped it
      tags.push(tag);
      t = t.slice(a2a.length);
    }
    // Cursor Agent stores a prompt stamped and wrapped: <timestamp>…</timestamp><user_query>…</user_query>
    t = t.replace(/^\s*<timestamp>[^<]*<\/timestamp>\s*/i, '');
    const q = t.match(/^\s*<user_query>([\s\S]*)<\/user_query>\s*$/i);
    if (q) t = q[1];
    if (TG_TOKEN.test(t)) {      // Telegram's reply-marker instruction is a paragraph of its own
      t = t.split(/\n{2,}/).filter(p => !TG_TOKEN.test(p)).join('\n\n');
      tags.push({ kind: 'telegram' });
    }
    return pasted.parts ? { text: t.trim(), tags, parts: pasted.parts } : { text: t.trim(), tags };
  }
  // An agent's words, minus ShellBot's protocol markers. `foldRed`: the [[SF:RED]]
  // question at the end is on screen as buttons, so its tail is left out.
  // `drawn`: the text goes on screen (the timeline), where the hourglass of a yellow marker becomes the icon: it is
  // tagged with a word joiner so a ⏳ an agent typed itself stays a character (previews and Python's rule stay plain text).
  function botView(text, foldRed, drawn) {
    let lines = String(text || '').replace(/(?:\[\[|<<)\/?TG_REPLY_[0-9a-fA-F]+(?:\]\]|>>)/g, '').split('\n');
    const red = lines.findIndex(l => RED_MARK.test(l));
    if (red >= 0) lines = foldRed ? lines.slice(0, red) : lines.filter((_, i) => i !== red);
    return lines.join('\n')
      .replace(/\[\[\s*SF\s*:\s*(?:GREEN|WORKING|MEMORY\s*:\s*SAVED)\s*\]\]/gi, '')
      .replace(/\[\[\s*SF\s*:\s*YELLOW\s*:\s*([^\]]*)\]\]/gi, (_, why) => (drawn ? '⏳\u2060 ' : '⏳ ') + why.trim())
      .trim();
  }

  // One line for a card, a list row or a notification (agent_link.preview_of is the same rule): the words of a
  // message as this view draws them (userView, botView), whitespace folded to one space, at most `limit`
  // characters, the last one "…" when it was cut. A message from another agent leads with its sender
  // ("研究：…", "排程（未轉達）：…" when the chain limit stopped it); something pasted among the person's own words
  // is "貼上的內容 · N 行"; the lines the harness typed (a rule, a <tag>, a picture's placeholder, a
  // <system-reminder>) are not words. opts.agent: the text is an agent's own words (only its markers go);
  // opts.lang: 'zh…' or not. Not redacted, markdown left alone: that is for the caller.
  // The first line of what ShellBot asks an open bot to save its memory with (bot_memory.MARK). The round it
  // starts (that message, the bot's tool calls and words after it, up to the next message) is one line in the
  // conversation (buildTimeline), and the request is no message a card or a list has anything to say about.
  const MEMORY_MARK = '[[ShellBot: memory autosave]]';
  const NOISE_LINE = ['---', '[[SF:', '<', '[Image'];
  const NOTICE = /^\[\[ShellBot[:：].*\]\]$/;     // ShellBot's own line ("this waited 2 h in the queue"): words only when alone
  const READ_MAX = 20000;      // characters read: a line of at most `limit` never depends on more, and a paste can be megabytes
  function preview(text, limit, opts) {
    const o = opts || {};
    const zh = isZh(o.lang);
    if (!(limit > 0)) return '';
    text = String(text || '');
    if (text.length > READ_MAX) text = Array.from(text).slice(0, READ_MAX).join('');     // code points, as Python counts them
    let who = null, blocked = false, words;
    if (o.agent) {
      words = botView(text, false);
    } else {
      const u = userView(text);
      if (u.text.startsWith(MEMORY_MARK)) return '';        // ShellBot's own request to a bot: nobody said it
      const a = u.tags.find(x => x.kind === 'a2a');
      if (a) { who = a.who || ''; blocked = !!a.blocked; }
      words = u.parts ? u.parts.map(x => (x.paste !== undefined ? pastedLabel(x.paste.split('\n').length, zh) : x.text)).join('\n') : u.text;
      const lines = words.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '').split('\n').map(l => l.trim())
        .filter(l => l && !NOISE_LINE.some(n => l.startsWith(n)) && !l.includes('system-reminder'));
      const plain = lines.filter(l => !NOTICE.test(l));
      words = (plain.length ? plain : lines.map(l => l.slice(2, -2))).join('\n');
    }
    let line = words.replace(/\s+/g, ' ').trim();
    if (who !== null) {
      const name = (who || (zh ? '另一個 AI' : 'Another agent')) + (blocked ? (zh ? '（未轉達）' : ' (not passed on)') : '');
      line = line ? name + (zh ? '：' : ': ') + line : name;
    }
    const chars = Array.from(line);                       // code points, as Python counts them
    return chars.length <= limit ? line : chars.slice(0, limit - 1).join('').trimEnd() + '…';
  }

  // turns (chat_view) → what the view draws, in order:
  //   {type:'user', text, tags} {type:'bot', text} {type:'tools', calls} {type:'compact', text} {type:'error', text}
  //   {type:'memory'}
  // Consecutive tool calls become one 'tools' item; nothing is dropped from it.
  function buildTimeline(turns, opts) {
    const o = opts || {};
    const out = [];
    let lastUser = '';
    let saving = false;                  // inside a memory round: the bot's calls and words are not drawn
    const list = turns || [];
    const lastBot = (() => { for (let i = list.length - 1; i >= 0; i--) if (list[i].kind === 'assistant_text' && String(list[i].text || '').trim()) return i; return -1; })();
    list.forEach((t, i) => {
      const kind = t && t.kind;
      if (saving && (kind === 'tool_call' || kind === 'assistant_text')) return;
      if (kind === 'user_msg') saving = false;
      if (kind === 'tool_call') {
        const prev = out[out.length - 1];
        const call = { tool: t.tool || '', target: t.target || '', ts: t.ts || 0 };
        if (prev && prev.type === 'tools') prev.calls.push(call);
        else out.push({ type: 'tools', calls: [call], ts: t.ts || 0 });
      } else if (kind === 'user_msg') {
        if (isCompaction(t)) { out.push({ type: 'compact', text: String(t.text || ''), ts: t.ts || 0 }); return; }
        const pic = String(t.text || '').match(/^\s*\[Image: source: ([^\]\n]+)\]\s*$/);   // Claude's record of an attached picture
        if (pic) { out.push({ type: 'file', path: pic[1].trim(), ts: t.ts || 0 }); return; }
        const u = userView(t.text);
        if (!u.text) return;               // Codex's turn-start record carries no words
        if (u.text.startsWith(MEMORY_MARK)) { saving = true; out.push({ type: 'memory', ts: t.ts || 0 }); return; }
        lastUser = u.text;
        // Another agent's message (A2A): its sender, not the person. The server names it
        // (`from`, agent_link.sender_of, what a phone reads); the header is the same rule.
        // `blocked`: the chain limit stopped it and it came as data only, so it says so.
        const a2aTag = u.tags.find(x => x.kind === 'a2a') || {};
        const peer = (t.from && t.from.kind === 'a2a') ? t.from.name : a2aTag.who;
        if (peer !== undefined) {
          const item = { type: 'peer', who: peer || '', text: u.text, ts: t.ts || 0 };
          if ((t.from && t.from.kind === 'a2a' && t.from.blocked) || a2aTag.blocked) item.blocked = true;
          out.push(item);
          return;
        }
        out.push(u.parts ? { type: 'user', text: u.text, tags: u.tags, parts: u.parts, ts: t.ts || 0 }
                         : { type: 'user', text: u.text, tags: u.tags, ts: t.ts || 0 });
      } else if (kind === 'assistant_text') {
        const raw = String(t.text || '').trim();
        if (!raw || CODEX_CONTEXT.test(raw)) return;           // reasoning, or Codex's own context
        if (raw === lastUser) return;                          // Codex repeats the prompt as a message
        const prev = out[out.length - 1];
        const text = botView(raw, !!o.foldRed && i === lastBot, true);
        if (!text) return;
        if (prev && prev.type === 'bot' && prev.text === text) return;   // Codex writes each answer twice
        out.push({ type: 'bot', text, ts: t.ts || 0 });
      } else if (kind === 'error') {
        const prev = out[out.length - 1];
        if (prev && prev.type === 'error' && prev.text === String(t.text || '')) { prev.times = (prev.times || 1) + 1; return; }
        out.push({ type: 'error', text: String(t.text || ''), ts: t.ts || 0 });
      }
    });
    return out;
  }
  // The tool call a question is about: the last one since the last user turn.
  function pendingCall(turns) {
    const list = turns || [];
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].kind === 'user_msg') return null;
      if (list[i].kind === 'tool_call') return { tool: list[i].tool || '', target: list[i].target || '' };
    }
    return null;
  }

  // ── rules: markdown, safely ──────────────────────────────────────────────
  // Agent text is untrusted and this page can type into every tab, so: escape
  // everything first, then add a fixed set of tags. No HTML from the text survives,
  // and a link is only ever http(s), opened outside the page.
  const escHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function inline(src) {
    const keep = [];
    const hold = html => `\u0000${keep.push(html) - 1}\u0000`;
    let s = escHtml(src);
    s = s.replace(/`([^`\n]+)`/g, (_, c) => hold(`<code>${c}</code>`));
    s = s.replace(/\[([^\]\n]{1,300})\]\((https?:\/\/[^\s)]+)\)/g,
      (_, label, url) => hold(`<a class="cm-link" href="#" data-url="${url}">${label}</a>`));
    s = s.replace(/(^|[\s(（])(https?:\/\/[^\s<()（）]+[^\s<()（）.,;:!?。，；：！？'"])/g,
      (_, pre, url) => pre + hold(`<a class="cm-link" href="#" data-url="${url}">${url}</a>`));
    s = s.replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?![\w*])/g, '$1<em>$2</em>');
    s = s.replace(/~~([^~\n]+?)~~/g, '<del>$1</del>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => keep[+i]);
  }
  const CODE_FOLD = 24;                    // lines of code shown before "show all"
  function codeBlock(body, lang, zh) {
    const lines = body.split('\n');
    const long = lines.length > CODE_FOLD;
    const more = zh ? `顯示全部 ${lines.length} 行` : `Show all ${lines.length} lines`;
    return `<div class="cm-code${long ? ' is-long' : ''}">` +
      (lang ? `<span class="cm-code-lang">${escHtml(lang)}</span>` : '') +
      `<button class="cm-copy" data-act="copy-code" data-label="${zh ? '複製' : 'Copy'}" aria-label="${zh ? '複製程式碼' : 'Copy the code'}"></button>` +
      `<pre><code>${escHtml(body)}</code></pre>` +
      (long ? `<button class="cm-more" data-act="code-more">${more}</button>` : '') + '</div>';
  }
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const cells = l => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
  function md(src, zh, depth) {
    const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n');
    const para = [];
    let html = '', i = 0;
    const flush = () => { if (para.length) { html += `<p>${para.map(inline).join('<br>')}</p>`; para.length = 0; } };
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+#.-]*)/);
      if (fence) {
        flush();
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
        i++;
        html += codeBlock(body.join('\n'), fence[2], zh);
        continue;
      }
      if (!line.trim()) { flush(); i++; continue; }
      const h = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
      if (h) { flush(); html += `<div class="cm-h cm-h${h[1].length}">${inline(h[2])}</div>`; i++; continue; }
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); html += '<hr>'; i++; continue; }
      if (/^\s*>/.test(line) && (depth || 0) < 3) {
        flush();
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ''));
        html += `<blockquote>${md(q.join('\n'), zh, (depth || 0) + 1)}</blockquote>`;
        continue;
      }
      if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
        flush();
        const head = cells(line);
        i += 2;
        let body = '';
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
          body += `<tr>${cells(lines[i++]).map(c => `<td>${inline(c)}</td>`).join('')}</tr>`;
        }
        html += `<div class="cm-table"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead>` +
                `<tbody>${body}</tbody></table></div>`;
        continue;
      }
      const li = line.match(/^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/);
      if (li) {
        flush();
        const lvl = Math.min(4, Math.floor(li[1].replace(/\t/g, '  ').length / 2));
        const mark = /\d/.test(li[2]) ? escHtml(li[2]) : '•';
        html += `<div class="cm-li" style="--lvl:${lvl}"><span class="cm-mark">${mark}</span><span>${inline(li[3])}</span></div>`;
        i++;
        continue;
      }
      para.push(line);
      i++;
    }
    flush();
    return html;
  }

  // A tool call's kind in a word, for the question card.
  function callWord(call, lang) {
    const w = { run: ['Command', '指令'], edit: ['File', '檔案'], read: ['File', '檔案'], web: ['Web', '網址'],
                search: ['Looking for', '要找'], agent: ['Sub-task', '子任務'], mcp: ['Tool', '工具'] }[toolKind(call.tool)];
    return w ? w[isZh(lang) ? 1 : 0] : call.tool;
  }
  // Under a question, what it is about: its dialog's own words when the screen gave them
  // (`detail`: whole, as the CLI wrote them). Else the call it is about, which agent_status
  // keeps as a command's first line, 40 characters: shown as its start, with the terminal
  // offered. A question not put in plain words is also shown as written.
  function askContext(d, m, call, lang) {
    const zh = isZh(lang);
    const toTerm = `<button class="cm-more-text" data-act="to-term">${escHtml(zh ? '完整內容看終端機' : 'See it all in the terminal')}</button>`;
    const asked = !m.questionTranslated && m.question && m.translated
      ? `<div class="cm-ask-ctx"><code>${escHtml(m.question)}</code></div>` : '';
    if (d && d.detail) return `<pre class="cm-ask-detail">${escHtml(d.detail)}</pre>` + asked + (/…$/.test(d.detail) ? toTerm : '');
    if (asked || !call || !call.target) return asked;
    const whole = ['edit', 'read'].includes(toolKind(call.tool));       // a file is named whole
    return `<div class="cm-ask-ctx"><span class="cm-ask-k">${escHtml(callWord(call, lang) + (whole ? '' : zh ? '開頭' : ' (start)'))}</span>` +
      `<code>${escHtml(call.target + (whole ? '' : '…'))}</code></div>` + (whole ? '' : toTerm);
  }

  const RULES = { isZh, CHAT_PROVIDERS, guessProvider, commandNamed, plainName, mapOption, mapQuestion, mapDecision, toolKind, toolSummary,
                  plainDoing, runWords, isCompaction, userView, botView, preview, pastedLabel, buildTimeline, pendingCall, askContext,
                  md, inline, escHtml, OPTION_RULES, QUESTION_RULES };

  if (typeof module === 'object' && module.exports) module.exports = RULES;
  root.SFChatRules = RULES;
})(typeof window !== 'undefined' ? window : globalThis);
