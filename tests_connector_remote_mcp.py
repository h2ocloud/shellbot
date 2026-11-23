#!/usr/bin/env python3
"""A remote MCP server added to the AI programs that may use it: the `remote_mcp` kind (registry), what is done
for each program (connectors/remote_mcp.py), and the shipped Figma description and adapter.

Stand-ins only: the programs are `FakeProgram`, which answers in the shape the real ones printed on 2026-11-23
(Claude Code 2.1.329 `mcp get`, Codex 0.142.1 `mcp list` and `mcp add --url`); Cursor's file is a temporary folder's.
No real program, account folder or settings file is touched, and nothing is signed in.

  1. the kind: a description needs `remote` {name, url}; the name is the person's own (not shellbot-…), the address https;
     `via: remote` only for the programs that can take one; an `mcp` on such a route, or `remote` on another kind, is wrong
  2. the real lines: Claude's `mcp get` and `mcp list`, Codex's table: the address and the sign-in are read out
  3. looking before adding, never replacing: no entry adds; the same address (case, trailing slash) counts as added and
     nothing is run; another address is a conflict and is left alone; a program that does not answer adds nothing
  4. every account folder: the default folder and each profile, each with its own CLAUDE_CONFIG_DIR / CODEX_HOME; the
     inherited one is dropped for the default
  5. Codex: `mcp add --url` is run until its "Added" line and then taken down (its sign-in never starts); what counts
     is what `mcp list` holds afterwards
  6. Cursor: ~/.cursor/mcp.json merged, other servers kept, the same address left as it is, a file that is not the
     expected JSON not touched
  7. the Figma description: valid, both languages, the programs Figma does not list have no button and the words say so;
     the adapter's add and checks; the engine keeps the rows; nothing secret
Run: .venv/bin/python tests_connector_remote_mcp.py
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules.setdefault("webview", MagicMock())
import _connectors_fake as fake  # noqa: E402
from connectors import clis, detect, engine as eng_mod, health, registry, remote_mcp  # noqa: E402
from connectors.adapter import Ctx  # noqa: E402
from connectors.services import figma  # noqa: E402
from _remote_fake import CODEX_HEAD, CODEX_ROW, URL, FakeProgram, find, tmp, world  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")



def remote(prog, home, cfg, installed=("claude", "codex", "cursor"), env=None, cfgd=None):
    return remote_mcp.Remote(prog, find(installed), env=env if env is not None else {"PATH": "/x", "CLAUDE_CONFIG_DIR": "/inherited"},
                             cfg=cfgd, launcher=prog.launcher, home=home, cfg_dir=cfg)


# ── 1. the kind ────────────────────────────────────────────────────────────
good = json.loads((HERE / "connectors/services/figma.json").read_text(encoding="utf-8"))
check("the Figma description is valid", registry.validate(good) == [], registry.validate(good))
check("remote_mcp is a kind and `remote` a way", "remote_mcp" in registry.KINDS and "remote" in registry.VIAS)


def bad(mutate, needle):
    d = copy.deepcopy(good)
    mutate(d)
    errs = registry.validate(d)
    return any(needle in e for e in errs), errs


R = lambda d: d["routes"][0]
ok, e = bad(lambda d: R(d).pop("remote"), "remote")
check("a remote_mcp route needs its server", ok, e)
ok, e = bad(lambda d: R(d)["remote"].update(name="shellbot-figma"), "not shellbot-")
check("the name is the person's own, not shellbot-…", ok, e)
ok, e = bad(lambda d: R(d)["remote"].update(url="http://mcp.figma.com/mcp"), "https link")
check("the address must be https", ok, e)
ok, e = bad(lambda d: R(d)["remote"].update(url="https://mcp.example.com/mcp?token=sk-abcdefghijklmnopqrstuv"), "https link")
check("no secret in the address", ok, e)
ok, e = bad(lambda d: R(d)["clis"]["agy"].update(via="remote"), "'remote' is a remote_mcp route's")
check("a program that cannot take a remote server cannot be given `via: remote`", ok, e)
ok, e = bad(lambda d: R(d).update(mcp={"server": "shellbot-x", "command": "x"}), "has `remote`, not `mcp`")
check("a remote_mcp route has no `mcp`", ok, e)
dm = fake.demo()
dm["routes"][1]["remote"] = {"name": "x", "url": URL}
check("`remote` on another kind is wrong", any("only a remote_mcp route" in e for e in registry.validate(dm)))
dm = fake.demo()
dm["routes"][1]["clis"]["claude"]["via"] = "remote"
check("`via: remote` on a local_mcp route is wrong", any("'remote' is a remote_mcp route's" in e for e in registry.validate(dm)))
check("the core's other kinds are unchanged", registry.validate(fake.demo()) == [])
check("the table says who can take it: Claude Code, Codex, Cursor; the rest cannot",
      {c: clis.mode_for(c, "remote_mcp") for c in clis.CLI_IDS} == {"claude": "auto", "codex": "auto", "cursor": "auto", "pi": "unsupported",
                                                                     "opencode": "unsupported", "agy": "unsupported", "grok": "unsupported"})
check("the other kinds' modes did not move", clis.mode_for("cursor", "local_mcp") == "manual" and clis.mode_for("claude", "claude_connector") == "connector")

# ── 2. the real lines ──────────────────────────────────────────────────────
real_claude = "Checking MCP server health…\n\nfigma: https://mcp.figma.com/mcp (HTTP) - ! Needs authentication\n"
e = detect.parse_claude(real_claude)[0]
check("Claude's list line: a remote server that needs a sign-in, with its address", (e["kind"], e["state"], e["why"], e["url"]) == ("remote", "broken", "auth", URL), e)
tab = (CODEX_HEAD + CODEX_ROW.format(url=URL.lower(), auth="oauth"))
e = detect.parse_codex(tab)[0]
check("Codex's table: a remote server, its address and its sign-in", (e["name"], e["kind"], e["url"], e["auth"]) == ("figma", "remote", URL, "oauth"), e)
check("Codex's table: not logged in is broken/auth", detect.parse_codex(CODEX_HEAD + CODEX_ROW.format(url=URL.lower(), auth="not logged in"))[0]["why"] == "auth")
check("the same address: case and a trailing slash aside", detect.same_url("https://MCP.figma.com/mcp/", URL) and not detect.same_url("https://mcp.figma.com/other", URL) and not detect.same_url("", ""))

# ── 3. look first, never replace ───────────────────────────────────────────
home, cfg = world(profiles=(), default=("claude",), cursor=False)
prog = FakeProgram()
rm = remote(prog, home, cfg, installed=("claude",))
prof = rm.profiles("claude")[0]
check("nothing there: it is added with the http transport, user scope, under the name figma",
      rm.add(prof, "figma", URL) == "added" and prog.calls[1][2] == ["mcp", "add", "--transport", "http", "-s", "user", "figma", URL], prog.calls)
n = len(prog.calls)
check("the same address is there now: counts as added, nothing is run to change it", rm.add(prof, "figma", URL) == "present" and all(c[2][1] != "add" for c in prog.calls[n:]), prog.calls[n:])
prog.state[("claude", "default")] = {"url": "https://MCP.Figma.com/mcp/", "status": "✔ connected"}
check("case and a trailing slash do not make it another address", rm.add(prof, "figma", URL) == "present")
check("signed in reads as signed_in", rm.look(prof, "figma", URL) == {"entry": "same", "auth": "signed_in"})
prog.state[("claude", "default")] = {"url": "https://other.example.com/mcp", "status": "✔ connected"}
n = len(prog.calls)
check("another address under the name: a conflict, left alone", rm.add(prof, "figma", URL) == "conflict" and all(c[2][1] != "add" for c in prog.calls[n:]))
check("a check says conflict too", rm.check_all("figma", URL)[0]["state"] == "conflict")
prog.state.clear()
prog.silent.add(("claude", "default"))
n = len(prog.calls)
check("a program that does not answer: nothing is added", rm.add(prof, "figma", URL) == "unknown" and all(c[2][1] != "add" for c in prog.calls[n:]))
prog.silent.clear()
prog.refuse_add.add(("claude", "default"))
check("a program that refuses the change: failed", rm.add(prof, "figma", URL) == "failed")
prog.refuse_add.clear()
check("a program that is not installed: missing, and nothing run",
      remote(prog, home, cfg, installed=()).add_all("figma", URL, ["claude"]) == [{"cli": "claude", "profile": "default", "label": "", "state": "missing"}])
prog.state.clear()
prog.state[("claude", "default")] = {"url": URL, "status": "✘ failed to connect"}
check("a server that fails to connect is not a sign-in", rm.check_all("figma", URL)[0]["state"] == "failed")
prog.state[("claude", "default")] = {"url": URL, "status": "? pending"}
check("a state it cannot read is unverified, not a guess", rm.check_all("figma", URL, ["claude"])[0]["state"] == "unverified")

# ── 4. every account folder ────────────────────────────────────────────────
home, cfg = world()
prog = FakeProgram()
cfgd = {"accounts": {"profiles": {"claude": [{"id": "claude-aaaa1111", "label": "Work", "email": "a@example.com"}]}}}
rm = remote(prog, home, cfg, cfgd=cfgd)
rows = rm.add_all("figma", URL)
keys = [(r["cli"], r["profile"], r["state"]) for r in rows]
check("every folder of every program gets one row: the default, each profile, and Cursor's file",
      keys == [("claude", "default", "added"), ("claude", "claude-aaaa1111", "added"), ("claude", "claude-bbbb2222", "added"),
               ("codex", "default", "added"), ("codex", "codex-cccc3333", "added"), ("cursor", "default", "added")], keys)
adds = [c for c in prog.calls if c[2][:2] == ["mcp", "add"]]
check("each Claude folder is run with its own CLAUDE_CONFIG_DIR, the default with none", sorted(a[1] for a in adds if a[0] == "claude") ==
      sorted(["default", str(cfg / "account-profiles/claude/claude-aaaa1111"), str(cfg / "account-profiles/claude/claude-bbbb2222")]), adds)
check("the inherited CLAUDE_CONFIG_DIR does not reach the default folder", not any(c[1] == "/inherited" for c in prog.calls))
check("the account's label is carried; an email is not", next(r for r in rows if r["profile"] == "claude-aaaa1111")["label"] == "Work"
      and "a@example.com" not in json.dumps(rm_rows := remote_mcp.rows_text(rows)), rm_rows)
check("a row is a short string", "claude|claude-aaaa1111|added|Work" in rm_rows and "cursor|default|added|" in rm_rows, rm_rows)
n = len(prog.calls)
again = rm.add_all("figma", URL)
check("a second run changes nothing: every row is 'present', no add is run",
      {r["state"] for r in again} == {"present"} and not [c for c in prog.calls[n:] if c[2][:2] == ["mcp", "add"] or "<until" in c[2][-1]], again)
home2, cfg2 = world(profiles=(), default=(), cursor=False)
check("a program with no folder of its own yet has no row but its 'missing' one when not installed",
      remote(FakeProgram(), home2, cfg2, installed=("claude",)).add_all("figma", URL, ["claude", "codex"]) == [
          {"cli": "codex", "profile": "default", "label": "", "state": "missing"}])

# ── 5. Codex: until the entry is written ───────────────────────────────────
codex_calls = [c for c in prog.calls if c[0] == "codex" and "<until" in c[2][-1]]
check("`codex mcp add figma --url` is run until its Added line, in each Codex folder",
      len(codex_calls) == 2 and all(c[2][:5] == ["mcp", "add", "figma", "--url", URL] and c[2][-1] == "<until added global mcp server>" for c in codex_calls), codex_calls)
check("what counts is what `mcp list` holds afterwards (a sign-in is still to do)", rm.check_all("figma", URL, ["codex"])[0]["state"] == "needs_login")
prog.state[("codex", "default")]["auth"] = "oauth"
check("Codex's OAuth column is a sign-in", rm.check_all("figma", URL, ["codex"])[0]["state"] == "signed_in")
prog2 = FakeProgram()
prog2.refuse_add.add(("codex", "default"))
check("an add that wrote nothing is failed, whatever the launcher said",
      remote(prog2, home, cfg).add(remote(prog2, home, cfg).profiles("codex")[0], "figma", URL) == "failed")
try:
    remote_mcp.spawn_default(["x"], {})
    check("the real launcher refuses under test", False)
except RuntimeError:
    check("the real launcher refuses under test", True)


class Pipe:
    def __init__(self, lines):
        self.lines = list(lines)

    def readline(self):
        return self.lines.pop(0) if self.lines else ""


class Proc:
    def __init__(self, lines):
        self.stdout, self.stdin, self.pid, self.returncode = Pipe(lines), None, 99999999, None

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


seen = remote_mcp.launch_until(["codex"], {}, "added global mcp server", 3, spawn=lambda a, e: Proc([b"Added global MCP server 'figma'.\n", b"Detected OAuth support.\n"]))
check("launch_until reads to the marker (bytes, any case) and stops", seen is True)
check("launch_until says no when the marker never shows", remote_mcp.launch_until(["codex"], {}, "added global", 3, spawn=lambda a, e: Proc(["error: nope\n"])) is False)
check("launch_until says no when the program cannot start", remote_mcp.launch_until(["codex"], {}, "x", 3, spawn=lambda a, e: (_ for _ in ()).throw(OSError("no"))) is False)

# ── 6. Cursor ──────────────────────────────────────────────────────────────
def cursor(home_dir):
    return remote(FakeProgram(), home_dir, tmp(), installed=("cursor",)), home_dir / ".cursor" / "mcp.json"


home = tmp()
(home / ".cursor").mkdir()
rm, path = cursor(home)
prof = rm.profiles("cursor")[0]
check("no file yet: it is written as {mcpServers: {figma: {url}}}", rm.add(prof, "figma", URL) == "added" and json.loads(path.read_text()) == {"mcpServers": {"figma": {"url": URL}}}, path.read_text())
before = path.read_bytes()
check("the same address in the file: present, the file is not rewritten", rm.add(prof, "figma", URL) == "present" and path.read_bytes() == before)
check("its sign-in cannot be read: unverified, not signed in", rm.check_all("figma", URL, ["cursor"])[0]["state"] == "unverified")
path.write_text(json.dumps({"theme": "x", "mcpServers": {"other": {"command": "x", "env": {"K": "v"}}, "figma": {"url": "https://example.com/mcp"}}}))
before = path.read_bytes()
check("another address under the name: conflict, the file is not touched", rm.add(prof, "figma", URL) == "conflict" and path.read_bytes() == before)
path.write_text(json.dumps({"theme": "x", "mcpServers": {"other": {"command": "x", "env": {"K": "v"}}}}))
check("other servers and other keys are kept", rm.add(prof, "figma", URL) == "added" and json.loads(path.read_text()) == {
    "theme": "x", "mcpServers": {"other": {"command": "x", "env": {"K": "v"}}, "figma": {"url": URL}}}, path.read_text())
for label, text in (("not JSON", "{ not json"), ("not an object", "[1]"), ("mcpServers not an object", '{"mcpServers": []}')):
    path.write_text(text)
    check(f"a file that is {label} is not touched", rm.add(prof, "figma", URL) in ("unknown",) and path.read_text() == text, rm.add(prof, "figma", URL))
check("no ~/.cursor and no cursor-agent: missing", remote(FakeProgram(), tmp(), tmp(), installed=()).add_all("figma", URL, ["cursor"])[0]["state"] == "missing")

# ── 7. the Figma description and adapter ───────────────────────────────────
reg = registry.Registry()
desc = reg.get("figma")
check("figma is a shipped service", desc is not None and not reg.errors, reg.errors)
route = reg.route("figma")
check("a design service, recommended, on every platform", desc["category"]["id"] == "design" and desc["recommended"] and desc["platforms"] == ["mac", "windows", "linux"])
check("one route: remote_mcp at Figma's address, under the name figma", len(desc["routes"]) == 1 and route["kind"] == "remote_mcp" and route["remote"] == {"name": "figma", "url": URL})
check("Claude Code, Codex and Cursor get a button", {c for c, s in route["clis"].items() if s["via"] == "remote"} == {"claude", "codex", "cursor"})
check("Antigravity, Grok Build, OpenCode and Pi: none, with the words",
      {c for c, s in route["clis"].items() if s["via"] == "none"} == {"agy", "grok", "opencode", "pi"}
      and all(all(w in route["clis"][c]["note"]["en"] for w in ("Claude Code", "Codex", "Cursor", "approved list")) and "Claude Code" in route["clis"][c]["note"]["zh"]
          for c in ("agy", "grok", "opencode", "pi")))
st = health.build(desc, {}, route=route, platform="mac")
check("the status shows those four as not supported and the three as unknown", {c: s for c, s in st["clis"].items()} == {
    "claude": "unknown", "codex": "unknown", "cursor": "unknown", "agy": "unsupported", "grok": "unsupported", "opencode": "unsupported", "pi": "unsupported"}, st["clis"])
check("the FAQ answers why not ShellBot, each account, and how to take it back",
      {q["id"] for q in desc["faq"]} >= {"why-not-shellbot", "each-account", "remove"}
      and "claude mcp remove figma" in next(q for q in desc["faq"] if q["id"] == "remove")["a"]["en"]
      and "codex mcp remove figma" in next(q for q in desc["faq"] if q["id"] == "remove")["a"]["en"])
text = json.dumps(desc, ensure_ascii=False)
check("no secret and no e-mail address in the description (names: tests_repo_hygiene.py)", "sk-" not in text and "@" not in text.replace("@scope", ""), "")

# the adapter, run the way the engine runs it
home, cfg = world()
prog = FakeProgram()
figma.make_remote = lambda ctx: remote_mcp.Remote(prog, find(), env=dict(ctx.env or {}), cfg=None, launcher=prog.launcher, home=home, cfg_dir=cfg)
ctx = Ctx(service="figma", route="remote", env={"PATH": "/x"}, run=prog, find=lambda b: f"/fake/bin/{'cursor' if b == 'cursor-agent' else b}")
res = figma.action_add(ctx, {})
check("add: every place got it", res["state"] == "added" and (res["done"], res["total"]) == (6, 6) and len(res["rows"]) == 6, res)
check("add again: already there", figma.action_add(ctx, {})["state"] == "present")
res = figma.check_added(ctx, {})
check("check_added: it is in 6 of 6", res["state"] == "added" and (res["have"], res["total"]) == (6, 6), res)
res = figma.check_login(ctx, {})
check("check_login: Claude and Codex need the sign-in; Cursor's cannot be read", res["state"] == "needs_login" and res["need"] == 5 and res["signed"] == 0, res)
for k in prog.state:
    prog.state[k] = {**prog.state[k], "status": "✔ connected", "auth": "oauth"}
res = figma.check_login(ctx, {})
check("check_login: all that can be read are signed in", res["state"] == "ok" and res["signed"] == 5, res)
check("the rows are strings only (a result keeps nothing nested)", all(isinstance(x, str) for x in res["rows"]))
prog.state.clear()
c = Ctx(service="figma", route="remote", env={"PATH": "/x"}, run=prog, find=lambda b: None)
home3, cfg3 = world(profiles=(), default=(), cursor=False)
figma.make_remote = lambda ctx: remote_mcp.Remote(prog, lambda cli: None, env={}, launcher=prog.launcher, home=home3, cfg_dir=cfg3)
check("no program at all: add says no_cli", figma.action_add(c, {})["state"] == "no_cli" and figma.check_added(c, {})["state"] == "no_cli")
figma.make_remote = lambda ctx: remote_mcp.Remote(prog, find(("claude",)), env={}, launcher=prog.launcher, home=home3, cfg_dir=cfg3)
prog.state[("claude", "default")] = {"url": "https://other.example.com/mcp"}
home4, cfg4 = world(profiles=(), default=("claude",), cursor=False)
figma.make_remote = lambda ctx: remote_mcp.Remote(prog, find(("claude",)), env={}, launcher=prog.launcher, home=home4, cfg_dir=cfg4)
check("another address under figma: add says conflict, and the check says so", figma.action_add(c, {})["state"] == "conflict" and figma.check_added(c, {})["state"] == "conflict")
prog.state.clear()
figma.make_remote = lambda ctx: remote_mcp.Remote(prog, find(), env={}, launcher=prog.launcher, home=home, cfg_dir=cfg)
prog.refuse_add.add(("claude", "default"))
check("one folder refusing: partial, with the others added", figma.action_add(ctx, {})["state"] == "partial")
prog.refuse_add.clear()
prog.state.clear()

# the test prompt: one call, no repeating
for cli in ("claude", "codex"):
    p = figma.ADAPTER.prompt(cli)
    check(f"the test prompt for {cli}: whoami once, no other tool, nothing repeated, the marker",
          "whoami" in p and "exactly once" in p and "do not repeat the name, email" in p and "FIGMA-TEST: OK" in p, p)
check("the Codex prompt names the server; the Claude one loads the tools", "figma MCP server" in figma.ADAPTER.prompt("codex") and "ToolSearch" in figma.ADAPTER.prompt("claude"))
judged = figma.ADAPTER.judge([{"kind": "user_msg", "text": "FIGMA-TEST"}, {"kind": "tool_call", "tool": "mcp__figma__whoami"},
                              {"kind": "assistant_text", "text": "worked\nFIGMA-TEST: OK"}, {"kind": "turn_end"}])
check("the test passes on a whoami call and an OK line", judged["verdict"] == "pass", judged)
judged = figma.ADAPTER.judge([{"kind": "user_msg", "text": "FIGMA-TEST"}, {"kind": "tool_call", "tool": "mcp__figma__authenticate"}, {"kind": "turn_end"}])
check("only the sign-in tool: needs_auth, back to the sign-in step", judged["verdict"] == "needs_auth" and figma.FAILED_STEP["needs_auth"] == "f2", judged)
judged = figma.ADAPTER.judge([{"kind": "user_msg", "text": "FIGMA-TEST"}, {"kind": "tool_call", "tool": "mcp__plugin_figma_figma__whoami"},
                              {"kind": "assistant_text", "text": "FIGMA-TEST: OK"}, {"kind": "turn_end"}])
check("Figma's own plugin's tool names count too", judged["verdict"] == "pass", judged)
check("the test CLI is the primary bot's when it is Claude Code or Codex, else Claude",
      figma.ADAPTER.pick_cli(Ctx(service="figma", cfg={"agent_roster": {"a": {"cmd": "codex", "bot": {"primary": True}}}})) == "codex"
      and figma.ADAPTER.pick_cli(Ctx(service="figma", cfg={"agent_roster": {"a": {"cmd": "agy", "bot": {"primary": True}}}})) == "claude")

# the engine: the steps, and the rows the card lists
d = tmp()
figma.make_remote = lambda ctx: remote_mcp.Remote(prog, find(), env=dict(ctx.env or {}), launcher=prog.launcher, home=home, cfg_dir=cfg)
E = eng_mod.Engine(registry=reg, path=d / "connectors.json", clis=clis.Clis(runner=prog, finder=fake.finder(("claude", "codex", "cursor"))),
                   backend=fake.backend(), ctx_factory=lambda s, r: Ctx(service=s, route=r, env={"PATH": "/x"}, find=lambda b: f"/fake/bin/{'cursor' if b == 'cursor-agent' else b}"))
r = E.start("figma")
check("the engine starts the route at its first step", r["success"] and r["view"]["step"]["id"] == "f1", r.get("code"))
r = E.step("figma", "act", {"action": "add"})
res = r["view"]["step"]["result"]
check("add: the step stays so the card can list what was done, with a row per place", r["success"] and r["view"]["step"]["id"] == "f1"
      and res["code"] == "added" and len(res["rows"]) == 6 and "6 of 6" in res["text"]["en"] and "6" in res["text"]["zh"], res)
r = E.step("figma", "done", {})
check("I did it: the sign-in step is in front, its check ran on entering and listed every place",
      r["view"]["step"]["id"] == "f2" and r["view"]["step"]["check"]["code"] == "needs_login" and len(r["view"]["step"]["check"]["rows"]) == 6, r["view"]["step"])
chk = r["view"]["step"]["check"]
check("the sign-in rows say which place needs it", "claude|default|needs_login|" in chk["rows"] and "cursor|default|unverified|" in chk["rows"], chk["rows"])
check("the words carry the counts (both languages)", "5 place(s)" in chk["text"]["en"] and "5" in chk["text"]["zh"], chk["text"])
r = E.step("figma", "done", {})
check("the person can move on without every folder signed in (no gate); the test step is next", r["view"]["step"]["id"] == "f3", r["view"]["step"]["id"])
saved = json.dumps(E.store.all())
check("what the engine keeps is codes and counts: no address, no command, no account", "mcp.figma.com" not in saved and "claude mcp" not in saved and "@" not in saved, saved[:300])
r = E.disconnect("figma")
check("disconnect forgets the progress and removes nothing from any AI", r["success"] and not [c for c in prog.calls if c[2][:2] == ["mcp", "remove"]], r)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
