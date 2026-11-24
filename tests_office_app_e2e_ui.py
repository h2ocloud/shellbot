#!/usr/bin/env python3
"""The office in the phone app, end to end in a browser: the real office.html with the real web/office_app_host.js, over a
fake app bridge (window.webkit.messageHandlers.sfoffice) that answers like the desktop's POST /link/office/api.

The other tests check the parts apart (tests_office_app_host.js: the host against a fake page; tests_office_app_mode_ui.py:
the page against a fake host; tests_link_office.py: the desktop's routes). This one checks the seams between them:

  - office.html loads the host's files and finds window.SFOfficeHost; the first snapshot is drawn and `ready` is posted
  - every call the page makes is one the desktop answers: POST /link/office/api, a method on link_office.METHODS, and
    arguments that pass the desktop's own check (link_office's table, run here on what the page really sent)
  - a tap on a bot and 在對話頁開啟 posts the native openChat with that bot's tab
  - SFOfficeApp.active(false) stops the calls, active(true) starts them again
  - the office switched off on the desktop: `error` with why=off, nothing drawn
  - without SFOfficeApp (the desktop's iframe) the host does nothing

Run: .venv/bin/python tests_office_app_e2e_ui.py
"""
import functools
import http.server
import json
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import link_office  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


# What the desktop answers (the shapes link_office.serve hands back inside {success, result}); demo names only.
BRIDGE = r"""
(() => {
const BOTS = { enabled: true, primary: 'main', roster: {
  main: { label: '示範主管', responsibility: '大廳', bot: { primary: true, memory: true }, cli: { label: 'Claude' } },
  qa: { label: '示範測試', responsibility: '檢查', bot: { memory: true } } } };
const TABS = { success: true, now: 1, bots_on: true, tabs: [
  { sid: 's1', label: '示範主管', cmd: 'claude', cli: 'claude', role: 'main', kind: 'bot', state: 'working', line: '寫計畫', narration: '計畫寫好了。', loop: null, opened_at: 1 },
  { sid: 's2', label: '示範草稿', cmd: 'codex', cli: 'codex', role: '', kind: 'temp', state: 'decision', line: '等你決定', narration: '', loop: null, opened_at: 2 } ],
  helpers: {} };
const PEERS = { peers: { PEER1: { name: '示範電腦', primary: 'a', bots: [{ role: 'a', label: '示範甲', sid: 'r1', state: 'idle', latest: '你好' }],
  tabs: {} } }, pending: 0 };
const TASKS = { success: true, decisions: [{ id: 7, title: '示範：選一個', owner: 'main', requester: 'qa', status: 'decision' }], open: [], stuck: [] };
const OBJECTS = { success: true, objects: [] };
const OFF = %s;
const A = {
  bots_state: () => BOTS, office_tabs: () => TABS, link_peer_bots: () => PEERS, tasks_overview: () => TASKS,
  link_peer_tasks: () => ({ peers: { PEER1: { state: 'ok', at: 5, decisions: [], stuck: [], open: [] } }, pending: 0 }),
  office_objects: () => OBJECTS, task_detail: a => ({ success: true, task: { id: a[0], events: [] } }),
  schedules_list: () => ({ schedules: [], configured: true, launchd_ok: true }),
  office_object_list: () => ({ groups: [], items: [], total: 0 }), office_object_item: () => ({ success: true, item: { title: 'x', markdown: '', actions: [] } }),
  office_ops_view: () => ({ success: true, sections: [] }), office_ops_live: () => ({ success: true }),
};
window.__sent = [];
window.webkit = { messageHandlers: { sfoffice: { postMessage(m) {
  window.__sent.push(JSON.parse(JSON.stringify(Object.assign({ t: Date.now() }, m))));
  if (m.op !== 'call') return undefined;
  if (OFF) return Promise.resolve({ success: false, code: 'off', message: 'off' });
  const f = A[m.body && m.body.method];
  return Promise.resolve(f ? { success: true, result: f(m.body.args || []) } : { success: false, code: 'no_method' });
} } } };
if (%s) window.SFOfficeApp = { v: 1, lang: 'zh-TW', uiScale: 1, idiom: 'pad', prefs: %s, pairedIds: ['PEER1'], caps: { editBot: true, openChat: true } };
})();
"""


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    class Server(http.server.ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True

    srv = Server(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def open_page(browser, base, *, app=True, off=False, size=(1194, 834), prefs=None):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, locale="zh-TW")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.add_init_script(BRIDGE % ("true" if off else "false", "true" if app else "false", json.dumps(prefs or {})))
    page.goto(base + "/office.html")
    return ctx, page, errors


def sent(page, op=None):
    return [m for m in page.evaluate("window.__sent") if op is None or m.get("op") == op]


def backend_accepts(msg):
    """The desktop's own answer to this call's shape: the path, the method on its table, the arguments its check takes."""
    if msg.get("method") != "POST" or msg.get("path") != "/link/office/api":
        return "path"
    body = msg.get("body") or {}
    name = body.get("method")
    if name not in link_office.METHODS:
        return f"method {name}"
    try:
        link_office.METHODS[name][0](body.get("args") if body.get("args") is not None else [])
    except (ValueError, TypeError) as e:
        return f"args {name} {body.get('args')}: {e}"
    if body.get("lang") not in ("zh", "en"):
        return f"lang {body.get('lang')}"
    return ""


def run(browser, base, engine):
    print(f"[{engine}] the real page over the bridge")
    ctx, page, errors = open_page(browser, base)
    page.wait_for_function("window.__sent.some(m => m.op === 'ready' || m.op === 'error')", timeout=20000)
    check("the page posts ready (not error) once the first snapshot is drawn",
          sent(page, "ready") and not sent(page, "error"), [m for m in sent(page) if m["op"] != "call"])
    check("office.html took window.SFOfficeHost from office_app_host.js",
          page.evaluate("!!window.SFOfficeAppHost && window.SFOfficeHost === window.SFOfficeAppHost.host"))
    page.wait_for_function("window.__office && Object.keys(__office.assignments()).length >= 2", timeout=10000)
    snap = page.evaluate("""(() => { const s = SFOfficeAppHost.snapshot() || {};
        return {bots: (s.bots || []).map(b => b.id), temps: (s.temps || []).map(t => t.id), peers: (s.peers || []).map(p => p.id)}; })()""")
    check("the snapshot holds the desktop's people (two bots, the temp tab, the other computer)",
          snap["bots"] == ["main", "qa"] and snap["temps"] == ["s2"] and snap["peers"] == ["PEER1"], snap)
    drawn = page.evaluate("['b:main', 't:s2'].map(k => !!__office.entScreen(k))")
    check("…and they are drawn in the room (the bot and the temp have a place on screen)", drawn == [True, True], drawn)
    check("app mode is on", page.evaluate("document.body.classList.contains('app')"))
    check("the legend has no 拖曳頭像 row (no seat moves in the app): really not drawn, not only [hidden]",
          page.evaluate("getComputedStyle(document.querySelectorAll('#legend li')[7]).display") == "none")
    page.wait_for_timeout(3500)                     # one more round of polling
    calls = sent(page, "call")
    bad = [(m["body"].get("method"), why) for m in calls if (why := backend_accepts(m))]
    check(f"every call the page made ({len(calls)}) is one the desktop answers: path, method, arguments, lang", calls and not bad, bad)
    check("…and none is a write", not [m for m in calls if m["body"].get("method") in link_office.WRITE_METHODS])

    print(f"[{engine}] tapping a bot")
    page.evaluate("__office.pause(true)")
    p = page.evaluate("__office.entScreen('b:main')")
    page.mouse.click(p["x"], p["y"])
    page.wait_for_function("__office.following() === 'b:main'", timeout=5000)
    page.wait_for_selector("#pOpenChat", state="visible", timeout=5000)
    page.click("#pOpenChat")
    nat = [m for m in sent(page, "native") if m.get("name") == "openChat"]
    check("在對話頁開啟 posts the native openChat with that bot's tab",
          len(nat) == 1 and nat[0]["args"] == {"key": "b:main", "sid": "s1", "peer": "", "rsid": ""}, nat)
    page.evaluate("__office.pause(false)")

    print(f"[{engine}] background and foreground")
    page.evaluate("SFOfficeApp.active(false)")
    page.wait_for_timeout(300)
    n0 = len(sent(page, "call"))
    page.wait_for_timeout(4000)
    quiet = sent(page, "call")[n0:]
    check("active(false): no call for 4 s (more than one 3 s round)", not quiet, [m["body"]["method"] for m in quiet])
    check("…and the page is told it is away", page.evaluate("window.__sfInactive === true"))
    n1 = len(sent(page, "call"))
    page.evaluate("SFOfficeApp.active(true)")
    page.wait_for_timeout(1000)
    check("active(true): it asks again at once", len(sent(page, "call")) > n1)
    check("no script error on the page", not errors, errors)
    ctx.close()

    print(f"[{engine}] a phone on its side, with the desktop's prefs saying the legend is open")
    ctx, page, errors = open_page(browser, base, size=(844, 390), prefs={"legendMin": False})
    page.wait_for_function("window.__sent.some(m => m.op === 'ready' || m.op === 'error')", timeout=20000)
    check("a low screen starts with the legend folded (the desktop's legendMin=false does not open it here)",
          page.evaluate("document.getElementById('legend').classList.contains('min')"))
    check("…its folded box leaves the room in view (under a third of the height)",
          page.evaluate("document.getElementById('legend').getBoundingClientRect().height") < 390 / 3,
          page.evaluate("document.getElementById('legend').getBoundingClientRect().height"))
    check("no script error on the page", not errors, errors)
    ctx.close()

    print(f"[{engine}] the office off on the desktop")
    ctx, page, errors = open_page(browser, base, off=True)
    page.wait_for_function("window.__sent.some(m => m.op === 'ready' || m.op === 'error')", timeout=20000)
    errs = sent(page, "error")
    check("error why=off, no ready", errs and errs[0].get("why") == "off" and not sent(page, "ready"), sent(page))
    ctx.close()

    print(f"[{engine}] the desktop's iframe (no SFOfficeApp)")
    ctx, page, errors = open_page(browser, base, app=False)
    page.wait_for_function("window.Office && window.__office", timeout=15000)
    page.wait_for_timeout(1500)
    check("without SFOfficeApp the host does nothing: no SFOfficeHost, nothing posted",
          page.evaluate("!window.SFOfficeAppHost && !window.SFOfficeHost && window.__sent.length === 0"), sent(page))
    check("…and the page is not in app mode", not page.evaluate("document.body.classList.contains('app')"))
    ctx.close()


def main():
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    with sync_playwright() as pw:
        for engine in ("webkit", "chromium"):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as e:
                if engine == "chromium":
                    raise
                print(f"[{engine}] not installed, skipped: {str(e).splitlines()[0][:80]}")
                continue
            try:
                run(browser, base, engine)
            finally:
                browser.close()
    srv.shutdown()
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
