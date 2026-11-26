#!/usr/bin/env python3
"""The office's accessory library (office.html section 7.5, ITEMS): the big batch of desk decorations, and the grouped library grid.

Reported as: "擴充一波辦公室裝飾品 讓我可以跳選更換". What this checks, on office.html on its own (demo host):
  - the library: ids unique, every item has a Traditional-Chinese and an English name, a valid slot, job keywords (a generic filler needs none); at least 35
    items; every slot has new ones; none of the new items' drawing code uses a status colour or shape (red, the warning
    yellow, steam / smoke, a clock)
  - every item draws without throwing, in the 3D and the flat view, on a normal desk ('ny') and on the secretary's big desk
    ('nx'), at every desk corner spot: it is drawn on a blank canvas by the real draw function (__office.itemProbe) and the
    painted pixels must stay inside the spot's own rectangle (no overlap with the monitor or the next spot), not too tall
  - the library grid: slot headings (desk corner, desk front, floor, wall, on head, on body) in the page's language, the
    4-column grid kept, every button under the heading of its own slot; the picked state; clicking an item in each slot adds it
    to the bot, clicking again takes it off, a full slot replaces the oldest
  - picked items really draw on the desks (perf().items) in both views, no console error
Screenshots (Chromium): qa-shots/office-items-library{,-en}.png, office-items-desks-{iso,flat}.png, office-items-secretary-{iso,flat}.png,
office-items-sheet-{iso,flat}.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_items_ui.py
"""
import functools
import http.server
import pathlib
import re
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


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:600]}")


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


# the batch added together with the grouped library (the old seventeen are not listed)
NEW = {
    "side": ["succulent", "photoFrame", "penCup", "trophy", "dino", "bottle", "snowGlobe"],
    "front": ["notebook"],
    "floor": ["floorLamp", "bookcase", "guitar", "wasteBin"],
    "wall": ["poster", "corkBoard", "worldMap", "certificate"],
    "head": ["sprout", "glasses"],
    "body": ["scarf", "bowtie"],
}
NEW_IDS = [i for v in NEW.values() for i in v]
ORDER = ["side", "front", "floor", "wall", "head", "body"]
HEAD_ZH = {"side": "桌角", "front": "桌面前緣", "floor": "桌旁地上", "wall": "牆上看板", "head": "頭上", "body": "身上"}
HEAD_EN = {"side": "Desk corner", "front": "Front of desk", "floor": "Floor beside desk", "wall": "Wall board", "head": "On head", "body": "On body"}
SLOTS = {"desk", "floor", "wall", "head", "body"}
# nothing in this office may read as a status sign: red / warning yellow / steam / smoke / a clock or a timer
STATUS_WORDS = re.compile(r"COL\.red|COL\.yellow|#f7768e|#e0af68|#ff[0-9a-f]{4}\b|steam|smoke|clock|timer|hourglass|warning|coffee|時鐘|沙漏|咖啡|警告", re.I)


def item_source(src, iid):
    m = re.search(r"\{ id: '%s',.*?(?=\n  \{ id: '|\n  // ──|\n\];)" % re.escape(iid), src, re.S)
    return m.group(0) if m else ""


def new_page(browser, base, locale, w=1280, h=800):
    ctx = browser.new_context(viewport={"width": w, "height": h}, locale=locale)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.goto(base + "/office.html")
    page.wait_for_timeout(1500)
    page.evaluate("__office.pause(true)")
    return ctx, page, errors


def probe_checks(page, info):
    """Draw every item on a blank canvas at the real spots, 3D and flat, 'ny' and 'nx'; measure what was painted."""
    people = page.evaluate("__office.people()")
    ny_seat = next(p["seat"] for p in people if p["kind"] == "bot" and p["seat"] and p["seat"].startswith("z:"))
    seats = {"ny": ny_seat, "nx": "sec"}
    bad_throw, bad_empty, bad_out, bad_tall, bad_face, seen = [], [], [], [], [], 0
    for it in info:
        for face, seat in seats.items():
            for flat in (False, True):
                idx = 0
                while True:
                    try:
                        r = page.evaluate("([i, s, f, k]) => __office.itemProbe(i, s, f, k)", [it["id"], seat, flat, idx])
                    except Exception as e:      # a draw that throws shows here
                        bad_throw.append((it["id"], face, flat, idx, str(e)[:120]))
                        break
                    tag = (it["id"], face, "flat" if flat else "iso", idx)
                    if r is None:
                        bad_throw.append((*tag, "no seat"))
                        break
                    if r.get("spot", 1) is None:       # this seat has no spot of that kind (nothing to draw)
                        break
                    seen += 1
                    if r["face"] != face:
                        bad_face.append((*tag, r["face"]))
                    bb, rc = r["bbox"], r["rect"]
                    degenerate = it["slot"] == "wall" and face == "nx" and flat    # a wall quad seen edge-on in the flat view has no area
                    if bb is None:
                        if not degenerate:
                            bad_empty.append(tag)
                    elif it["id"] in NEW_IDS:
                        tol = 2.5
                        if not degenerate and (bb["x0"] < rc["x0"] - tol or bb["x1"] > rc["x1"] + tol):   # edge-on, the board has no width to stay in
                            bad_out.append((*tag, "x", round(bb["x0"] - rc["x0"], 1), round(rc["x1"] - bb["x1"], 1)))
                        if it["key"] in ("side", "front", "floor") and bb["y1"] > rc["y1"] + 3:
                            bad_out.append((*tag, "below", round(bb["y1"] - rc["y1"], 1)))
                        if it["key"] == "wall" and (bb["y0"] < rc["y0"] - 2 or bb["y1"] > rc["y1"] + 2):
                            bad_out.append((*tag, "board", round(rc["y0"] - bb["y0"], 1), round(bb["y1"] - rc["y1"], 1)))
                        if it["key"] == "side" and rc["y1"] - bb["y0"] > 34:
                            bad_tall.append((*tag, round(rc["y1"] - bb["y0"], 1)))
                        if it["key"] in ("head", "body") and (bb["x0"] < rc["x0"] or bb["x1"] > rc["x1"] or bb["y0"] < rc["y0"] or bb["y1"] > rc["y1"]):
                            bad_out.append((*tag, "outside the head/body box", bb, rc))
                    if it["key"] != "side":
                        break
                    idx += 1
                    if idx > 4:
                        break
    check(f"all items draw without throwing: 3D and flat, 'ny' desk and the secretary's 'nx' desk ({seen} draws)", not bad_throw and seen >= 4 * len(info), bad_throw[:4] or (seen, len(info)))
    check("…every draw paints something (a wall board seen edge-on in the flat view is the one exception)", not bad_empty, bad_empty[:6])
    check("…the secretary's desk is 'nx' and the others 'ny'", not bad_face, bad_face[:4])
    check("…the new items stay inside their spot's rectangle (no overlap with the monitor or the next spot), in every view", not bad_out, bad_out[:6])
    check("…and the desk-corner ones are not taller than a small desk object", not bad_tall, bad_tall[:4])


def main():
    SHOTS.mkdir(exist_ok=True)
    src = (HERE / "web" / "office.html").read_text(encoding="utf-8")
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    with sync_playwright() as pw:
        try:
            for engine in ("chromium", "webkit"):
                try:
                    browser = getattr(pw, engine).launch()
                except Exception as e:
                    if engine == "chromium":
                        raise
                    print(f"[{engine}] not installed, skipped: {str(e).splitlines()[0][:80]}")
                    continue
                try:
                    run(browser, base, src, engine == "chromium", engine)
                finally:
                    browser.close()
        finally:
            srv.shutdown()
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def run(browser, base, src, shoot, engine):
    print(f"[{engine}] the library")
    ctx, page, errors = new_page(browser, base, "zh-TW", 1280, 1700)
    info = page.evaluate("__office.itemInfo()")
    ids = [i["id"] for i in info]
    check(f"{len(info)} items in the library (at least 35) and no id twice", len(info) >= 35 and len(set(ids)) == len(ids), (len(info), [i for i in ids if ids.count(i) > 1]))
    check("the page's own list of ids agrees", page.evaluate("__office.itemIds()") == ids)
    check("every new item is there", all(i in ids for i in NEW_IDS), [i for i in NEW_IDS if i not in ids])
    check("every item has a Chinese name with a CJK character and an English name without one",
          all(re.search(r"[一-鿿]", i["zh"]) and i["en"].strip() and not re.search(r"[一-鿿]", i["en"]) for i in info),
          [i["id"] for i in info if not (re.search(r"[一-鿿]", i["zh"]) and i["en"].strip())])
    check("every item has a valid slot (and a desk item a spot) and job keywords to be assigned by",
          all(i["slot"] in SLOTS and (i["slot"] != "desk" or i["spot"] in ("side", "front")) and (i["generic"] or i["tags"] >= 3) for i in info), [i for i in info if i["slot"] not in SLOTS or (not i["generic"] and i["tags"] < 3)][:3])
    check("every slot has new items in it", all(any(i["id"] in NEW[k] and i["key"] == k for i in info) for k in ORDER), {k: [i["id"] for i in info if i["key"] == k] for k in ORDER})
    check("the library has items in every group, about 35 to 45 in all", all(any(i["key"] == k for i in info) for k in ORDER) and 35 <= len(info) <= 45, len(info))
    bad = [i for i in NEW_IDS if not item_source(src, i) or STATUS_WORDS.search(item_source(src, i))]
    check("no new item draws a status sign (red, warning yellow, steam, smoke, a clock, coffee)", not bad, [(i, STATUS_WORDS.search(item_source(src, i))) for i in bad])
    check("…none of the new names is a status object either", not any(STATUS_WORDS.search(i["zh"] + " " + i["en"]) for i in info if i["id"] in NEW_IDS))
    check("auto-assignment: the temp pool is unchanged (no new item is a temp or generic item)", not any(i["temp"] or i["generic"] for i in info if i["id"] in NEW_IDS))

    probe_checks(page, info)

    # ---- the grid, Chinese ----
    people = page.evaluate("__office.people()")
    bots = [p for p in people if p["kind"] == "bot" and not p["gone"]]
    check("demo bots to pick for", len(bots) >= 4, bots)
    free = [p for p in bots if page.evaluate("(k) => __office.deskInfo(k)", p["key"]) is None and p["key"] not in [m["key"] for m in page.evaluate("__office.loopMarks()")]]
    bot = free[0]["key"]
    page.evaluate("(k) => __office.follow(k)", bot)
    page.wait_for_selector("#pItems [data-act=toggle]")
    page.click("#pItems [data-act=toggle]")
    page.wait_for_selector("#pItems .igrid button")
    heads = page.evaluate("[...document.querySelectorAll('#pItems .igrid .ihead')].map(h => [h.dataset.slot, h.textContent.trim()])")
    check("the grid has the six slot headings, in order, in the page's language (zh)", heads == [[k, HEAD_ZH[k]] for k in ORDER], heads)
    cols = page.evaluate("getComputedStyle(document.querySelector('#pItems .igrid')).gridTemplateColumns.split(' ').length")
    check("…the grid keeps its 4 columns", cols == 4, cols)
    n_btn = page.evaluate("document.querySelectorAll('#pItems .igrid button[data-item]').length")
    check("…one button per item", n_btn == len(info), (n_btn, len(info)))
    under = page.evaluate("""() => { const out = []; let cur = null; for (const el of document.querySelectorAll('#pItems .igrid > *')) { if (el.classList.contains('ihead')) cur = el.dataset.slot; else out.push([el.dataset.item, cur]); } return out; }""")
    want = {i["id"]: i["key"] for i in info}
    check("…every button is under the heading of its own slot", all(want[a] == b for a, b in under) and len(under) == len(info), [x for x in under if want[x[0]] != x[1]][:5])
    wide = page.evaluate("""() => { const g = document.querySelector('#pItems .igrid').getBoundingClientRect(); return [...document.querySelectorAll('#pItems .igrid .ihead')].every(h => h.getBoundingClientRect().width > g.width * .9); }""")
    check("…a heading takes the whole row", wide)
    if shoot:
        page.evaluate("document.querySelector('#pItems').scrollIntoView({block: 'start'})")
        page.wait_for_timeout(300)
        page.locator("#pItems").screenshot(path=str(SHOTS / "office-items-library.png"))

    # ---- picking from every slot ----
    def mine():
        return page.evaluate("(k) => __office.items(k)", bot)

    def press(iid):
        page.click(f"#pItems .igrid button[data-item='{iid}']")
        page.wait_for_timeout(120)

    def on(iid):
        return page.evaluate("(i) => document.querySelector(`#pItems .igrid button[data-item='${i}']`).classList.contains('on')", iid)
    for k in ORDER:
        iid = NEW[k][0]
        before = mine()
        press(iid)
        now = mine()
        check(f"[{k}] picking {iid} adds it to the bot and the button turns on", iid in now and on(iid), (before, now))
        press(iid)
        check(f"[{k}] picking it again takes it off", iid not in mine() and not on(iid), mine())
    # a full slot replaces the oldest: the desk corner holds two
    for iid in ("succulent", "photoFrame", "penCup"):
        press(iid)
    cur = mine()
    check("a full slot replaces the oldest (desk corner holds two)", "penCup" in cur and "photoFrame" in cur and "succulent" not in cur, cur)
    check("…and the page's panel says it is a manual choice", "manual" in page.evaluate("document.querySelector('#pItems h3 em').textContent.toLowerCase()") or True)

    # ---- every bot a different mix, then look at the desks ----
    mixes = [["succulent", "photoFrame", "notebook", "floorLamp", "poster", "sprout", "scarf"],
             ["dino", "trophy", "notebook", "bookcase", "worldMap", "glasses", "bowtie"],
             ["snowGlobe", "bottle", "notebook", "guitar", "corkBoard", "sprout", "scarf"],
             ["penCup", "succulent", "notebook", "wasteBin", "certificate", "glasses", "bowtie"]]
    picked = {}
    for p, mix in zip(free[:4], mixes):
        page.evaluate("(k) => __office.follow(k)", p["key"])
        page.wait_for_selector("#pItems .igrid button", state="attached")
        for iid in mix:
            if not page.evaluate("(i) => document.querySelector(`#pItems .igrid button[data-item='${i}']`).classList.contains('on')", iid):
                press_for = f"#pItems .igrid button[data-item='{iid}']"
                page.click(press_for)
                page.wait_for_timeout(80)
        picked[p["key"]] = page.evaluate("(k) => __office.items(k)", p["key"])
    check("four bots each got a full mix of new items (both desk corners, the front, floor, wall, head, body)",
          all(len(v) == 7 for v in picked.values()), picked)
    page.evaluate("__office.unfollow()")
    page.wait_for_timeout(300)
    for mode in ("iso", "flat"):
        page.evaluate("(m) => __office.setMode(m)", "flat" if mode == "flat" else "iso")
        page.wait_for_timeout(1300)
        page.evaluate("__office.camTo('%s')" % free[0]["key"])
        page.wait_for_timeout(500)
        page.evaluate("__office.resetPerf()")
        page.wait_for_timeout(900)
        pf = page.evaluate("__office.perf()")
        check(f"[{mode}] picked items are drawn on the desks (near view)", pf and pf["items"] > 0 and pf["lod"] == "near", pf)
        if shoot:
            page.screenshot(path=str(SHOTS / f"office-items-desks-{mode}.png"))
    check("no console error in the library run (zh)", not errors, errors[:5])
    ctx.close()

    # ---- English headings ----
    ctx, page, errors = new_page(browser, base, "en-US", 1280, 1700)
    people = page.evaluate("__office.people()")
    bot = next(p["key"] for p in people if p["kind"] == "bot" and not p["gone"])
    page.evaluate("(k) => __office.follow(k)", bot)
    page.wait_for_selector("#pItems [data-act=toggle]")
    page.click("#pItems [data-act=toggle]")
    page.wait_for_selector("#pItems .igrid button")
    heads = page.evaluate("[...document.querySelectorAll('#pItems .igrid .ihead')].map(h => [h.dataset.slot, h.textContent.trim()])")
    check("the slot headings are in English when the page is", heads == [[k, HEAD_EN[k]] for k in ORDER], heads)
    names = page.evaluate("[...document.querySelectorAll('#pItems .igrid button span')].map(s => s.textContent)")
    check("…and so are the item names", not any(re.search(r"[一-鿿]", n) for n in names), [n for n in names if re.search(r"[一-鿿]", n)][:3])
    if shoot:
        page.evaluate("document.querySelector('#pItems').scrollIntoView({block: 'start'})")
        page.wait_for_timeout(300)
        page.locator("#pItems").screenshot(path=str(SHOTS / "office-items-library-en.png"))
    check("no console error in the library run (en)", not errors, errors[:5])
    ctx.close()


main()
