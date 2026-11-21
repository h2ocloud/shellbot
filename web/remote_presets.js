/* The「新增 session」dialog for a paired computer: which buttons it shows.
 *
 * The other computer says what it can open (GET /link/presets, link_presets.py): its own presets,
 * name and command, and optionally which one is its default (config "default_preset"). The dialog
 * draws those, the default as the primary button and first. An older ShellFrame over there (no such
 * route), an error or a timeout gives no list, and the dialog shows the buttons it always had.
 * The custom-command field stays either way.
 */
(function (root) {
  'use strict';

  const FALLBACK = [
    { label: 'Claude', cmd: 'claude', primary: true },
    { label: 'Codex', cmd: 'sf-codex --dangerously-bypass-approvals-and-sandbox --search --no-alt-screen', primary: false },
    { label: 'bash', cmd: 'bash', primary: false },
  ];

  // res: the parsed answer of link_remote_presets (or anything else: null, an error, junk).
  function buttons(res) {
    const list = (res && res.success && Array.isArray(res.presets)) ? res.presets : [];
    const out = [];
    for (const p of list) {
      if (!p || typeof p.name !== 'string' || typeof p.cmd !== 'string') continue;
      const label = p.name.trim(), cmd = p.cmd.trim();
      if (label && cmd) out.push({ label, cmd, primary: false });
    }
    if (!out.length) return FALLBACK.map(b => Object.assign({}, b));
    const def = (typeof res.default === 'string') ? res.default : '';
    const at = def ? out.findIndex(b => b.label === def) : -1;
    if (at >= 0) {
      const [d] = out.splice(at, 1);
      d.primary = true;
      out.unshift(d);
    }
    return out;
  }

  const api = { buttons, FALLBACK };
  root.SFRemotePresets = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
