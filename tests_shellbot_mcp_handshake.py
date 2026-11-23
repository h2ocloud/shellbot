#!/usr/bin/env python3
"""A real handshake: ShellBot's own `Clis.add` puts the gateway into a REAL `claude` (a temporary home and a temporary
CLAUDE_CONFIG_DIR per account folder) and a real `codex` (a temporary CODEX_HOME), and each program's own `mcp list` /
`mcp get` has to see it. Claude Code starts the server to answer and prints Connected: that is the gateway
answering `initialize` over stdio to the real client, once with no token (the key store of a test refuses) and once
behind a wrapper that points it at a made-up local upstream with a made-up token.

Nothing real is touched: HOME, CLAUDE_CONFIG_DIR and CODEX_HOME are temp folders, no account is signed in, the key
store is never reached (SHELLFRAME_TESTS makes it refuse), the upstream is on localhost. Skipped when a program is not
installed. The programs' output is printed (their own words) so the run shows what they said.

Run: .venv/bin/python tests_shellbot_mcp_handshake.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import _mcp_fake_upstream as fake  # noqa: E402
from connectors import clis as clis_mod  # noqa: E402
from connectors.clis import Result  # noqa: E402

passed = failed = 0
TMP = Path(tempfile.mkdtemp(prefix="sf-handshake-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def find(name):
    for d in ("/usr/local/bin", "/opt/homebrew/bin", str(Path.home() / ".local/bin")):
        p = shutil.which(name, path=d)
        if p:
            return p
    return shutil.which(name)


def isolated(env, home):
    """The environment a real program is run with: a temp HOME and nothing of the person's accounts."""
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TMPDIR")}
    base.update({"HOME": str(home), "SHELLFRAME_TESTS": "1", "SHELLFRAME_CONFIG_DIR": str(home / "shellbot"), "CI": "1"})
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        if k in (env or {}):
            base[k] = env[k]
    return base


def runner_for(home, log):
    def run(argv, timeout=40, env=None):
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=120, env=isolated(env, home), cwd=str(home))
        log.append((list(argv), r.returncode))
        return Result(r.returncode, (r.stdout or "") + (r.stderr or ""))
    return run


up = fake.Upstream(honor_readonly=True)
MCP = {"server": "shellbot-gateway", "command": "{python}", "args": ["{gateway}"]}
real_py = os.path.abspath(sys.executable)

# ── Claude Code ────────────────────────────────────────────────────────────
claude = find("claude")
if not claude:
    print("  [SKIP] claude is not installed")
else:
    home = TMP / "claude-home"
    home.mkdir()
    profiles = [str(TMP / "cprof" / "a"), str(TMP / "cprof" / "b")]
    for p in profiles:
        Path(p).mkdir(parents=True)
    log = []
    cl = clis_mod.Clis(runner=runner_for(home, log), finder=lambda c: claude if c == "claude" else None, profiles=lambda c: profiles,
                       paths={"python": real_py, "gateway": str(HERE / "shellbot_mcp.py")})
    r = cl.add("claude", MCP, all_homes=True)
    check("Clis.add(all_homes) with the real claude: added in the default folder and both account folders", r["state"] == "added" and r["homes"] == 3, str(r))
    check("the entry is in each account folder's own settings file and holds no token or environment",
          all("shellbot-gateway" in (Path(p) / ".claude.json").read_text() for p in profiles) and "shellbot-gateway" in (home / ".claude.json").read_text()
          and "github_pat" not in (Path(profiles[0]) / ".claude.json").read_text() and '"env": {}' in (Path(profiles[0]) / ".claude.json").read_text().replace('"env":{}', '"env": {}'),
          (Path(profiles[0]) / ".claude.json").read_text()[:400])
    for label, env in (("default folder", {}), ("account folder a", {"CLAUDE_CONFIG_DIR": profiles[0]}), ("account folder b", {"CLAUDE_CONFIG_DIR": profiles[1]})):
        p = subprocess.run([claude, "mcp", "list"], capture_output=True, text=True, timeout=120, env=isolated(env, home), cwd=str(home))
        out = (p.stdout + p.stderr).strip()
        print(f"      claude mcp list ({label}):\n        " + out.replace("\n", "\n        "))
        check(f"claude mcp list ({label}): shellbot-gateway is Connected (no token: the status tool only)", "shellbot-gateway" in out and "Connected" in out.split("shellbot-gateway", 1)[1].split("\n", 1)[0], out)
    check("check/remove look in every folder: present, then gone", cl.check("claude", "shellbot-gateway", all_homes=True)["state"] == "present"
          and cl.remove("claude", "shellbot-gateway", all_homes=True)["state"] == "removed" and cl.check("claude", "shellbot-gateway", all_homes=True)["state"] == "absent")
    # behind a wrapper: the same gateway, pointed at the made-up upstream with a made-up token
    wrapper = TMP / "gw_fake.py"
    wrapper.write_text(f"import sys\nsys.path.insert(0, {str(HERE)!r})\nimport shellbot_mcp\nfrom connectors import secrets\n"
                       f"b = secrets.MemoryBackend(); b.set('github', 'token', {fake.FAKE_TOKEN!r})\n"
                       f"sys.exit(shellbot_mcp.main(backend=b, github_url={up.url!r}))\n")
    cl2 = clis_mod.Clis(runner=runner_for(home, log), finder=lambda c: claude, profiles=lambda c: profiles, paths={"python": real_py, "gateway": str(wrapper)})
    cl2.add("claude", MCP, all_homes=False)
    p = subprocess.run([claude, "mcp", "list"], capture_output=True, text=True, timeout=120, env=isolated({}, home), cwd=str(home))
    out = (p.stdout + p.stderr).strip()
    print("      claude mcp list (gateway behind the made-up upstream):\n        " + out.replace("\n", "\n        "))
    check("claude mcp list: Connected with the made-up upstream and token too", "Connected" in out.split("shellbot-gateway", 1)[-1].split("\n", 1)[0], out)
    check("the made-up upstream was reached by the gateway, with the bearer token and the read-only header",
          any(x["headers"].get("authorization") == f"Bearer {fake.FAKE_TOKEN}" and x["headers"].get("x-mcp-readonly") == "true" for x in up.requests), str(up.requests[:1]))
    check("the token is in none of the settings files", all(fake.FAKE_TOKEN not in f.read_text(errors="replace") for f in [home / ".claude.json", *(Path(p) / ".claude.json" for p in profiles)] if f.exists()))

# ── Codex ──────────────────────────────────────────────────────────────────
codex = find("codex")
if not codex:
    print("  [SKIP] codex is not installed")
else:
    home = TMP / "codex-home"
    home.mkdir()
    cprofiles = [str(TMP / "xprof" / "x")]
    Path(cprofiles[0]).mkdir(parents=True)
    log = []
    cl = clis_mod.Clis(runner=runner_for(home, log), finder=lambda c: codex if c == "codex" else None, profiles=lambda c: cprofiles,
                       paths={"python": real_py, "gateway": str(HERE / "shellbot_mcp.py")})
    r = cl.add("codex", MCP, all_homes=True)
    check("Clis.add(all_homes) with the real codex: added in ~/.codex (temp HOME) and the account folder", r["state"] in ("added", "replaced") and r["homes"] == 2, str(r) + str(log))
    for label, env in (("default folder", {}), ("account folder", {"CODEX_HOME": cprofiles[0]})):
        p = subprocess.run([codex, "mcp", "list"], capture_output=True, text=True, timeout=120, env=isolated(env, home), cwd=str(home))
        out = (p.stdout + p.stderr).strip()
        print(f"      codex mcp list ({label}):\n        " + out.replace("\n", "\n        "))
        check(f"codex mcp list ({label}) shows shellbot-gateway with our command", "shellbot-gateway" in out and "shellbot_mcp.py" in out and "enabled" in out.lower(), out)
    check("codex: the entry is in the config.toml of each folder", "shellbot-gateway" in (home / ".codex" / "config.toml").read_text() and "shellbot-gateway" in (Path(cprofiles[0]) / "config.toml").read_text())
    check("check/remove look in every folder for codex too", cl.check("codex", "shellbot-gateway", all_homes=True)["state"] == "present"
          and cl.remove("codex", "shellbot-gateway", all_homes=True)["state"] == "removed" and cl.check("codex", "shellbot-gateway", all_homes=True)["state"] == "absent")

up.stop()
print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
