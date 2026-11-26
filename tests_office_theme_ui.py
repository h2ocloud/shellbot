#!/usr/bin/env python3
"""Office styles (辦公室風格) and the real sky outside the windows, and the secretary's desk without overlaps.

Requested: an office style picker in Settings → Office, kept in office.json (not localStorage), the same on the phone; the
look only (floor, walls, furniture, the view outside), never the seats or what the status colours mean. Three styles:
night blue (the original, the default), warm wood, nordic light. Outside the windows the
sun rises in the east and sets in the west at this computer's place, and the weather there is drawn. Also reported: on the
secretary's desk (it faces up-left) the default monitor and potted plant overlapped.

Part 1, office.html on its own (demo data, no host), Chromium and WebKit:
  - the default is night blue, and the status colours (COL, SCREEN, ST) are the same in every style
  - every style draws in 3D and flat with no page error, at 12 fps or more while active, rain included
  - nordic light turns the floating toolbar and legend light; the status chips stay dark
  - the sun: in Taipei in late November it is low in the east at 07:00 (left part of the view), high near the middle at
    noon, low in the west at 16:50 (right part), and gone at 22:00 (night); in Sydney the morning sun is on the right
    (the windows face the equator); near the pole in December nothing is NaN
  - weather: the kind given is drawn (rain, snow, fog, cloudy); a stale or missing reading is a clear sky
  - desks: no corner, front or floor spot shares floor with the monitor, on the normal desks and on the secretary's
    ('nx') desk; the secretary's floor spot is not behind the desk and its 3D picture does not cross the monitor's;
    the floor spot does not overlap the lobby's other furniture
Part 2, office.html with a stand-in host:
  - the style comes from the host's prefs (`theme`); the page never writes it
  - night blue never asks for the sky; a style with a view asks once (and uses the place answered)
  - the board alone stays night blue and asks nothing
  - in the phone app (SFOfficeApp) the desktop's style is used
Part 3, the main window (index.html with a stand-in pywebview api):
  - Settings → Office has 辦公室風格 (three choices) and 窗外天氣 (on by default, the place in its hint)
  - picking a style before the office was ever opened reads office.json first and saves the style WITH the seats
  - when office.json cannot be read, nothing is written (the seats are never overwritten by a lone key)
  - an open office changes at once; night blue is saved as no key
Screenshots (Chromium): qa-shots/office-theme-<id>-iso.png, office-theme-<id>-flat.png, office-sky-<moment>.png,
office-weather-<kind>.png, office-secretary-desk.png, office-theme-settings.png.
Run: .venv/bin/python tests_office_theme_ui.py
"""
import functools
import http.server
import json
import pathlib
import sys
import threading

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: playwright not installed")
    sys.exit(0)

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
passed = failed = 0
THEMES = ["night", "wood", "nordic"]
TAIPEI = {"lat": 25.05, "lon": 121.5}
SYDNEY = {"lat": -33.87, "lon": 151.21}
POLAR = {"lat": 78.22, "lon": 15.65}


def utc(y, mo, d, h, mi=0):
    import calendar
    return calendar.timegm((y, mo, d, h, mi, 0)) * 1000


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


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


def rects_overlap(a, b, eps=1e-6):
    return a["x"] < b["x"] + b["w"] - eps and b["x"] < a["x"] + a["w"] - eps and a["y"] < b["y"] + b["h"] - eps and b["y"] < a["y"] + a["h"] - eps


def monitor_rect(mon):
    if mon.get("lap"):
        x, y, w, h = mon["base"]
    else:
        x, y, w, h = mon["bx"]
    return {"x": x, "y": y, "w": w, "h": h}


STATUS = "() => __office.statusColours()"
SCREEN_BOX = """([x, y, w, h, z0, z1]) => { const pts = [];
  for (const a of [x, x + w]) for (const b of [y, y + h]) for (const z of [z0, z1]) pts.push(__office.proj(a, b, z));
  return { x0: Math.min(...pts.map(p => p.x)), x1: Math.max(...pts.map(p => p.x)), y0: Math.min(...pts.map(p => p.y)), y1: Math.max(...pts.map(p => p.y)) }; }"""


def boxes_cross(a, b):
    return a["x0"] < b["x1"] - 1 and b["x0"] < a["x1"] - 1 and a["y0"] < b["y1"] - 1 and b["y0"] < a["y1"] - 1


def fps_while_active(page, ms=2500):
    return page.evaluate("""(ms) => new Promise(res => { let n = 0; const t0 = performance.now(), orig = __office.isActive;
        const tick = () => { __office.setMode && 0; };
        const iv = setInterval(() => { document.getElementById('stage').dispatchEvent(new PointerEvent('pointermove', { clientX: 400 + (n++ % 5), clientY: 400 })); }, 100);
        const f0 = window.__frames || 0;
        setTimeout(() => { clearInterval(iv); const txt = (document.getElementById('fps') || {}).textContent || ''; res(txt); }, ms); })""", ms)


def standalone(browser, base, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.goto(base + "/office.html")
    page.wait_for_function("window.__office && Object.keys(__office.assignments()).length >= 3", timeout=15000)
    page.evaluate("__office.pause(true)")
    page.wait_for_timeout(400)

    print(f"[{engine}] the default and the status colours")
    check("the default style is night blue", page.evaluate("__office.theme()") == "night")
    check("…the page says so (html data-sty), and it is not the light chrome",
          page.evaluate("document.documentElement.dataset.sty === 'night' && !document.documentElement.classList.contains('sty-light')"))
    check("three styles", page.evaluate("__office.themes()") == THEMES, page.evaluate("__office.themes()"))
    base_status = page.evaluate(STATUS)
    same = True
    for t in THEMES:
        page.evaluate(f"__office.setTheme('{t}')")
        if page.evaluate(STATUS) != base_status:
            same = False
    check("the status colours (COL, SCREEN, ST) are the same in every style", same)
    page.evaluate("__office.setTheme('nope')")
    check("an unknown style is night blue", page.evaluate("__office.theme()") == "night")

    print(f"[{engine}] every style, 3D and flat")
    page.evaluate("__office.skyAt(%d, %s)" % (utc(2026, 11, 25, 6), json.dumps(TAIPEI)))   # 14:00 in Taipei
    page.evaluate("__office.setWeather(null)")
    for t in THEMES:
        page.evaluate(f"__office.setTheme('{t}')")
        for mode in ("iso", "flat"):
            page.evaluate(f"__office.setMode('{mode}', true)")
            page.wait_for_timeout(350)
            if shoot:
                page.screenshot(path=str(SHOTS / f"office-theme-{t}-{mode}.png"))
    check("every style draws in 3D and flat without a page error", not errors, errors[:3])
    page.evaluate("__office.setMode('iso', true)")

    print(f"[{engine}] nordic light's chrome")
    page.evaluate("__office.setTheme('nordic')")
    page.wait_for_timeout(100)
    chrome = page.evaluate("""() => { const lum = c => { const m = c.match(/[\\d.]+/g).map(Number); return (m[0] * .299 + m[1] * .587 + m[2] * .114); };
        const bg = el => getComputedStyle(el).backgroundImage !== 'none' ? getComputedStyle(el).backgroundImage.match(/rgba?\\([^)]*\\)/)[0] : getComputedStyle(el).backgroundColor;
        return { top: lum(bg(document.getElementById('top'))), legend: lum(bg(document.getElementById('legend'))), stat: lum(bg(document.querySelector('.stat'))),
                 ink: lum(getComputedStyle(document.querySelector('.brand b')).color) }; }""")
    check("nordic light: the toolbar and the legend are light, their words dark",
          chrome["top"] > 200 and chrome["legend"] > 200 and chrome["ink"] < 90, chrome)
    check("…the status chips stay dark (their coloured numbers read as on the floor)", chrome["stat"] < 70, chrome)
    page.evaluate("__office.setTheme('night')")
    check("back to night blue: the light chrome is gone", page.evaluate("!document.documentElement.classList.contains('sty-light')"))

    print(f"[{engine}] the sun and the moon")
    page.evaluate("__office.setTheme('wood')")
    moments = {"morning": utc(2026, 11, 24, 23), "noon": utc(2026, 11, 25, 4), "afternoon": utc(2026, 11, 25, 7), "dusk": utc(2026, 11, 25, 8, 50), "night": utc(2026, 11, 25, 14)}
    sky = {}
    for name, ms in moments.items():
        page.evaluate("__office.skyAt(%d, %s)" % (ms, json.dumps(TAIPEI)))
        sky[name] = page.evaluate("__office.sky()")
        if shoot:
            page.evaluate("__office.setArea('lobby', true)")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / f"office-sky-{name}.png"))
    m, n, d, ni = sky["morning"]["sun"], sky["noon"]["sun"], sky["dusk"]["sun"], sky["night"]
    check("Taipei 07:00: the sun is up, low, in the east (the left part of the view)", 0 < m["el"] < 15 and 60 < m["az"] < 130 and m["pan"] < .35, m)
    check("Taipei 12:00: high, near the south (the middle)", n["el"] > 35 and 160 < n["az"] < 200 and .4 < n["pan"] < .6, n)
    check("Taipei 16:50: low in the west (the right part)", 0 < d["el"] < 8 and 220 < d["az"] < 260 and d["pan"] > .65, d)
    check("Taipei 22:00: night, the sun far below", ni["phase"] == "night" and ni["sun"]["el"] < -12, ni)
    check("the day goes day → dusk → night by the sun's height", sky["noon"]["phase"] == "day" and sky["dusk"]["phase"] == "dusk", {k: v["phase"] for k, v in sky.items()})
    page.evaluate("__office.skyAt(%d, %s)" % (utc(2026, 11, 24, 21), json.dumps(SYDNEY)))      # 08:00 in Sydney
    s = page.evaluate("__office.sky()")["sun"]
    check("Sydney 08:00: the windows face north, so the morning sun (east) is on the right", s["el"] > 10 and s["pan"] > .55, s)

    def sun_drawn(ms, place):      # which window each room drew the sun in, in the frame after the clock was set
        page.evaluate("__office.setArea('all', true)")
        page.evaluate("__office.skyAt(%d, %s)" % (ms, json.dumps(place)))
        page.wait_for_timeout(300)
        return page.evaluate("__office.sunSeen()")
    seen = [sun_drawn(utc(2026, 11, 25, h), TAIPEI) for h in (1, 4, 7)]          # 09:00, 12:00, 15:00 in Taipei
    dup = [(k, e) for k, ss in enumerate(seen) for j, e in enumerate(ss) if any(f["room"] == e["room"] for f in ss[:j])]
    check("Taipei 09:00, 12:00, 15:00: the sun is drawn in the lobby, in exactly one window of each room it shows in",
          all(any(e["room"] == "lobby" and e["i"] >= 0 for e in ss) for ss in seen) and not dup, (seen, dup))
    lob = [next((e["i"] for e in ss if e["room"] == "lobby"), None) for ss in seen]
    check("…and it moves left to right through the day (the lobby's windows, east to west)", None not in lob and lob[0] < lob[1] < lob[2], lob)
    syd = [sun_drawn(ms, SYDNEY) for ms in (utc(2026, 11, 24, 22), utc(2026, 11, 25, 1), utc(2026, 11, 25, 4))]   # 09:00, 12:00, 15:00 in Sydney
    sl = [next((e["i"] for e in ss if e["room"] == "lobby"), None) for ss in syd]
    check("Sydney 09:00, 12:00, 15:00: right to left (the windows face north)", None not in sl and sl[0] > sl[1] > sl[2], sl)
    page.evaluate("__office.skyAt(%d, %s)" % (utc(2026, 12, 21, 11), json.dumps(POLAR)))
    p = page.evaluate("__office.sky()")
    check("near the pole in December: polar night, and no NaN anywhere",
          p["sun"]["el"] < 0 and all(isinstance(v, (int, float)) and v == v for v in (p["sun"]["el"], p["sun"]["az"], p["sun"]["pan"], p["moon"]["el"], p["moon"]["az"])), p)
    check("the moon has a phase between 0 and 1", 0 <= p["moon"]["lit"] <= 1, p["moon"])

    print(f"[{engine}] the weather")
    page.evaluate("__office.skyAt(%d, %s)" % (utc(2026, 11, 25, 3), json.dumps(TAIPEI)))
    page.evaluate("__office.setArea('lobby', true)")
    for kind in ("rain", "snow", "fog", "cloudy"):
        page.evaluate("(k) => __office.setWeather({ kind: k, thunder: k === 'rain', heavy: false, light: false })", kind)
        page.wait_for_timeout(250)
        check(f"weather {kind}: drawn as {kind}", page.evaluate("__office.sky().kind") == kind)
        if shoot:
            page.screenshot(path=str(SHOTS / f"office-weather-{kind}.png"))
    page.evaluate("__office.setWeather({})")
    check("a reading without a kind is a clear sky", page.evaluate("__office.sky().kind") == "clear")
    check("no page error while drawing the weather", not errors, errors[:3])
    page.evaluate("__office.setWeather({ kind: 'rain', heavy: true, thunder: true })")
    page.evaluate("__office.setArea('all', true)")
    page.evaluate("__office.pause(false)")
    fps = fps_while_active(page)
    try:
        fv = float(str(fps).split()[0])
    except (ValueError, IndexError):
        fv = 0
    check("heavy rain with thunder in warm wood, whole floor: 12 fps or more while active", fv >= 12, fps)
    page.evaluate("__office.pause(true)")
    page.evaluate("__office.setWeather(undefined)")

    print(f"[{engine}] the desks")
    keys = page.evaluate("Object.values(__office.assignments()).map(s => s.replace(/ \\(.*/, ''))")
    zone_key = next((k for k in keys if k.startswith("z:")), None)
    bad = []
    for key in ["sec"] + ([zone_key] if zone_key else []):
        info = page.evaluate("(k) => __office.deskSpots(k)", key)
        mon = monitor_rect(info["mon"])
        sp = info["spots"]
        for nm, r in [("side%d" % i, r) for i, r in enumerate(sp["side"] or [])] + [("front", sp["front"]), ("floor", sp["floor"])]:
            if r and rects_overlap(r, mon):
                bad.append((key, nm, r, mon))
    check("no desk spot (corners, front, floor) shares floor with the monitor, normal desks and the secretary's", not bad, bad)
    sec = page.evaluate("__office.deskSpots('sec')")
    g, fl = sec["g"], sec["spots"]["floor"]
    check("the secretary's desk faces up-left ('nx')", g["face"] == "nx", g)
    check("…its floor spot is beside an end of the desk, not behind it (behind it, the 3D monitor covers it)",
          fl["x"] >= g["x"] and (fl["y"] + fl["h"] <= g["y"] or fl["y"] >= g["y"] + g["h"]), (g, fl))
    mx, my, mw, mh = sec["mon"]["bx"]
    mon_box = page.evaluate(SCREEN_BOX, [mx, my, mw, mh, sec["mon"]["z0"], sec["mon"]["z1"]])
    crossing = []
    for nm, r, h in [("floor", fl, 34)] + [("side%d" % i, r, 22) for i, r in enumerate(sec["spots"]["side"])]:
        b = page.evaluate(SCREEN_BOX, [r["x"], r["y"], r["w"], r["h"], r["z"], r["z"] + h])
        if boxes_cross(b, mon_box):
            crossing.append(nm)
    check("…in 3D, the floor spot and the desk corners do not cross the monitor's picture", not crossing, crossing)
    lay = page.evaluate("__office.lobbyLayout()")
    lobby_room = next(r for r in page.evaluate("__office.rooms()") if r["id"] == "lobby")
    flr = {"x": fl["x"] - lobby_room["x"], "y": fl["y"] - lobby_room["y"], "w": fl["w"], "h": fl["h"]}
    hit = [f["id"] for f in lay["floor"] if f["id"] != "seat:sec" and rects_overlap(flr, {"x": f["x0"], "y": f["y0"], "w": f["x1"] - f["x0"], "h": f["y1"] - f["y0"]})]
    check("…and it does not overlap the lobby's other furniture", not hit and flr["y"] >= 0, (hit, flr))
    if shoot:
        page.evaluate("__office.setTheme('night')")
        page.evaluate("__office.setArea('lobby', true)")
        page.wait_for_timeout(300)
        p0 = page.evaluate("__office.seatScreen('sec')")
        if p0:
            page.screenshot(path=str(SHOTS / "office-secretary-desk.png"),
                            clip={"x": max(0, p0["x"] - 220), "y": max(0, p0["y"] - 200), "width": 440, "height": 320})
    page.close()


HOST_STUB = r"""
(() => {
const PREFS = %s, APPMODE = %s, BOARD = %s;
window.__calls = [];
const rec = (n, ...a) => window.__calls.push([n, ...a]);
if (APPMODE) window.SFOfficeApp = { v: 1, lang: 'zh', uiScale: 1, idiom: 'pad', prefs: PREFS, pairedIds: [], caps: { editBot: false, openChat: false }, active() {} };
window.SFOfficeHost = {
  lang: () => 'zh', uiScale: () => 1, boardOnly: () => BOARD, isBoardKey: () => false,
  get: k => (rec('get', k), Object.prototype.hasOwnProperty.call(PREFS, k) ? PREFS[k] : null),
  set: (k, v) => rec('set', k),
  close: () => {}, objectsChanged: () => {}, editBot: () => false, md: t => t,
  sky: () => (rec('sky'), Promise.resolve({ success: true, place: { lat: -33.87, lon: 151.21, tz: 'Australia/Sydney', source: 'tz' }, weather: { kind: 'snow', thunder: false, heavy: false, light: true, at: Date.now() / 1000 }, weather_on: true, credit: 'MET Norway' })),
  chat: { has: () => false, open: () => false, openUrl: () => {} },
  board: { detail: async () => ({ success: false }), schedules: async () => ({ ok: true, rows: [] }), canRequest: () => false, takeFocus: () => null },
  objects: { list: async () => ({ success: true, groups: [], items: [], total: 0 }), item: async () => ({ success: false }), action: async () => ({ success: false }), view: async () => ({ success: false }), live: async () => ({ success: true }) },
};
})();
"""


def with_host(browser, base, engine):
    def page_with(prefs, app=False, board=False):
        page = browser.new_page(viewport={"width": 1100, "height": 760})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.add_init_script(HOST_STUB % (json.dumps(prefs), json.dumps(app), json.dumps(board)))
        page.goto(base + "/office.html")
        page.wait_for_function("window.__office || window.Office", timeout=15000)
        page.wait_for_timeout(1200)
        return page, errs

    print(f"[{engine}] with a host")
    page, errs = page_with({})
    n = lambda pg, name: pg.evaluate("(n) => window.__calls.filter(c => c[0] === n).length", name)
    check("no style kept: night blue, and the sky is never asked", page.evaluate("__office.theme()") == "night" and n(page, "sky") == 0)
    page.evaluate("Office.setTheme('nordic')")
    page.wait_for_timeout(300)
    check("Office.setTheme (Settings while the office is open) changes it at once, and the sky is asked once", page.evaluate("__office.theme()") == "nordic" and n(page, "sky") == 1)
    check("…with the host's place and weather", page.evaluate("__office.sky().place.lat") == -33.87 and page.evaluate("__office.sky().kind") == "snow")
    page.evaluate("Office.setTheme('night')")
    page.wait_for_timeout(200)
    check("…back to night blue asks nothing more", n(page, "sky") == 1)
    check("the page never writes the style itself", not page.evaluate("window.__calls.some(c => c[0] === 'set' && c[1] === 'theme')"))
    page.close()

    page, errs = page_with({"theme": "wood"})
    check("the host's prefs say wood: the page starts in warm wood and asks the sky once", page.evaluate("__office.theme()") == "wood" and n(page, "sky") == 1)
    check("…no page error", not errs, errs[:3])
    page.close()

    page, errs = page_with({"theme": "wood"}, board=True)
    check("the board alone stays night blue and asks nothing", page.evaluate("__office ? __office.theme() : 'night'") == "night" and n(page, "sky") == 0)
    page.close()

    page, errs = page_with({"theme": "nordic"}, app=True)
    check("in the phone app the desktop's style is used (nordic light, light chrome)",
          page.evaluate("__office.theme()") == "nordic" and page.evaluate("document.documentElement.classList.contains('sty-light')"))
    page.close()


CFG = {"settings": {"language": "zh-TW", "experimental_bots": False, "experimental_office": True, "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}, "guide": {"tour": "done", "seen": "9.9.9"}}


def main_stub(prefs, prefs_ok=True):
    return """
if (window.top === window) {
window.__calls = []; window.__saved = []; window.__cfg = %s; window.__prefs = %s; const PREFS_OK = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, ...args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R([]);
      case 'office_prefs': return PREFS_OK ? R({success: true, prefs: window.__prefs}) : Promise.reject(new Error('down'));
      case 'office_prefs_save': window.__saved.push(JSON.parse(args[0])); return R({success: true});
      case 'office_sky': return R({success: true, place: {lat: 25.05, lon: 121.5, tz: 'Asia/Taipei', source: 'tz'}, weather: null, weather_on: true, credit: 'MET Norway'});
      case 'guide_data': return R({success: true, features: [], tours: {}, state: {tour: 'done'}});
      default: return R('{}');
    }
  };
}})};
}
""" % (json.dumps(CFG), json.dumps(prefs), json.dumps(prefs_ok))


def main_window(browser, base, engine):
    shoot = engine == "chromium"
    seats = {"seats": {"b:eng": "z:all:3"}, "mode": "iso"}

    def open_settings(prefs, ok=True):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.add_init_script(main_stub(prefs, ok))
        page.goto(base + "/index.html")
        page.wait_for_function("window.SFOffice", timeout=20000)
        page.wait_for_timeout(500)   # no tabs: the window opens the new-tab launcher first; put it away
        page.evaluate("document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'))")
        page.click("#btn-settings")
        page.click('#settings-tabs-bar [data-tab="office"]')
        page.wait_for_selector("#setting-office-theme", state="attached", timeout=5000)
        page.wait_for_timeout(400)
        return page

    print(f"[{engine}] Settings → Office")
    page = open_settings(seats)
    ui = page.evaluate("""() => { const s = document.getElementById('setting-office-theme'), w = document.getElementById('setting-office-weather');
        return { opts: [...s.options].map(o => [o.value, o.textContent]), value: s.value, panel: s.closest('.settings-tab-panel').id,
                 label: document.querySelector('#sfo-theme .sx-label').textContent, wOn: w.classList.contains('on'),
                 wHint: document.querySelector('#sfo-weather .sx-hint').textContent }; }""")
    check("辦公室風格 sits in Settings → Office with three choices in the page's language, night blue picked",
          ui["panel"] == "settings-panel-office" and [o[0] for o in ui["opts"]] == THEMES and ui["opts"][1][1] == "暖木" and ui["value"] == "night" and ui["label"] == "辦公室風格", ui)
    check("窗外天氣 is on by default, its hint names the place (from the time zone) and the source", ui["wOn"] and "Asia/Taipei" in ui["wHint"] and "MET Norway" in ui["wHint"], ui["wHint"])
    check("asking the place for the hint never asks for weather (want_weather false)",
          page.evaluate("window.__calls.filter(c => c[0] === 'office_sky').every(c => c[2] === false)"))
    if shoot:
        page.locator("#settings-panel-office .sx-card").screenshot(path=str(SHOTS / "office-theme-settings.png"))
    page.select_option("#setting-office-theme", "wood")
    page.wait_for_timeout(400)
    saved = page.evaluate("window.__saved")
    check("picking warm wood before the office was opened saves it together with the seats (office.json read first)",
          saved and saved[-1].get("theme") == "wood" and saved[-1].get("seats") == seats["seats"] and saved[-1].get("mode") == "iso", saved)
    page.select_option("#setting-office-theme", "night")
    page.wait_for_timeout(400)
    saved = page.evaluate("window.__saved")
    check("night blue is saved as no key (the original look)", "theme" not in saved[-1] and saved[-1].get("seats") == seats["seats"], saved[-1])
    page.click("#setting-office-weather")
    page.wait_for_timeout(200)
    check("the weather switch saves settings.office_weather", page.evaluate("window.__cfg.settings.office_weather") is False)
    page.close()

    page = open_settings(seats, ok=False)
    page.select_option("#setting-office-theme", "nordic")
    page.wait_for_timeout(500)
    check("office.json cannot be read: nothing is written, and the picker goes back",
          page.evaluate("window.__saved.length") == 0 and page.evaluate("document.getElementById('setting-office-theme').value") == "night")
    page.close()


def main():
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    SHOTS.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        for engine in ("chromium", "webkit"):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as e:
                print(f"SKIP {engine}: {e}")
                continue
            try:
                standalone(browser, base, engine)
                with_host(browser, base, engine)
                main_window(browser, base, engine)
            finally:
                browser.close()
    srv.shutdown()
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
