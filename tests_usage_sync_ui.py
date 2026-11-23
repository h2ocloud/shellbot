#!/usr/bin/env python3
"""用量不同步（v0.64）的畫面端：web/index.html 在瀏覽器裡，Python 端用替身。

  - 沒綁帳號的分頁：帳號面板「目前 Session」顯示實際在用的全域帳號並標「全域」，不是「未登入」
  - 真的查不到登入才顯示「未登入」
  - 面板查到某帳號的新數字，而目前分頁用的就是那個帳號 → 膠囊立刻重畫，不另外再查一次
  - 反方向：膠囊查到新值 → 面板開著時那一列同步更新
  - 綁別的帳號的分頁：面板的數字不會蓋到膠囊

全是示範資料。截圖：qa-shots/usage-sync-global-login.png
需要 playwright；沒裝就 SKIP。跑法：.venv/bin/python tests_usage_sync_ui.py
"""
import json
import pathlib
import sys

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
    print("SKIP  tests_usage_sync_ui.py（沒裝 playwright）")
    sys.exit(0)

DEMO = "demo@example.test"
REF = "claude-demo0001"
OTHER = "claude-demo0002"
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "demo tab", "bridge_enabled": True}]
WEEK = 7 * 24 * 60


def win(pct, reset="11-30 09:00"):
    return {"pct": pct, "reset": reset}


ACC = {"id": REF, "email": DEMO, "label": DEMO, "plan": "team", "organization": "Demo Org"}
ACC2 = {"id": OTHER, "email": "second@example.test", "label": "second@example.test", "plan": "pro",
        "organization": ""}


def state(current, source=""):
    return {"session_id": "s501", "providers": {"claude": {
        "current": current, "global": ACC, "accounts": [ACC, ACC2], "logged_in": True,
        "installed": True, "install": {}, "current_source": source}}}


STUB = """
window.__calls = [];

const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push(prop);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'tab_usage_brief': return R(window.__briefs[Math.min(window.__brief_i++, window.__briefs.length - 1)]);
      case 'account_state': return R(window.__state);
      case 'account_usage_all': return R(window.__usage);
      default: return R('{}');
    }
  };
}})};
"""


def open_page(browser, brief, st, usage):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "cli"},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.set_default_timeout(8000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    init = (f"window.__cfg = {json.dumps(cfg)}; window.__sessions = {json.dumps(SESSIONS)};"
            f"window.__state = {json.dumps(st)}; window.__usage = {json.dumps(usage)};"
            f"window.__briefs = {json.dumps([json.dumps(b) for b in brief])}; window.__brief_i = 0;")
    page.add_init_script(init + STUB)
    page.goto((HERE / "web" / "index.html").as_uri(), wait_until="domcontentloaded")
    page.wait_for_function("window.__sfHost && Object.keys(window.__sfHost.sessions()).length > 0", timeout=15000)
    page.wait_for_timeout(900)
    return page, errors


def n_brief(page):
    return page.evaluate("window.__calls.filter(c => c === 'tab_usage_brief').length")


def pill(page):
    return page.evaluate("""() => { const e = document.getElementById('usage-brief');
        return {text: e.textContent.replace(/\\s+/g, ' ').trim(), stale: e.classList.contains('stale')}; }""")


def row_text(page, ref):
    return page.evaluate("""(k) => { const e = document.querySelector('[data-usage-key="claude|' + k + '"]');
        return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null; }""", ref)


def scope_text(page):
    return page.evaluate("""() => [...document.querySelectorAll('.account-scope-card')]
        .map(e => e.textContent.replace(/\\s+/g, ' ').trim())""")


STALE_BRIEF = {"ai": "claude", "account": DEMO, "five_hr": win(0), "week": win(85),
               "stale": True, "error": None, "snapshot": None, "account_ref": REF}
FRESH_ROW = {"ai": "claude", "account": DEMO, "five_hr": win(4), "week": win(92), "stale": False,
             "error": None, "snapshot": None, "checked": "22:07", "is_current_login": True}


with sync_playwright() as p:
    browser = p.chromium.launch()

    print("1. 沒綁帳號：目前 Session 顯示全域帳號，面板查新後膠囊立刻重畫")
    page, errors = open_page(browser, [STALE_BRIEF], state(ACC, "global_login"),
                             {"success": True, "providers": {"claude": {REF: FRESH_ROW}}})
    page.wait_for_timeout(300)
    before = pill(page)
    check("起點：膠囊是舊資料（⚠ stale，0% / 85%）",
          before["stale"] and "0%" in before["text"] and "85%" in before["text"], before)
    calls = n_brief(page)
    page.click("#usage-brief")
    page.wait_for_selector(".account-scope-card")
    page.wait_for_function("document.querySelector('[data-usage-key] .au-v')")
    page.wait_for_timeout(300)
    scopes = scope_text(page)
    check("「目前 Session」顯示全域帳號", DEMO in scopes[0], scopes)
    check("…並標「全域」", "全域" in scopes[0], scopes)
    check("…不是「未登入」", "未登入" not in scopes[0], scopes)
    btn = page.evaluate("""() => { const b = document.querySelector('[data-account-action="session"][data-ref="%s"]');
        return b ? b.disabled : null; }""" % REF)
    check("沒綁帳號時「切目前」仍可按（才能真的把分頁綁到這個帳號）", btn is False, btn)
    check("帳號列 4% / 92%", "4%" in (row_text(page, REF) or "") and "92%" in (row_text(page, REF) or ""),
          row_text(page, REF))
    after = pill(page)
    check("面板查新之後膠囊重畫成 4% / 92%，不再 stale",
          (not after["stale"]) and "4%" in after["text"] and "92%" in after["text"], after)
    check("…沒有另外再查一次（沒多叫 tab_usage_brief）", n_brief(page) == calls, (calls, n_brief(page)))
    SHOTS.mkdir(exist_ok=True)
    page.locator(".modal-overlay.open .modal").screenshot(path=str(SHOTS / "usage-sync-global-login.png"))

    print("2. 反方向：膠囊查到新值 → 面板開著時那一列同步")
    page.evaluate("""() => { window.__briefs = [JSON.stringify({ai: 'claude', account: 'x',
        five_hr: {pct: 7, reset: '11-30 09:00'}, week: {pct: 93, reset: '11-30 09:00'}, stale: false,
        error: null, snapshot: null, account_ref: '%s'})]; window.__brief_i = 0; }""" % REF)
    page.evaluate("window.refreshUsageBrief()")
    page.wait_for_timeout(300)
    check("帳號列同步成 7% / 93%", "7%" in (row_text(page, REF) or "") and "93%" in (row_text(page, REF) or ""),
          row_text(page, REF))
    check("膠囊同為 7% / 93%", "7%" in pill(page)["text"] and "93%" in pill(page)["text"], pill(page))
    page.evaluate("""() => { window.__briefs = [JSON.stringify({ai: 'claude', account: 'x',
        five_hr: {pct: 50, reset: '11-30 09:00'}, week: {pct: 60, reset: '11-30 09:00'}, stale: true,
        error: null, snapshot: null, account_ref: '%s'})]; window.__brief_i = 0; }""" % REF)
    page.evaluate("window.refreshUsageBrief()")
    page.wait_for_timeout(300)
    check("膠囊拿到的是舊資料（stale）時，不會把面板那一列蓋成舊的", "7%" in (row_text(page, REF) or ""),
          row_text(page, REF))
    check("頁面沒有 JS 錯誤", not errors, errors)
    page.close()

    print("3. 真的查不到登入才顯示「未登入」")
    page, errors = open_page(browser, [STALE_BRIEF], state(None, ""),
                             {"success": True, "providers": {"claude": {}}})
    page.click("#usage-brief")
    page.wait_for_selector(".account-scope-card")
    scopes = scope_text(page)
    check("目前 Session：未登入", "未登入" in scopes[0] and "全域" not in scopes[0], scopes)
    page.close()

    print("4. 分頁綁的是別的帳號：面板別的帳號的數字不會蓋到膠囊")
    page, errors = open_page(browser, [{**STALE_BRIEF, "account_ref": OTHER}], state(ACC2, ""),
                             {"success": True, "providers": {"claude": {REF: FRESH_ROW}}})
    page.click("#usage-brief")
    page.wait_for_selector(".account-scope-card")
    page.wait_for_function("document.querySelector('[data-usage-key] .au-v')")
    page.wait_for_timeout(300)
    after = pill(page)
    check("膠囊仍是這個分頁自己帳號的（舊）數字", after["stale"] and "85%" in after["text"], after)
    check("「目前 Session」不標全域", "全域" not in scope_text(page)[0], scope_text(page))
    page.close()
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
