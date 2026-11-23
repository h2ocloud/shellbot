#!/usr/bin/env python3
"""Figma in the wizard card: web/connect.js in a headless browser talking to the real Python side (api_connect's js_api, the
engine, figma.json and the Figma adapter as shipped) with stand-ins for Claude Code, Codex and Cursor (_remote_fake.py), a made-up
home folder, and a stand-in for the temporary test tab. No network, no browser of the person's, no Figma, no sign-in.

  1. zh-TW: Connect -> the add step; the four AIs Figma does not list have no button, and the words say where to use Figma;
     "Add it for me" lists a row for each AI and account folder; the sign-in step lists who still has to sign in (a screenshot)
  2. en: the same words in English, and a second press changes nothing ("already there")
  3. another address under the name figma: the card says it was left alone
WebKit (the Mac app's engine), headless. Screenshots: qa-shots/connect-figma-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_connect_figma_ui.py
"""
import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_connect_figma_ui.py（沒裝 playwright）")
    sys.exit(0)

sys.modules.setdefault("webview", MagicMock())
import api_host  # noqa: E402

api_host.bind({"load_config": lambda: {}, "update_config": lambda fn: None,
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import _connectors_fake as fake  # noqa: E402
import api_connect  # noqa: E402
from _remote_fake import FakeProgram, find, world  # noqa: E402
from connectors import clis, engine as eng_mod, registry, remote_mcp  # noqa: E402
from connectors.adapter import Ctx  # noqa: E402
from connectors.services import figma  # noqa: E402

PRE = "mcp__figma__"


class Kit:
    """The temporary test tab, as connect_tabs.TabKit."""

    def __init__(self):
        self.asked, self.snap, self.alive = [], {}, set()

    def command(self, cli="claude"):
        return cli

    def open(self, cmd, title):
        sid = f"s{900 + len(self.alive)}"
        self.alive.add(sid)
        return sid

    def ask(self, sid, text, ready_timeout=90.0):
        self.asked.append((sid, text))

    def phase(self, sid):
        return "asking" if sid in self.alive else "closed"

    def read(self, sid):
        return dict(self.snap) if sid in self.alive else {"alive": False}

    def close(self, sid):
        self.alive.discard(sid)


class Api(api_connect.ConnectApiMixin):
    """The real js_api over a real engine, the shipped descriptions, a made-up home with two Claude accounts and one Codex account."""

    def __init__(self):
        self.sessions = {}
        self.kit = Kit()
        self.prog = FakeProgram()
        self.home, self.cfg = world()
        figma.make_remote = lambda ctx: remote_mcp.Remote(self.prog, find(), env=dict(ctx.env or {}), cfg={"accounts": {"profiles": {"claude": [
            {"id": "claude-aaaa1111", "label": "Work account"}, {"id": "claude-bbbb2222", "label": "Personal"}]}}},
            launcher=self.prog.launcher, home=self.home, cfg_dir=self.cfg)
        self._connect_engine = eng_mod.Engine(
            registry=registry.Registry(), path=str(self.home / "connectors.json"),
            clis=clis.Clis(runner=self.prog, finder=find()), backend=fake.backend(), platform="mac", ctx_factory=self.ctx)

    def ctx(self, service, route):
        c = Ctx(service=service, route=route, cfg={}, env={"PATH": "/nowhere"}, tabs=self.kit)
        c.find = lambda b: f"/fake/bin/{'cursor' if b == 'cursor-agent' else b}"
        c.run = self.prog
        return c

    def pass_test(self):
        sid, q = self.kit.asked[-1]
        self.kit.snap = {"alive": True, "state": "", "events": [{"kind": "user_msg", "text": q}, {"kind": "tool_call", "tool": PRE + "whoami"},
                                                                 {"kind": "assistant_text", "text": "ok\nFIGMA-TEST: OK"}, {"kind": "turn_end"}]}


STUB = r"""
const LANG = "__LANG__";
window.__calls = []; window.__opened = [];
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    if (String(prop).startsWith('connect_')) return window.__py(String(prop), JSON.stringify(args));
    const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
    switch (prop) {
      case 'get_config': return R({settings: {language: LANG, sidebar_open: true}, presets: [{name: 'Claude', cmd: 'claude', icon: 'C'}], agent_roster: {}});
      case 'list_sessions': return R([{sid: 's501', cmd: 'claude', alive: true, label: '示範分頁'}]);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'open_url': window.__opened.push(args[0]); return R('ok');
      default: return R('{}');
    }
  };
}})};
"""


def open_page(browser, api, lang):
    page = browser.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.expose_function("__py", lambda name, args: getattr(api, name)(*json.loads(args)))
    page.add_init_script(STUB.replace("__LANG__", lang))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("window.SFConnect && SFConnect.list().loaded", timeout=15000)
    page.click("#btn-settings")
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector('#settings-panel-connect .cg-row[data-service="figma"]', timeout=5000)
    return page, errors


def shot(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.locator("#cn-card").screenshot(path=str(SHOTS / name))


def card(page):
    return page.inner_text("#cn-card")


def start(page):
    page.click('#settings-panel-connect .cg-row[data-service="figma"] button')
    page.wait_for_selector("#cn-card .cn-how", timeout=8000)


def adds(api):
    return [c for c in api.prog.calls if c[2][:2] == ["mcp", "add"] or "<until" in c[2][-1]]


def run(browser):
    print("1. zh-TW: add, then sign in")
    api = Api()
    page, errors = open_page(browser, api, "zh-TW")
    start(page)
    text = card(page)
    check("Connect opens the add step (one way, three steps)", "把 Figma 加進你的 AI" in text and "第 1 步，共 3 步" in page.inner_text("#cn-card .cn-prog-label"), text)
    check("the four AIs Figma does not list are named, with where to use Figma, and there is one button only",
          page.locator('#cn-card [data-notlisted]').count() == 1 and all(n in page.inner_text('#cn-card [data-notlisted]') for n in ("Antigravity", "Grok Build", "OpenCode", "Pi"))
          and "請在 Claude Code 或 Codex 的分頁用" in page.inner_text('#cn-card [data-notlisted]') and page.locator('#cn-card [data-act="do"]').count() == 1, text)
    check("nothing has been added before the button is pressed", not adds(api))
    page.click('#cn-card [data-act="do"]')
    page.wait_for_selector("#cn-card .cn-rows li", timeout=8000)
    rows = page.locator("#cn-card .cn-rows li")
    check("a row for every AI and account folder: default and each account of Claude Code, Codex, and Cursor", rows.count() == 6
          and page.locator('#cn-card .cn-rows li[data-cli="claude"]').count() == 3 and page.locator('#cn-card .cn-rows li[data-cli="codex"]').count() == 2
          and page.locator('#cn-card .cn-rows li[data-cli="cursor"]').count() == 1, page.inner_text("#cn-card .cn-rows"))
    check("each row says it was added; the accounts are told apart by their labels", page.locator('#cn-card .cn-rows li[data-state="added"]').count() == 6
          and "Work account" in page.inner_text("#cn-card .cn-rows") and "Personal" in page.inner_text("#cn-card .cn-rows"), page.inner_text("#cn-card .cn-rows"))
    check("ShellBot ran claude/codex adds for every folder, and Codex only until its Added line", len([a for a in adds(api) if a[0] == "claude"]) == 3
          and len([a for a in adds(api) if a[0] == "codex"]) == 2 and all("<until" in a[2][-1] for a in adds(api) if a[0] == "codex"), adds(api))
    check("the step stays and says Added to 6 of 6", "6 / 6" in page.inner_text("#cn-card .cn-msg"), card(page))
    shot(page, "connect-figma-add-zh.png")
    page.click('#cn-card [data-act="did"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('登入 Figma')", timeout=15000)
    check("the sign-in step lists who still has to sign in, per account folder", page.locator('#cn-card .cn-rows li[data-state="needs_login"]').count() == 5
          and page.locator('#cn-card .cn-rows li[data-cli="cursor"][data-state="unverified"]').count() == 1, card(page))
    check("it says how, for each AI: /mcp, codex mcp login, Cursor's Connect", all(w in card(page) for w in ("/mcp", "codex mcp login figma", "Connect")), card(page))
    shot(page, "connect-figma-signin-zh.png")
    for k in api.prog.state:                                   # the person signs in everywhere that can be read
        api.prog.state[k] = {**api.prog.state[k], "status": "✔ connected", "auth": "oauth"}
    page.click('#cn-card [data-act="check"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('測試接通')", timeout=15000)
    kept = api._connect().store.get("figma")["steps"]["f2"]["check"]
    check("Check again: five places read as signed in, so the card moved on by itself (Cursor's cannot be read and does not block)",
          kept["code"] == "ok" and sum(1 for r in kept["rows"] if "|signed_in|" in r) == 5, kept)
    page.click('#cn-card [data-act="test-start"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-spin')", timeout=5000)
    check("the test asks for whoami once and not to repeat the answer", len(api.kit.asked) == 1 and "whoami" in api.kit.asked[0][1] and "FIGMA-TEST" in api.kit.asked[0][1]
          and "不要重複" in api.kit.asked[0][1], api.kit.asked)
    api.pass_test()
    page.wait_for_selector("#cn-card .cn-result.ok", timeout=8000)
    check("connected", api._connect().status("figma")["state"] == "connected" and "已連結" in page.inner_text("#cn-card .cn-result"))
    check("no page errors", not errors, errors)
    page.close()

    print("2. en: the same, and a second press changes nothing")
    api = Api()
    page, errors = open_page(browser, api, "en")
    start(page)
    check("English: the add step and the not-listed AIs", "Add Figma to your AIs" in card(page) and "only lets the programs on its approved list" in page.inner_text("#cn-card [data-notlisted]")
          and "Use Figma from a Claude Code or Codex tab" in page.inner_text("#cn-card [data-notlisted]"), card(page))
    page.click('#cn-card [data-act="do"]')
    page.wait_for_selector("#cn-card .cn-rows li", timeout=8000)
    check("English rows: Added", page.locator('#cn-card .cn-rows li[data-state="added"]').count() == 6 and "Added" in page.inner_text("#cn-card .cn-rows"))
    n = len(adds(api))
    page.click('#cn-card [data-act="do"]')
    page.wait_for_function("document.querySelectorAll('#cn-card .cn-rows li[data-state=\"present\"]').length === 6", timeout=15000)
    check("pressing again: every row is 'Already there' and nothing more was run", len(adds(api)) == n and "Already there" in page.inner_text("#cn-card .cn-rows"), (n, len(adds(api))))
    shot(page, "connect-figma-add-en.png")
    check("no page errors", not errors, errors)
    page.close()

    print("3. another address under the name figma is left alone")
    api = Api()
    api.prog.state[("claude", "default")] = {"url": "https://other.example.com/mcp", "status": "✔ connected"}
    page, errors = open_page(browser, api, "zh-TW")
    start(page)
    page.click('#cn-card [data-act="do"]')
    page.wait_for_selector("#cn-card .cn-rows li", timeout=8000)
    row = page.locator('#cn-card .cn-rows li[data-cli="claude"]').first
    check("the default Claude folder says it was left alone, the others were added", row.get_attribute("data-state") == "conflict" and "沒有動它" in row.inner_text()
          and page.locator('#cn-card .cn-rows li[data-state="added"]').count() == 5
          and api.prog.state[("claude", "default")]["url"] == "https://other.example.com/mcp", page.inner_text("#cn-card .cn-rows"))
    shot(page, "connect-figma-conflict-zh.png")
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as p:
    browser = p.webkit.launch(headless=True)
    try:
        run(browser)
    finally:
        browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
