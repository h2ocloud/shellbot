/* ShellBot bots: the faces' parts (shapes, colours) and the face a bot gets when nobody picked one.
 *
 * Split out of web/bots.js so a page without the sidebar (the phone app's office, web/office_app_host.js) draws the
 * same faces from the same table: bots.js and office.js both take them from SFBotsKit. Pure: no DOM, no network;
 * bots.py SHAPES / COLORS / default_avatar are the same table and the same hash (tests_bots.py, tests_link_bots.py). */
(function (root) {
  'use strict';

  // ── atoms ──────────────────────────────────────────────────────────────
  // viewBox 0 0 100 100. `eyes` is the centre of the eye pair.
  const SHAPES = {
    round:    { name: ['Round', '圓'], eyes: [54, 48], body: '<circle class="body" cx="50" cy="51" r="42"/>' },
    pill:     { name: ['Pill', '膠囊'], eyes: [55, 51], body: '<rect class="body" x="6" y="26" width="88" height="50" rx="25"/>' },
    squircle: { name: ['Squircle', '方圓'], eyes: [54, 48], body: '<rect class="body" x="10" y="11" width="80" height="80" rx="25"/>' },
    blob:     { name: ['Blob', '軟糖'], eyes: [54, 47], body: '<path class="body" d="M51 8c22 0 41 15 41 40 0 27-18 44-43 44C25 92 8 76 8 52 8 27 28 8 51 8z"/>' },
    drop:     { name: ['Drop', '水滴'], eyes: [52, 63], body: '<path class="body" d="M50 9C57 25 82 44 82 63a32 32 0 0 1-64 0C18 44 43 25 50 9z"/>' },
    hex:      { name: ['Hexagon', '六角'], eyes: [53, 50], body: '<path class="body-round" stroke-width="12" transform="rotate(-8 50 50)" d="M50 13L82 31.5L82 68.5L50 87L18 68.5L18 31.5Z"/>' },
    cloud:    { name: ['Cloud', '雲'], eyes: [55, 57], body: '<g class="body"><circle cx="30" cy="58" r="20"/><circle cx="52" cy="44" r="25"/><circle cx="72" cy="58" r="20"/><rect x="30" y="54" width="42" height="24"/></g>' },
    ghost:    { name: ['Ghost', '小幽靈'], eyes: [54, 47], body: '<path class="body" d="M16 50a34 34 0 0 1 68 0v34q-5.67 6-11.33 0t-11.33 0t-11.33 0t-11.33 0t-11.33 0t-11.35 0z"/>' },
    tilt:     { name: ['Tilted', '歪方'], eyes: [54, 49], body: '<rect class="body" x="13" y="14" width="74" height="74" rx="22" transform="rotate(-14 50 51)"/>' },
    star:     { name: ['Star', '星'], eyes: [50, 55], body: '<path class="body-round" stroke-width="14" d="M50 16L65.9 32.2L86.1 42.3L75.7 62.3L72.3 84.7L50 81L27.7 84.7L24.3 62.3L13.9 42.3L34.1 32.2Z"/>' },
    egg:      { name: ['Egg', '蛋'], eyes: [53, 48], body: '<ellipse class="body" cx="50" cy="52" rx="41" ry="33" transform="rotate(-24 50 52)"/>' },
    tri:      { name: ['Triangle', '三角'], eyes: [50, 62], body: '<path class="body-round" stroke-width="14" d="M50 16L88 80L12 80Z"/>' },
  };
  const PALETTE = {
    red: '#F04E5E', orange: '#F57C2B', amber: '#F0A934', yellow: '#DDBB3C',
    green: '#3FB97A', teal: '#1FA99E', sky: '#38B2E8', blue: '#3E7BF6',
    indigo: '#6A5CF0', purple: '#A15BF0', pink: '#F0679E', brown: '#A8714C',
    gray: '#8B90A0', ink: '#23262F',
  };
  const DEFAULT_COLORS = ['red', 'orange', 'amber', 'green', 'teal', 'sky',
                          'blue', 'indigo', 'purple', 'pink', 'brown', 'yellow'];

  function hash(s) {
    let h = 0x811c9dc5;
    for (const ch of String(s)) { h ^= ch.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
    return h >>> 0;
  }
  function defaultAvatar(seed) {
    const h = hash(seed || '');
    const keys = Object.keys(SHAPES);
    return { shape: keys[h % keys.length], color: DEFAULT_COLORS[(h >>> 8) % DEFAULT_COLORS.length] };
  }

  const kit = { SHAPES, PALETTE, DEFAULT_COLORS, hash, defaultAvatar };
  if (typeof module === 'object' && module.exports) module.exports = kit;
  root.SFBotsKit = kit;
})(typeof window !== 'undefined' ? window : globalThis);
