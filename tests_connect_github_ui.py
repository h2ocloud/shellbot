#!/usr/bin/env python3
"""GitHub's token wizard in the card: web/connect.js in a headless browser talking to the real Python side (api_connect's
js_api, the engine, github.json and the adapter as shipped) with stand-ins for every AI program (a runner that keeps entries
per account folder) and an in-memory key store. Made-up token, no network, nothing real.

  1. the paste step: a password field and one button; an empty press is refused without a call; the pasted value is sent once,
     the field is emptied at once, the token is in the key store and nowhere else; Enter in the field presses the button
  2. the add step: one tick box per AI (all on) and "also allow changes" (off); unticking an AI skips it; the entry goes into
     every account folder; the card moves on
WebKit, headless, zh-TW. Screenshot: qa-shots/connect-github-wizard-zh.png. SKIP without playwright.
Run: .venv/bin/python tests_connect_github_ui.py
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
TOKEN = "github_pat_FAKE0000TOKEN1111FOR2222TESTS3333ONLY4444"


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:500]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_connect_github_ui.py（沒裝 playwright）")
    sys.exit(0)

sys.modules.setdefault("webview", MagicMock())
import api_host  # noqa: E402

CONFIG = []
api_host.bind({"load_config": lambda: {}, "update_config": lambda fn: CONFIG.append(fn),
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import _connectors_fake as fake  # noqa: E402
import api_connect  # noqa: E402
from connectors import clis, engine as eng_mod, registry, secrets  # noqa: E402
from connectors.adapter import Ctx  # noqa: E402
from connectors.clis import Result  # noqa: E402
from _connectors_homes_fake import HomeRunner  # noqa: E402


class Api(api_connect.ConnectApiMixin):
    def __init__(self, installed=None):
        self.sessions = {}
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="github-ui-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))
        self.runner = HomeRunner()
        self.backend = secrets.MemoryBackend()
        self.profiles = {"claude": [str(self.home / "p" / "claude" / "work"), str(self.home / "p" / "claude" / "home")], "codex": [str(self.home / "p" / "codex" / "main")]}
        self._connect_engine = eng_mod.Engine(
            registry=registry.Registry(), path=str(self.home / "connectors.json"), backend=self.backend, platform="mac", ctx_factory=self.ctx,
            clis=clis.Clis(runner=self.runner, finder=fake.finder(installed) if installed else fake.finder(), profiles=lambda cli: self.profiles.get(cli, []),
                           paths={"python": "/install/.venv/bin/python", "gateway": "/install/shellbot_mcp.py"}),
            config_set=lambda k, v: CONFIG.append((k, v)))

    def ctx(self, service, route):
        return Ctx(service=service, route=route, cfg={}, env={"HOME": str(self.home)})


STUB = r"""
window.__calls = [];
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    if (String(prop).startsWith('connect_')) return window.__py(String(prop), JSON.stringify(args));
    const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
    switch (prop) {
      case 'get_config': return R({settings: {language: 'zh-TW', sidebar_open: true}, presets: [{name: 'Claude', cmd: 'claude', icon: 'C'}], agent_roster: {}});
      case 'list_sessions': return R([{sid: 's501', cmd: 'claude', alive: true, label: '示範分頁'}]);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'open_url': return R('ok');
      default: return R('{}');
    }
  };
}})};
"""


def open_page(browser, api):
    page = browser.new_page(viewport={"width": 1100, "height": 860}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.expose_function("__py", lambda name, args: getattr(api, name)(*json.loads(args)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("window.SFConnect && SFConnect.list().loaded", timeout=15000)
    page.click("#btn-settings")
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector('#settings-panel-connect .cg-row[data-service="github"]', timeout=5000)
    return page, errors


def title(page):
    return page.inner_text("#cn-card .cn-title")


def acts(page, action):
    return [c for c in page.evaluate("window.__calls") if c[0] == "connect_step" and c[1][1] == "act" and json.loads(c[1][2]).get("action") == action]


def run(browser):
    api = Api()
    page, errors = open_page(browser, api)
    row = page.locator('#settings-panel-connect .cg-row[data-service="github"]')
    check("GitHub is in the catalog, under Development (開發), as a recommended service", "開發" in page.inner_text("#settings-panel-connect")
          and row.count() == 1, page.inner_text("#settings-panel-connect")[:300])
    row.locator("button").click()
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("one way only, so it opens on the first step: make a token", "GitHub token" in title(page) or "建立" in title(page), title(page))
    check("the first step has the token page's link and the three things to choose (repos, read-only, expiry)", "唯讀" in page.inner_text("#cn-card") and "Only select repositories" in page.inner_text("#cn-card"))
    page.click('#cn-card [data-act="did"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('貼上')", timeout=5000)

    print("1. paste the token")
    field = page.locator('#cn-card input[data-secret="save"]')
    check("the paste step has a password field", field.count() == 1 and field.get_attribute("type") == "password" and field.get_attribute("autocomplete") == "off")
    page.click('#cn-card [data-act="do"]')
    page.wait_for_timeout(300)
    check("an empty press is refused on the page: no call went to the app, the card says to paste it first",
          not acts(page, "save") and "請先把內容貼進欄位" in page.inner_text("#cn-card"), page.inner_text("#cn-card"))
    field = page.locator('#cn-card input[data-secret="save"]')
    field.fill(TOKEN)
    check("typing clears the 'paste it first' message", "請先把內容貼進欄位" not in page.inner_text("#cn-card"), page.inner_text("#cn-card"))
    page.screenshot(path=str(SHOTS / "connect-github-wizard-zh.png"), clip=page.locator("#cn-card").bounding_box())
    field.press("Enter")
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('加進你的 AI')", timeout=5000)
    sent = acts(page, "save")
    check("Enter pressed the button: the value went once, with the action", len(sent) == 1 and json.loads(sent[0][1][2]).get("value") == TOKEN)
    check("the token is in the key store", api.backend.get("github", "token") == TOKEN)
    check("the token is in no state file, no page text, no field", TOKEN not in (api.home / "connectors.json").read_text() and TOKEN not in page.content()
          and TOKEN not in page.inner_text("body") and page.locator("#cn-card input[data-secret]").count() == 0)

    print("2. add it to the AIs")
    text = page.inner_text("#cn-card")
    boxes = page.locator("#cn-card .cn-opt")
    check("seven AIs with a tick box each (on), and 'also allow changes' (off)", boxes.count() == 8
          and [boxes.nth(i).get_attribute("aria-checked") for i in range(8)] == ["true"] * 7 + ["false"], [boxes.nth(i).get_attribute("aria-checked") for i in range(boxes.count())])
    check("the step says it goes into every AI and every account folder, and GitHub is read-only unless ticked", "每個帳號資料夾" in text and "唯讀" in text and "也允許修改" in text, text)
    page.screenshot(path=str(SHOTS / "connect-github-add-zh.png"), clip=page.locator("#cn-card").bounding_box())
    boxes.nth(1).click()                                  # Codex off
    check("unticking Codex shows it off", boxes.nth(1).get_attribute("aria-checked") == "false")
    page.click('#cn-card [data-act="do"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('測試接通')", timeout=8000)
    calls = api.runner.of("add")
    check("every Claude account folder and the default one got it; Codex, unticked, did not", sorted(c[2] for c in calls if c[0] == "claude") == sorted(["", *api.profiles["claude"]])
          and not [c for c in calls if c[0] == "codex"], [(c[0], c[2]) for c in calls])
    check("writing stays off: the setting is read-only", CONFIG and CONFIG[-1] == ("connect.github.readonly", True), CONFIG)
    check("no token in any run program's arguments or environment", TOKEN not in json.dumps(api.runner.calls))
    check("the test step is next, and the card has no page errors", "測試接通" in title(page) and not errors, errors)
    page.close()


def run_partial(browser):
    print("3. only some AIs installed: the others are not offered, and the manual one shows what to enter")
    api = Api(installed=("claude", "opencode"))
    page, errors = open_page(browser, api)
    page.locator('#settings-panel-connect .cg-row[data-service="github"] button').click()
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    page.click('#cn-card [data-act="did"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('貼上')", timeout=5000)
    page.click('#cn-card [data-act="do"]')                       # empty: refused on the page
    page.locator('#cn-card input[data-secret="save"]').fill(TOKEN)
    page.click('#cn-card [data-act="do"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('加進你的 AI')", timeout=5000)
    labels = page.locator("#cn-card .cn-opt").all_inner_texts()
    check("tick boxes only for the installed AIs, plus 'also allow changes'", [x for x in labels if "加進" in x] == ["加進 Claude Code", "加進 OpenCode"] and any("也允許修改" in x for x in labels), labels)
    man = page.locator("#cn-card .cn-manual")
    check("OpenCode (set up by hand) is listed with the command to enter; nothing for the AIs that are not installed",
          man.count() == 1 and "OpenCode" in man.inner_text() and "/install/.venv/bin/python" in man.inner_text() and "Cursor" not in man.inner_text() and "Pi" not in man.inner_text().replace("Python", ""), man.inner_text() if man.count() else "")
    page.locator("#cn-card .cn-opt").nth(1).click()                # untick OpenCode
    check("unticking it takes its entry out of the box", page.locator("#cn-card .cn-manual").count() == 0)
    check("no token on the page, no page errors", TOKEN not in page.content() and not errors, errors)
    page.close()


with sync_playwright() as p:
    browser = p.webkit.launch(headless=True)
    try:
        run(browser)
        run_partial(browser)
    finally:
        browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
