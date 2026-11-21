/**
 * The「新增 session」dialog's buttons for a paired computer (web/remote_presets.js).
 * Its own presets, the default first and primary; no list (an older ShellFrame, an error) → the built-in three.
 *
 * 跑法：node tests_remote_presets.js
 */
const path = require('path');
const { buttons, FALLBACK } = require(path.join(__dirname, 'web/remote_presets.js'));

let fails = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + (detail || '')}`);
  if (!ok) fails++;
}

const fb = buttons(null);
check('no answer: the built-in three', JSON.stringify(fb.map(b => b.label)) === '["Claude","Codex","bash"]', JSON.stringify(fb));
check('…Claude primary as before', fb[0].primary === true && !fb[1].primary && !fb[2].primary);
check('a failure answer falls back', buttons({ success: false, message: 'no such route' }).length === FALLBACK.length);
check('an empty list falls back', buttons({ success: true, presets: [] })[0].label === 'Claude');
check('junk falls back', buttons({ success: true, presets: [null, 3, { name: '', cmd: 'x' }, { name: 'y' }] })[0].label === 'Claude');
check('the fallback is a copy (drawing it cannot change it)', (() => { buttons(null)[0].label = 'z'; return FALLBACK[0].label === 'Claude'; })());

const res = { success: true, default: 'Agent X',
  presets: [{ name: 'Bash', cmd: 'bash' }, { name: 'Agent X', cmd: 'agent-x --go' }, { name: 'Other', cmd: 'other' }] };
const b = buttons(res);
check('the other computer\'s presets', b.length === 3 && b.every(x => typeof x.cmd === 'string'));
check('the default first', b[0].label === 'Agent X' && b[0].cmd === 'agent-x --go');
check('…and the only primary', b[0].primary === true && b.filter(x => x.primary).length === 1);
check('the rest keep their order', b[1].label === 'Bash' && b[2].label === 'Other');
const nd = buttons({ success: true, presets: res.presets });
check('no default: no primary, order kept', nd.every(x => !x.primary) && nd[0].label === 'Bash');
check('a default naming nothing: no primary', buttons(Object.assign({}, res, { default: 'Nope' })).every(x => !x.primary));

if (fails) { console.log(`${fails} FAILED`); process.exit(1); }
console.log('ALL PASS');
