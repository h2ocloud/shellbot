#!/usr/bin/env python3
"""GitHub on the connector core: the description (connectors/services/github.json), its adapter, and the core's "every
AI program, every account folder" scope (`"targets": "all"`), with stand-ins for every AI program (a runner that keeps
entries per account folder and runs nothing) and an in-memory key store. No real settings, key store or network.

  - the description and adapter validate; Linux is not a platform; the entry's command is the install's absolute python
    and shellbot_mcp.py, with no environment and no secret in it
  - the token is pasted into a field, goes to the key store, and is in no argument list, environment, state file or answer
  - `cli_add` reaches ~/.claude plus each Claude account folder, ~/.codex plus each Codex one, the single-place programs
    once, and only shows the manual ones; unticking an AI skips it; Pi older than 0.99 is left out
  - remove and check look in every folder; a folder that lost the entry makes the service need reconnecting
  - "also allow changes" is the only way `connect.github.readonly` becomes false; disconnect puts it back
  - Plaud (no `targets`) is unchanged: one program, one folder, as before

Run: .venv/bin/python tests_connector_github.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
os.environ.setdefault("SHELLFRAME_TESTS", "1")
sys.modules.setdefault("webview", MagicMock())
import _connectors_fake as fake  # noqa: E402
from connectors import clis as clis_mod, engine as eng_mod, registry, secrets  # noqa: E402
from _connectors_homes_fake import HomeRunner  # noqa: E402
from connectors.services import github  # noqa: E402

passed = failed = 0
TOKEN = "github_pat_FAKE0000TOKEN1111FOR2222TESTS3333ONLY4444"
SERVICES = HERE / "connectors" / "services"
TMP = Path(tempfile.mkdtemp(prefix="sf-github-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class Rig:
    def __init__(self, name, registry_dirs=None, cfg=None, installed=None, versions=None):
        self.dir = TMP / name
        self.dir.mkdir(parents=True)
        self.profiles = {"claude": [str(self.dir / "p" / "claude" / "a"), str(self.dir / "p" / "claude" / "b")],
                         "codex": [str(self.dir / "p" / "codex" / "x")]}
        self.runner = HomeRunner(versions)
        self.backend = secrets.MemoryBackend()
        self.config = []
        self.cfg = cfg or {}
        env = lambda cli="claude": {"HOME": str(self.dir), "CLAUDE_CONFIG_DIR": self.profiles["claude"][0], "CODEX_HOME": self.profiles["codex"][0]}   # noqa: E731
        self.paths = {"python": "/install/.venv/bin/python", "gateway": "/install/shellbot_mcp.py"}
        self.engine = eng_mod.Engine(registry=registry.Registry(dirs=registry_dirs or [SERVICES]), path=str(self.dir / "connectors.json"),
                                     clis=clis_mod.Clis(runner=self.runner, finder=fake.finder(installed) if installed else fake.finder(), env=env,
                                                        profiles=lambda cli: self.profiles.get(cli, []), paths=self.paths),
                                     backend=self.backend, ctx_factory=self.ctx, platform="mac",
                                     config_set=lambda k, v: self.config.append((k, v)))

    def ctx(self, service, route):
        from connectors.adapter import Ctx
        return Ctx(service=service, cfg=self.cfg, env={"HOME": str(self.dir)})

    def step(self, op, **args):
        return self.engine.step("github", op, args)


def sid(res):
    return ((res.get("status") or {}).get("step") or {}).get("id")


# ── the description ────────────────────────────────────────────────────────
print("== the description and the adapter ==")
desc = json.loads((SERVICES / "github.json").read_text(encoding="utf-8"))
check("github.json validates", registry.validate(desc) == [], str(registry.validate(desc)))
check("the adapter has what the description names", registry.validate_adapter(desc, github.ADAPTER) == [])
reg = registry.Registry(dirs=[SERVICES])
check("the registry takes it (no error noted)", reg.get("github") is not None and not reg.errors, str(reg.errors))
check("development category, recommended, Mac and Windows only (Linux has no key store)",
      desc["category"]["id"] == "development" and desc["recommended"] is True and desc["platforms"] == ["mac", "windows"]
      and registry.available(desc, "linux") == "not_on_platform" and registry.available(desc, "mac") == "")
route = desc["routes"][0]
check("one route, the token; every AI is `via: mcp`; it goes to all of them", route["id"] == "token" and route["targets"] == "all"
      and sorted(route["clis"]) == sorted(clis_mod.CLI_IDS) and all(v["via"] == "mcp" for v in route["clis"].values()))
check("the steps: make the token, paste it, add it, test", [s["id"] for s in route["steps"]] == ["g0", "g1", "g2", "g3"]
      and route["steps"][0]["url"].startswith("https://github.com/settings/personal-access-tokens"))
check("the add step has a tick box per AI (all on) and the write box (off)", {o["id"]: o["default"] for o in route["steps"][2]["options"]}
      == {"cli_claude": True, "cli_codex": True, "cli_agy": True, "cli_grok": True, "cli_opencode": True, "cli_cursor": True, "cli_pi": True, "github_write": False})
cmd = clis_mod.server_command(route["mcp"], False)
check("the command is this install's python and shellbot_mcp.py, absolute", os.path.isabs(cmd[0]) and cmd[0] == os.path.abspath(sys.executable)
      and os.path.isabs(cmd[1]) and cmd[1] == str(HERE / "shellbot_mcp.py") and os.path.exists(cmd[1]) and len(cmd) == 2, str(cmd))
check("the Windows command is the same two words", clis_mod.server_command(route["mcp"], True) == cmd)
check("the entry carries no environment and no secret reference", "env" not in route["mcp"] and "env_from_secret" not in route["mcp"])
check("the server is shellbot-… (ShellBot's own)", route["mcp"]["server"] == "shellbot-gateway" == github.SERVER)
bad = json.loads(json.dumps(desc))
bad["routes"][0]["config"][0]["key"] = "connect.github"
check("a config key must be connect.<service>.<name>", registry.validate(bad) != [])
bad = json.loads(json.dumps(desc))
bad["routes"][0]["targets"] = "some"
check("targets is `all` or nothing", registry.validate(bad) != [])

# ── connecting: paste, add everywhere ──────────────────────────────────────
print("== paste the token, add it to every AI and account folder ==")
r = Rig("flow")
res = r.engine.start("github")
check("start picks the one route and opens the first step", res["success"] and sid(res) == "g0", str(res)[:300])
res = r.step("done")
check("making the token is done by the person: 'I did it' moves on to the paste step", sid(res) == "g1", str(res)[:200])
res = r.step("act", action="save", value="")
check("an empty paste is refused and the step stays", sid(res) == "g1" and (res["view"]["step"]["result"] or {}).get("code") == "empty", str(res)[:300])
res = r.step("act", action="save", value=TOKEN)
check("the pasted token is saved in the key store and the step moves on", r.backend.get("github", "token") == TOKEN and sid(res) == "g2", str(res)[:300])
state_text = (r.dir / "connectors.json").read_text(encoding="utf-8")
check("the token is not in the state file, nor in the answer of the step", TOKEN not in state_text and TOKEN not in json.dumps(res), state_text[:300])
res = r.step("act", action="add", options={"github_write": False})
check("`add` connects", res["success"] and res["status"]["state"] in ("waiting_user", "verifying") and sid(res) == "g3", str(res)[:300])
adds = r.runner.of("add")
def homes(cli): return sorted(c[2] for c in adds if c[0] == cli)
check("Claude: the default folder plus each account folder", homes("claude") == sorted(["", *r.profiles["claude"]]), str(homes("claude")))
check("Codex: the default folder plus each account folder", homes("codex") == sorted(["", *r.profiles["codex"]]), str(homes("codex")))
check("agy and Grok: once, one settings place", len(homes("agy")) == 1 and len(homes("grok")) == 1)
check("OpenCode, Cursor and Pi are not run (manual: shown, never executed)", not [c for c in r.runner.calls if c[0] in ("opencode", "cursor", "pi") and c[1] in ("add", "remove")])
check("the default folder's call runs without CLAUDE_CONFIG_DIR / CODEX_HOME; a profile's with its own",
      all("CLAUDE_CONFIG_DIR" not in c[4] for c in adds if c[0] == "claude" and c[2] == "")
      and all(c[4].get("CLAUDE_CONFIG_DIR") == c[2] for c in adds if c[0] == "claude" and c[2])
      and all("CODEX_HOME" not in c[4] for c in adds if c[0] == "codex" and c[2] == "")
      and all(c[4].get("CODEX_HOME") == c[2] for c in adds if c[0] == "codex" and c[2]))
claude_add = next(c for c in adds if c[0] == "claude")[3]
check("claude add: user scope, the absolute command, and NO -e environment", claude_add[1:] == ["mcp", "add", "--transport", "stdio", "--scope", "user", "shellbot-gateway", "--", "/install/.venv/bin/python", "/install/shellbot_mcp.py"], str(claude_add))
codex_add = next(c for c in adds if c[0] == "codex")[3]
check("codex add: the same, no --env", codex_add[1:] == ["mcp", "add", "shellbot-gateway", "--", "/install/.venv/bin/python", "/install/shellbot_mcp.py"], str(codex_add))
blob = json.dumps(r.runner.calls)
check("the token is in no argument list and no environment of any program run", TOKEN not in blob and "github_pat" not in blob)
rec = r.engine.store.get("github")
check("recorded: connected for the ones ShellBot set up, manual for the rest", rec["clis"].get("claude") == "connected" and rec["clis"].get("codex") == "connected"
      and rec["clis"].get("opencode") == "manual" and rec["clis"].get("cursor") == "manual" and rec["clis"].get("pi") == "manual", str(rec["clis"]))
check("with writing not ticked, the setting is read-only", r.config and r.config[-1] == ("connect.github.readonly", True), str(r.config))

r2 = Rig("manual")
r2.engine.start("github"); r2.step("done"); r2.step("act", action="save", value=TOKEN)
man = {m["cli"]: m for m in r2.engine.view("github")["step"].get("manual", [])}
check("at the add step the manual AIs are listed with the command to enter, and no secret", set(man) == {"opencode", "cursor", "pi"}
      and man["cursor"]["command"] == ["/install/.venv/bin/python", "/install/shellbot_mcp.py"] and TOKEN not in json.dumps(man) and man["pi"]["env"] == {}, str(man)[:400])

# ── again: every folder gets replaced, none duplicated ─────────────────────
r.engine.step("github", "back", {})
n = len(r.runner.of("add"))
res = r.step("act", action="add")
check("running it again adds once per folder again (remove then add)", len(r.runner.of("add")) - n == n and all(len(v) == 1 for v in r.runner.entries.values() if v), str(r.runner.entries))

# ── a folder that lost it ──────────────────────────────────────────────────
r.runner.entries[("claude", r.profiles["claude"][1])].clear()
rec = r.engine.store.get("github")
def mark_connected(x):
    x["state"] = "connected"
r.engine.store.update("github", mark_connected)
out = r.engine.refresh("github", force=True)
check("an entry gone from one account folder makes the service need reconnecting (cli_entry_missing)", out["status"]["state"] == "needs_reconnect"
      and (out["status"].get("problem") or {}).get("code") == "cli_entry_missing", str(out["status"])[:300])
r.engine._b_cli_add("github", r.engine.registry.route("github"), {})
r.engine.store.update("github", mark_connected)
out = r.engine.refresh("github", force=True)
check("after adding again it is connected", out["status"]["state"] == "connected", str(out["status"])[:900])

# ── disconnect ─────────────────────────────────────────────────────────────
res = r.engine.disconnect("github")
check("disconnect removes the entry from every folder of every AI program", all(not v for v in r.runner.entries.values()), str(r.runner.entries))
check("disconnect deletes the token from the key store", r.backend.get("github", "token") is None)
check("disconnect takes the settings back to their defaults (removed)", r.config[-1] == ("connect.github.readonly", None), str(r.config[-3:]))
check("only shellbot-… was ever touched", all(a == "shellbot-gateway" for c in r.runner.calls if c[1] in ("add", "remove") for a in c[3] if str(a).startswith("shellbot-")))

# ── the person's choices ───────────────────────────────────────────────────
print("== untick an AI, allow changes, Pi's version ==")
r = Rig("opts")
r.engine.start("github"); r.step("done"); r.step("act", action="save", value=TOKEN)
r.step("act", action="add", options={"cli_codex": False, "cli_grok": False, "github_write": True})
check("an unticked AI is skipped, the others are done", not r.runner.of("add", "codex") and not r.runner.of("add", "grok") and r.runner.of("add", "claude") and r.runner.of("add", "agy"))
check("ticking 'also allow changes' is what turns read-only off", r.config[-1] == ("connect.github.readonly", False), str(r.config))
r = Rig("pi-old", versions={"pi": "pi 0.98.2"})
check("Pi older than 0.99 is not offered; 0.99 and newer, and a Pi that does not say, are",
      "pi" not in r.engine._targets(route, {}, "github", "add") and "pi" in Rig("pi-new", versions={"pi": "pi 0.99.0"}).engine._targets(route, {}, "github", "add")
      and "pi" in Rig("pi-unsure", versions={"pi": "pi dev"}).engine._targets(route, {}, "github", "add"))
r = Rig("only-some", installed=("claude",))
check("an AI that is not installed is not touched", r.engine._targets(route, {}, "github", "add") == ["claude"], str(r.engine._targets(route, {}, "github", "add")))

# ── Plaud and the others: one program, one folder, as before ───────────────
print("== a route without targets keeps its scope ==")
r = Rig("plaud")
r.cfg = {"agent_roster": {"a": {"cmd": "claude", "bot": {"primary": True}}}}
res = r.engine.start("plaud")
rt = r.engine.registry.route("plaud")
check("plaud has no `targets`", "targets" not in rt)
out = r.engine._b_cli_add("plaud", rt, {})
adds = r.runner.of("add")
check("Plaud: one add, one program, in the folder a tab would use (no extra homes)", len(adds) == 1 and adds[0][0] == "claude"
      and adds[0][4].get("CLAUDE_CONFIG_DIR") == r.profiles["claude"][0] and len(r.runner.of("remove")) == 1, str(r.runner.calls))
check("Plaud's command is still the pinned npx one", adds[0][3][-3:] == ["npx", "-y", "@plaud-ai/mcp@0.3.14"] or "npx" in adds[0][3], str(adds[0][3]))
check("Plaud: remove and check look at one folder too", r.engine._b_cli_check("plaud", rt, {})["state"] == "present" and len(r.runner.of("get")) == 1)

# ── the adapter ────────────────────────────────────────────────────────────
print("== the test and the health check ==")
for cli in ("claude", "codex"):
    p = github.ADAPTER.prompt(cli)
    check(f"{cli}: one line; one read-only call, a count, a verdict line; no opening, no shellbot_status, no changes",
          "\n" not in p and "read-only" in p and "GITHUB-TEST: OK" in p and "GITHUB-TEST: FAIL" in p and "do not call shellbot_status" in p
          and "do not create or change anything" in p and "Do not open any file" in p, p)
check("Claude's sentence loads the tools with ToolSearch; Codex's names the server", "ToolSearch" in github.ADAPTER.prompt("claude") and "ToolSearch" not in github.ADAPTER.prompt("codex")
      and "shellbot-gateway" in github.ADAPTER.prompt("codex"))
ask = {"kind": "user_msg", "text": github.ADAPTER.prompt("claude")}
call = lambda n: {"kind": "tool_call", "tool": n}      # noqa: E731
say = lambda t: {"kind": "assistant_text", "text": t}  # noqa: E731
end = {"kind": "turn_end"}
J = github.ADAPTER.judge
PRE = "mcp__shellbot-gateway__"
check("judge: a github_ call and OK is a pass; Codex's `_` spelling too",
      J([ask, call(PRE + "github_search_repositories"), say("1\nGITHUB-TEST: OK"), end])["verdict"] == "pass"
      and J([ask, call("mcp__shellbot_gateway__github_get_me"), say("1\nGITHUB-TEST: OK"), end])["verdict"] == "pass")
check("judge: only shellbot_status called means the token is missing or refused (needs_auth), never a pass",
      J([ask, call(PRE + "shellbot_status"), say("GITHUB-TEST: OK"), end])["verdict"] == "needs_auth")
check("judge: no call and OK proves nothing (said_ok); no call is no_tools; a call to another server does not count",
      J([ask, say("GITHUB-TEST: OK"), end])["verdict"] == "said_ok" and J([ask, say("nope"), end])["verdict"] == "no_tools"
      and J([ask, call("mcp__other__github_x"), say("GITHUB-TEST: OK"), end])["verdict"] == "said_ok")
check("a verdict sends the person back to the step that fixes it", github.FAILED_STEP == {"no_tools": "g2", "needs_auth": "g1", "failed": "g1"})
check("every verdict the judge can give has words in the description", all(v in route["test"]["verdicts"] for v in
      ("pass", "needs_auth", "no_tools", "failed", "said_ok", "unclear", "timeout", "not_ready", "closed", "start_failed", "needs_allow")))
from connectors.adapter import Ctx  # noqa: E402
mem = secrets.SecretStore("github", secrets.MemoryBackend())
ctx = Ctx(service="github", secrets=mem)
check("health: no token in the key store is `revoked`, a token is ok; the key store without support is unknown",
      github.ADAPTER.health(ctx, "token") == {"state": "revoked"} and (mem.set("token", TOKEN) or True) and github.ADAPTER.health(ctx, "token") == {"state": "ok"}
      and github.ADAPTER.health(Ctx(service="github", secrets=secrets.SecretStore("github", secrets.UnsupportedBackend())), "token") == {"state": "unknown"})

print("== the app's config writer ==")
import api_host  # noqa: E402
box = {}
def upd(fn):
    out = fn(box)
    return out if isinstance(out, dict) else box
api_host.bind({"load_config": lambda: box, "update_config": upd, "ACCOUNT_MANAGER": None})
import api_connect  # noqa: E402
mix = api_connect.ConnectApiMixin()
mix._connect_config_set("connect.github.readonly", False)
check("connect.github.readonly is written under `connect` in the config", box == {"connect": {"github": {"readonly": False}}}, str(box))
mix._connect_config_set("connect.github.readonly", None)
check("None takes it out again, and the empty blocks with it", box == {}, str(box))
try:
    mix._connect_config_set("telegram.token", "x")
    wrote = True
except ValueError:
    wrote = False
check("anything outside connect.<service>.<name> is refused", not wrote and box == {})

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
