#!/usr/bin/env python3
"""The office page as the phone app's page: window.SFOfficeApp present = read only, touch first.

The app opens web/office.html in its own web view and injects window.SFOfficeApp (flags) and window.SFOfficeHost (the same
host the desktop gives, as far as the phone can do it). Here both are stand-ins that record every call and hand back the
page's own demo data (made-up names; nothing real). Checks:
  - the flag turns the page into app mode (body.app); with only the host and no flag the page is the desktop one
    (a touch long-press on a bot's avatar still starts a seat drag; in app mode it never does, and nothing is written)
  - the controls that write are not there: the conversation tab and its box, 請他離開, the accessory library, the
    group card, 還原座位, the board card's 回覆／我做好了／留言, the calendar's switch and "ask the owner to change it",
    the object popup's actions (only 編輯執掌, and not at all when the app cannot open a bot's settings)
  - tapping a bot opens the card; its one button, 在對話頁開啟, calls host.chat.open(key) (and is gone when the app cannot
    open a chat); the board's "tell the other computer's main bot" does the same
  - nothing is written through host.set for the shared keys (seats, items, groups, arrange); no page error
  - a link never navigates the page: it goes to host.chat.openUrl (only http/https); window.open and alert do nothing
  - bot-controlled text (names, titles, duties, events, loops, helpers, the other computer's name and board) is escaped in
    the panel, the board, the calendar: no <img onerror> runs, the words show as typed
  - every cover has a visible close button big enough for a finger, and it closes
  - a tap on a name plate opens its second line (no hover on a phone), another tap closes it
  - the page stops drawing while the app says it is in the background (window.__sfInactive + visibilitychange)
  - the safe-area insets are in the page's styles; touch-action is manipulation; no sideways scroll at iPhone landscape,
    iPad and iPhone portrait
Headless WebKit (the app's engine) and Chromium. Screenshots (Chromium): qa-shots/office-app-mode-phone.png (932x430),
qa-shots/office-app-mode-pad.png (1194x834). Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_app_mode_ui.py
"""
import functools
import http.server
import json
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_office_app_mode_ui.py（沒裝 playwright）")
    sys.exit(0)

SHARED = ("seats", "items", "groups", "arrange")
PAYLOAD = '<img src=x onerror="window.__pwned=1">'
TEXT_KEYS = {"name", "title", "duty", "next", "text", "description", "reason", "next_step", "why", "team", "cli",
             "parent_name", "latest", "doing", "desc", "label", "responsibility"}


def taint(o):
    """Every field a bot or a paired computer controls and the page shows: the payload in front."""
    if isinstance(o, dict):
        return {k: (PAYLOAD + v if isinstance(v, str) and k in TEXT_KEYS else taint(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [taint(x) for x in o]
    return o


# The stand-ins. Everything the page calls on the host is recorded in window.__calls as [name, ...args].
STUB = r"""
(() => {
const DATA = %s, FLAGS = %s, WITH_APP = %s;
window.__calls = []; window.__pwned = undefined;
const rec = (n, ...a) => { window.__calls.push([n, ...a]); };
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
if (WITH_APP) window.SFOfficeApp = Object.assign({v: 1, lang: 'zh', uiScale: 1, idiom: 'phone', prefs: {}, pairedIds: ['peer1'],
  caps: {editBot: true, openChat: true}, active() {}}, FLAGS);
const objs = {
  list: async (id, a) => ({success: true, groups: [], items: [{id: 'bot:eng', title: '示範工程師', subtitle: '寫程式'}], total: 1}),
  item: async (id, iid) => (rec('objects.item', id, iid), {success: true, item: {title: '示範工程師', markdown: '執掌：寫程式。[說明](https://example.com/duty)', meta: [], chips: [], links: [],
      actions: ['edit_duty', 'keep', 'tell'], owner: '示範工程師', owner_key: 'b:eng'}}),
  action: async (id, iid, aid, args) => (rec('objects.action', id, iid, aid), aid === 'edit_duty' ? {success: true, ok: true, go: {settings: 'eng'}} : {success: true, ok: true}),
  view: async id => ({success: false}), live: async () => ({success: true}),
};
window.SFOfficeHost = {
  lang: () => 'zh', uiScale: () => 1, boardOnly: () => false, isBoardKey: () => false,
  get: k => (rec('get', k), (window.SFOfficeApp && window.SFOfficeApp.prefs ? window.SFOfficeApp.prefs[k] : null) ?? null),
  set: (k, v) => rec('set', k),
  close: () => rec('close'), objectsChanged: () => rec('objectsChanged'),
  editBot: role => (rec('editBot', role), true),
  md: t => esc(t).replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a class="cm-link" data-url="$2">$1</a>'),
  chat: {has: key => true, open: key => (rec('chat.open', key), true), openUrl: u => rec('chat.openUrl', u)},
  board: {
    detail: async id => ({success: true, task: {events: [{ts: Date.now() / 1000 - 600, actor: 'eng', kind: 'note', text: '示範：進度更新'}]}}),
    schedules: async () => ({ok: true, unsupported: false, configured: true, launchd_ok: true, at: Date.now(), rows: [
      {id: 'demo.backup', title: '示範每日備份', when: {en: 'Daily 02:30', zh: '每天 02:30'}, enabled: true, running: false, kind: 'daily', count: 0, owner: 'ops', toggle: true, state: '',
       slots: {interval: null, calendar: [{Hour: 2, Minute: 30}]}}]}),
    canRequest: () => false, takeFocus: () => null,
  },
  objects: objs,
};
window.__snap = DATA;
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


OBJECTS = [
    {"id": "bookshelf", "title": "書櫃", "furniture": "bookshelf", "place": "lobby:wall", "button": True, "badge": 0, "filters": [], "sorts": [], "actions": []},
    {"id": "personnel", "title": "人事檔案櫃", "short": "人事", "furniture": "cabinet", "place": "lobby:wall", "button": True, "badge": 0, "filters": [], "sorts": [],
     "actions": [{"id": "edit_duty", "label": "編輯執掌", "primary": True}, {"id": "keep", "label": "保留"}, {"id": "tell", "label": "請他改…"}]},
]


def open_page(browser, base, snap, *, app=True, flags=None, size=(932, 430), touch=False, tainted=False):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, has_touch=touch, locale="zh-TW")
    page = ctx.new_page()
    errors, dialogs = [], []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.add_init_script(STUB % (json.dumps(taint(snap) if tainted else snap, ensure_ascii=False), json.dumps(flags or {}), "true" if app else "false"))
    page.goto(base + "/office.html")
    page.wait_for_function("window.Office && window.__office", timeout=15000)
    page.evaluate("(s) => Office.update(s)", taint(snap) if tainted else snap)
    page.wait_for_function("Object.keys(__office.assignments()).length >= 8", timeout=10000)
    page.evaluate("__office.pause(true)")
    return ctx, page, errors, dialogs


def calls(page, name=None):
    c = page.evaluate("window.__calls")
    return [x for x in c if name is None or x[0] == name]


def visible(page, sel):
    return page.evaluate("""(s) => { const el = document.querySelector(s); if (!el) return false; const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') return false;
        for (let p = el; p; p = p.parentElement) { const ps = getComputedStyle(p); if (ps.display === 'none' || ps.visibility === 'hidden') return false; }
        const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }""", sel)


def box(page, sel):
    return page.evaluate("(s) => { const el = document.querySelector(s); if (!el) return null; const r = el.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }", sel)


def tap_ent(page, key):
    p = page.evaluate("(k) => __office.entScreen(k)", key)
    page.mouse.click(p["x"], p["y"])


TOUCH = """([x, y, type, id]) => { const cv = document.getElementById('stage');
    cv.dispatchEvent(new PointerEvent(type, {pointerId: id || 7, pointerType: 'touch', isPrimary: true, clientX: x, clientY: y, button: 0, buttons: type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true})); }"""


def run(browser, base, engine, snap):
    shoot = engine == "chromium"
    SHOTS.mkdir(exist_ok=True)

    print(f"[{engine}] app mode vs the desktop page, long press")
    ctx, page, errors, dialogs = open_page(browser, base, snap, app=False)
    p = page.evaluate("__office.entScreen('b:eng')")
    page.evaluate(TOUCH, [p["x"], p["y"], "pointerdown"])
    page.wait_for_timeout(700)
    check("control: with the host but no SFOfficeApp the page is the desktop one (long press starts a seat drag)",
          page.evaluate("document.getElementById('stage').classList.contains('seat')") and not page.evaluate("document.body.classList.contains('app')"))
    page.evaluate(TOUCH, [p["x"], p["y"], "pointerup"])
    ctx.close()

    ctx, page, errors, dialogs = open_page(browser, base, snap)
    check("SFOfficeApp turns on app mode", page.evaluate("document.body.classList.contains('app')"))
    seat0 = page.evaluate("__office.seatOf('b:eng')")
    p = page.evaluate("__office.entScreen('b:eng')")
    page.evaluate(TOUCH, [p["x"], p["y"], "pointerdown"])
    page.wait_for_timeout(700)
    check("a long press on a bot's avatar does not start a seat drag", not page.evaluate("document.getElementById('stage').classList.contains('seat')"))
    page.evaluate(TOUCH, [p["x"] + 160, p["y"] + 20, "pointermove"])
    page.evaluate(TOUCH, [p["x"] + 160, p["y"] + 20, "pointerup"])
    check("…and nobody changed seats; nothing was written", page.evaluate("__office.seatOf('b:eng')") == seat0 and not calls(page, "set"),
          (seat0, calls(page, "set")))
    page.evaluate("__office.setZoning('custom')")
    check("the 分區 choice is a display setting: it may be written, the shared keys never are",
          not [c for c in calls(page, "set") if c[1] in SHARED], calls(page, "set"))

    print(f"[{engine}] tapping a bot")
    page.evaluate("__office.setZoning('cli')")
    page.wait_for_timeout(300)
    tap_ent(page, "b:eng")
    page.wait_for_function("__office.following() === 'b:eng'", timeout=5000)
    page.wait_for_timeout(500)
    check("a tap opens the card: no conversation tab, no box, no 請他離開, no group card, no 還原座位",
          not visible(page, "#pTabs") and not visible(page, "#cComp") and not visible(page, "#pChat") and not visible(page, "#dismiss")
          and not visible(page, "#pGroup") and not visible(page, "#resetSeats") and visible(page, "#pInfo"))
    check("…the accessory library is not offered; the accessories are shown",
          page.evaluate("document.querySelectorAll('#pItems [data-act=toggle], #pItems [data-item], #pItems [data-act=auto]').length") == 0
          and page.evaluate("document.querySelectorAll('#pItems .ichip').length") >= 1)
    check("…its one button is 在對話頁開啟", visible(page, "#pOpenChat") and "對話頁" in page.inner_text("#pOpenChat"))
    page.click("#pOpenChat")
    check("…and it calls host.chat.open with that bot's key; nothing else", calls(page, "chat.open") == [["chat.open", "b:eng"]], calls(page))
    check("…and no 拖曳頭像可換位子 hint", "拖曳頭像" not in page.inner_text("#pMeta"))
    if shoot:
        page.wait_for_timeout(3000)         # the 分區 toasts above are gone
        page.screenshot(path=str(SHOTS / "office-app-mode-phone.png"))
    cb = box(page, "#pClose")
    check("the card has a close button a finger can hit, and it closes", visible(page, "#pClose") and cb["w"] >= 36 and cb["h"] >= 36)
    page.click("#pClose")
    check("…closed", not page.evaluate("__office.panelOpen()"))
    page.evaluate("__office.follow('t:tmp1')")
    page.wait_for_timeout(300)
    check("an intern's card: no 請他離開", not visible(page, "#dismiss"))
    page.evaluate("__office.unfollow()")
    helper = page.evaluate("(__office.helpers()[0] || {}).key")
    if helper:
        page.evaluate("(k) => __office.follow(k)", helper)
        page.wait_for_timeout(300)
        btn = page.query_selector("#pTask [data-act=parent-chat]")
        check("a part-timer's card: the parent's conversation opens on the chat page", btn is not None)
        if btn:
            btn.click()
            check("…as host.chat.open of the one who sent it", len(calls(page, "chat.open")) == 2 and calls(page, "chat.open")[1][1].startswith("b:"), calls(page, "chat.open"))
        page.evaluate("__office.unfollow()")

    print(f"[{engine}] the board")
    page.evaluate("__office.openView('board')")
    page.wait_for_selector("#vBody .nb", timeout=5000)
    page.click("#vBody .nb >> nth=0")
    page.wait_for_selector("#vOver .wcard", timeout=5000)
    page.wait_for_timeout(300)
    check("a task card: no 回覆／我做好了／留言, no form",
          page.evaluate("document.querySelectorAll('#vOver .wb-actions, #vOver [data-act=areply], #vOver [data-act=adone], #vOver [data-act=anote], #vOver form').length") == 0)
    check("…the card's close button closes only the card", visible(page, "#vOver .wx"))
    page.click("#vOver .wx")
    check("…closed", page.evaluate("document.querySelectorAll('#vOver .wcard').length") == 0)
    vb = box(page, "#vClose")
    check("the board has a close button a finger can hit", visible(page, "#vClose") and vb["w"] >= 36 and vb["h"] >= 36)
    page.evaluate("__office.openView('board:peer1')")
    page.wait_for_selector("#vBody .nb", timeout=5000)
    page.click("#vBody .nb >> nth=0")
    page.wait_for_selector("#vOver [data-act=tellmain]", timeout=5000)
    check("the other computer's card: 跟他說 becomes 在對話頁開啟", "在對話頁開啟" in page.inner_text("#vOver [data-act=tellmain]"))
    page.click("#vOver [data-act=tellmain]")
    last = calls(page, "chat.open")[-1]
    check("…host.chat.open of that computer's main bot", last[1].startswith("p:peer1/"), last)
    page.evaluate("__office.closeView()")

    print(f"[{engine}] the calendar")
    page.evaluate("__office.openView('cal')")
    page.wait_for_selector("#vBody [data-act=ev]", timeout=8000)
    page.click("#vBody [data-act=ev] >> nth=0")
    page.wait_for_selector("#calPop .cp", timeout=5000)
    check("the schedule's card: no switch, no frequency box, no 請 … 調整, no 其他設定只能由…",
          page.evaluate("document.querySelectorAll('#calPop [data-act=csw], #calPop [data-act=cfreq], #calPop #calFreq, #calPop #calTime').length") == 0
          and "其他設定只能" not in page.inner_text("#calPop"))
    check("…the state is shown as a badge", page.evaluate("document.querySelectorAll('#calPop .cpst').length") == 1)
    check("…and it has a close button that closes it", visible(page, "#calPop [data-act=cpx]"))
    page.click("#calPop [data-act=cpx]")
    check("…closed", not visible(page, "#calPop"))
    page.evaluate("__office.closeView()")
    check("nothing on the board or the calendar wrote anything",
          not [c for c in calls(page) if c[0] in ("act", "request", "setEnabled", "send") or (c[0] == "set" and c[1] in SHARED)], [c for c in calls(page) if c[0] != "get"])

    print(f"[{engine}] the cabinet (object popup)")
    page.evaluate("__office.objOpen('personnel')")
    page.wait_for_selector("#ooList .oo-it", timeout=5000)
    page.click("#ooList .oo-it >> nth=0")
    page.wait_for_selector("#ooActs", state="attached", timeout=5000)
    page.wait_for_function("document.querySelectorAll('#oo-view a.cm-link, #ooView a.cm-link').length > 0", timeout=5000)
    acts = page.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.dataset.action)")
    check("the popup's actions are only 編輯執掌 (the host offered three)", acts == ["edit_duty"], acts)
    page.click("#ooView a.cm-link")
    check("a link in the popup goes to host.chat.openUrl, once", calls(page, "chat.openUrl") == [["chat.openUrl", "https://example.com/duty"]], calls(page, "chat.openUrl"))
    page.click("#ooActs [data-action=edit_duty]")
    page.wait_for_timeout(300)
    check("…it asks the app for that bot's settings (host.editBot) and the server action list stays unasked for the others",
          calls(page, "editBot") == [["editBot", "eng"]] and [c[3] for c in calls(page, "objects.action")] == ["edit_duty"], (calls(page, "editBot"), calls(page, "objects.action")))
    check("…and the popup closes itself (the app decides what happens to the office)", not page.evaluate("__office.objects().open"))
    page.evaluate("__office.objOpen('personnel')")
    page.wait_for_selector("#ooList .oo-it", timeout=5000)
    check("the popup has a close button, and it closes", visible(page, "#ooClose") and box(page, "#ooClose")["w"] >= 36)
    page.click("#ooClose")
    check("…closed", not page.evaluate("__office.objects().open"), page.evaluate("JSON.stringify(__office.objects())")[:200])

    print(f"[{engine}] links and dialogs")
    page.evaluate("""() => { const a = document.createElement('a'); a.id = 'x1'; a.href = 'https://example.com/bot'; a.textContent = 'x';
        a.style.cssText = 'position:fixed;left:10px;top:200px;z-index:99;color:#fff'; document.body.appendChild(a);
        const b = document.createElement('a'); b.id = 'x2'; b.href = 'javascript:window.__pwned=2'; b.textContent = 'y'; b.style.cssText = 'position:fixed;left:60px;top:200px;z-index:99;color:#fff'; document.body.appendChild(b); }""")
    url0 = page.url
    page.click("#x1")
    page.click("#x2")
    page.wait_for_timeout(200)
    check("a link does not navigate the page; it is handed to host.chat.openUrl", page.url == url0 and ["chat.openUrl", "https://example.com/bot"] in calls(page, "chat.openUrl"), (page.url, calls(page, "chat.openUrl")))
    check("a javascript: link is not handed on and does not run", all(c[1].startswith("http") for c in calls(page, "chat.openUrl")) and page.evaluate("window.__pwned") is None)
    page.evaluate("window.open('https://example.com/w'); alert('x'); confirm('y'); prompt('z');")
    check("window.open goes to openUrl; no alert, confirm or prompt dialog", ["chat.openUrl", "https://example.com/w"] in calls(page, "chat.openUrl") and not dialogs, dialogs)

    print(f"[{engine}] a tap on a name plate")
    page.evaluate("__office.unfollow(); __office.pause(false); __office.setArea('all')")
    page.wait_for_timeout(1200)
    signs = page.evaluate("__office.signs()")
    hit = None
    for sg in signs:
        x, y = sg["x"] + sg["w"] / 2, sg["y"] + sg["h"] / 2
        page.evaluate(TOUCH, [x, y, "pointerdown"])
        page.evaluate(TOUCH, [x, y, "pointerup"])
        page.wait_for_timeout(60)
        if page.evaluate("__office.hoverSign()") == sg["id"]:
            hit = (sg, x, y)
            break
    check("a tap opens a plate's second line (no hover on a phone)", hit is not None, [s["id"] for s in signs])
    if hit:
        page.evaluate(TOUCH, [hit[1], hit[2], "pointerdown"])
        page.evaluate(TOUCH, [hit[1], hit[2], "pointerup"])
        check("…the same plate tapped again closes it", page.evaluate("__office.hoverSign()") is None)

    print(f"[{engine}] background and styles")
    f0 = page.evaluate("__office.frames()")
    page.evaluate("window.__sfInactive = true; document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(300)
    f1 = page.evaluate("__office.frames()")
    page.wait_for_timeout(700)
    check("the page stops drawing while the app says it is away", page.evaluate("__office.frames()") == f1, (f0, f1))
    page.evaluate("window.__sfInactive = false; document.dispatchEvent(new Event('visibilitychange'))")
    page.evaluate("__office.pause(false)")
    page.wait_for_timeout(700)
    check("…and draws again when it is back", page.evaluate("__office.frames()") > f1)
    css = page.evaluate("[...document.styleSheets].flatMap(s => { try { return [...s.cssRules]; } catch (e) { return []; } }).map(r => r.cssText).join('\\n')")
    check("the safe-area insets are in the styles (top, right, bottom, left)", all(f"safe-area-inset-{s}" in css for s in ("top", "right", "bottom", "left")))
    check("touch-action: manipulation on the page", page.evaluate("getComputedStyle(document.body).touchAction") == "manipulation")
    check("no error on the page", not errors, errors)
    ctx.close()

    print(f"[{engine}] what a bot writes is only text")
    ctx, page, errors, dialogs = open_page(browser, base, snap, tainted=True)
    page.evaluate("__office.follow('b:eng')")
    page.wait_for_timeout(400)
    check("the card shows the markup as typed", "<img src=x" in page.inner_text("#pName") and "<img src=x" in page.inner_text("#pDuty") and "<img src=x" in page.inner_text("#pTask"),
          (page.inner_text("#pName"), page.inner_text("#pDuty")))
    page.evaluate("__office.follow('t:tmp2')")
    page.wait_for_timeout(200)
    helper = page.evaluate("(__office.helpers()[0] || {}).key")
    if helper:
        page.evaluate("(k) => __office.follow(k)", helper)
        page.wait_for_timeout(200)
    page.evaluate("__office.follow('p:peer1/bld')")
    page.wait_for_timeout(200)
    page.evaluate("__office.unfollow()")
    for v in ("board", "board:peer1", "cal"):
        page.evaluate("(v) => __office.openView(v)", v)
        page.wait_for_timeout(500)
        page.evaluate("[...document.querySelectorAll('#vBody .nb, #vBody [data-act=ev]')].slice(0, 3).forEach(b => b.click())")
        page.wait_for_timeout(300)
        page.evaluate("__office.closeView()")
    page.evaluate("__office.objOpen('personnel')")
    page.wait_for_timeout(500)
    page.evaluate("__office.objClose()")
    check("no <img> from a bot's text exists anywhere; nothing ran",
          page.evaluate("document.querySelectorAll('img[src=\"x\"]').length") == 0 and page.evaluate("window.__pwned") is None)
    check("no error on the page", not errors, errors)
    ctx.close()

    print(f"[{engine}] what the app cannot do is not drawn")
    ctx, page, errors, dialogs = open_page(browser, base, snap, flags={"caps": {"editBot": False, "openChat": False}})
    page.evaluate("__office.follow('b:eng')")
    page.wait_for_timeout(400)
    check("openChat false: the card has no 在對話頁開啟", not visible(page, "#pOpenChat"))
    page.evaluate("__office.unfollow()")
    page.evaluate("__office.objOpen('personnel')")
    page.wait_for_selector("#ooList .oo-it", timeout=5000)
    page.click("#ooList .oo-it >> nth=0")
    page.wait_for_function("document.querySelectorAll('#ooView a.cm-link').length > 0", timeout=5000)
    check("editBot false: the popup has no action row", page.evaluate("document.querySelectorAll('#ooActs button').length") == 0 and not visible(page, "#ooActs"))
    ctx.close()

    print(f"[{engine}] sizes")
    for name, size, touch in (("iPhone landscape", (932, 430), True), ("iPad", (1194, 834), True), ("iPhone portrait", (390, 844), True)):
        ctx, page, errors, dialogs = open_page(browser, base, snap, size=size, touch=touch)
        page.wait_for_timeout(600)
        sw = page.evaluate("[document.scrollingElement.scrollWidth, innerWidth]")
        check(f"{name} {size[0]}x{size[1]}: no sideways scroll", sw[0] <= sw[1] + 1, sw)
        top = box(page, "#top")
        check(f"{name}: the top bar stays on screen", top["x"] >= -1 and top["x"] + top["w"] <= size[0] + 1, top)
        page.evaluate("__office.follow('b:eng')")
        page.wait_for_timeout(700)
        pb = box(page, "#panel")
        check(f"{name}: the card fits the screen and its close button is reachable",
              pb["x"] >= -1 and pb["x"] + pb["w"] <= size[0] + 1 and pb["y"] >= -1 and pb["y"] + pb["h"] <= size[1] + 1 and visible(page, "#pClose"), pb)
        if shoot and name == "iPad":
            page.evaluate("__office.unfollow()")
            page.evaluate("__office.openView('board')")
            page.wait_for_selector("#vBody .nb", timeout=5000)
            page.click("#vBody .nb >> nth=0")
            page.wait_for_selector("#vOver .wcard", timeout=5000)
            page.wait_for_timeout(600)
            page.screenshot(path=str(SHOTS / "office-app-mode-pad.png"))
        check(f"{name}: no error on the page", not errors, errors)
        ctx.close()


def snapshot(browser, base):
    """The page's own demo data as a snapshot (made-up names), the way the app would hand it over."""
    page = browser.new_page(viewport={"width": 932, "height": 430})
    page.goto(base + "/office.html")
    page.wait_for_function("window.__office", timeout=15000)
    page.wait_for_timeout(800)
    snap = page.evaluate("__office.db()")
    page.close()
    snap["objects"] = OBJECTS
    return snap


srv = serve()
base = f"http://127.0.0.1:{srv.server_address[1]}"
with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:  # noqa: BLE001
            print(f"SKIP  {engine}: {str(e).splitlines()[0]}")
            continue
        try:
            run(browser, base, engine, snapshot(browser, base))
        finally:
            browser.close()
srv.shutdown()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
