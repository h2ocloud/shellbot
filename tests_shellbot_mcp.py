#!/usr/bin/env python3
"""shellbot_mcp.py: ShellBot's MCP gateway, against a made-up GitHub-style upstream on localhost (JSON and event-stream
answers). The key store is an in-memory one or none; no real key store, token, AI program setting or network is touched.

Proven: initialize / tools/list (prefix) / tools/call (name sent upstream without the prefix) / ping; the read-only and
toolsets headers, and the client-side drop of a write tool; no token, no key store (Unsupported), a refused token and an
unreachable upstream each leave only `shellbot_status`; an upstream error comes back as an MCP error result; stdout holds
JSON-RPC and nothing else and the token is in no output, log or message (also when the upstream repeats it); the token
is re-read after a 401; a lost session is opened again; `sfctl call`.

Run: .venv/bin/python tests_shellbot_mcp.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
import _mcp_fake_upstream as fake  # noqa: E402
import shellbot_mcp as mcp  # noqa: E402
from connectors import secrets  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def backend(token=fake.FAKE_TOKEN):
    b = secrets.MemoryBackend()
    if token:
        b.set("github", "token", token)
    return b


def gw(up, token=fake.FAKE_TOKEN, settings=None, **kw):
    return mcp.build_gateway(backend=backend(token), github_url=up.url, settings=settings, **kw)


def rpc(g, method, params=None, ident=1):
    return g.handle({"jsonrpc": "2.0", "id": ident, "method": method, **({"params": params} if params is not None else {})})


def names(res):
    return [t["name"] for t in res["result"]["tools"]]


for mode in ("json", "sse"):
    print(f"── upstream answers as {mode}")
    up = fake.Upstream(mode=mode)
    g = gw(up)
    r = rpc(g, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    check(f"{mode}: initialize answers with the client's protocol, tools capability and a server name",
          r["result"]["protocolVersion"] == "2025-06-18" and "tools" in r["result"]["capabilities"] and r["result"]["serverInfo"]["name"] == "shellbot", str(r))
    check(f"{mode}: an unknown protocol gets one the gateway knows",
          rpc(g, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] in mcp.PROTOCOLS)
    check(f"{mode}: ping", rpc(g, "ping")["result"] == {})
    check(f"{mode}: a notification gets no answer", g.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)
    r = rpc(g, "tools/list")
    check(f"{mode}: tools/list prefixes every tool with github_ and offers no status tool when all is well",
          names(r) == ["github_list_issues", "github_get_me"], str(names(r)))
    check(f"{mode}: the tool's schema and description pass through",
          r["result"]["tools"][0]["description"] == "List issues" and r["result"]["tools"][0]["inputSchema"]["properties"]["repo"] == {"type": "string"})
    init = up.requests[0]
    check(f"{mode}: the upstream got the bearer token, read-only and the default toolsets",
          init["headers"].get("authorization") == f"Bearer {fake.FAKE_TOKEN}" and init["headers"].get("x-mcp-readonly") == "true"
          and init["headers"].get("x-mcp-toolsets") == "repos,issues,pull_requests,actions", str(init["headers"]))
    check(f"{mode}: initialize is followed by notifications/initialized, and the session id comes back on later requests",
          [x["body"].get("method") for x in up.requests[:3]] == ["initialize", "notifications/initialized", "tools/list"]
          and up.requests[2]["headers"].get("mcp-session-id") == "sess-1", str([x["body"].get("method") for x in up.requests]))
    r = rpc(g, "tools/call", {"name": "github_list_issues", "arguments": {"repo": "a/b"}})
    body = json.loads(r["result"]["content"][0]["text"])
    check(f"{mode}: tools/call goes upstream under the original name, with the arguments",
          body == {"tool": "list_issues", "args": {"repo": "a/b"}} and r["result"]["isError"] is False, str(r))
    r = rpc(g, "tools/call", {"name": "github_nothing"})
    check(f"{mode}: a tool that is not offered is an invalid-params error", r.get("error", {}).get("code") == -32602, str(r))
    check(f"{mode}: an unknown method is method-not-found", rpc(g, "resources/list").get("error", {}).get("code") == -32601)
    g.close()
    up.stop()

print("── write tools")
up = fake.Upstream(honor_readonly=False)
g = gw(up)
check("read-only: a write tool the upstream sends anyway is dropped here", names(rpc(g, "tools/list")) == ["github_list_issues", "github_get_me"])
r = rpc(g, "tools/call", {"name": "github_create_issue", "arguments": {"title": "x"}})
check("read-only: calling a dropped write tool is refused, nothing is sent", "error" in r and not any(
    (x["body"] or {}).get("params", {}).get("name") == "create_issue" for x in up.requests), str(r))
g.close()
g = gw(up, settings={"readonly": False, "toolsets": "repos,issues"})
check("writing on: the write tool is offered", "github_create_issue" in names(rpc(g, "tools/list")))
last = [x for x in up.requests if x["method"] == "POST"][-1]
check("writing on: no read-only header is sent, and the toolsets from the settings are", "x-mcp-readonly" not in last["headers"] and last["headers"]["x-mcp-toolsets"] == "repos,issues", str(last["headers"]))
g.close()
up.stop()

print("── settings")
check("config.json absent: read-only and the default toolsets", mcp.github_settings({}) == {"readonly": True, "toolsets": mcp.GITHUB_TOOLSETS})
check("only an explicit false turns read-only off", mcp.github_settings({"connect": {"github": {"readonly": False}}})["readonly"] is False
      and mcp.github_settings({"connect": {"github": {"readonly": 0}}})["readonly"] is True and mcp.github_settings({"connect": {"github": {"readonly": "no"}}})["readonly"] is True)
check("a toolsets list in the settings is used; a malformed one falls back to the default",
      mcp.github_settings({"connect": {"github": {"toolsets": ["repos", "gists"]}}})["toolsets"] == "repos,gists"
      and mcp.github_settings({"connect": {"github": {"toolsets": "repos, x\r\nInjected: 1"}}})["toolsets"] == mcp.GITHUB_TOOLSETS)

print("── pagination and a lost session")
up = fake.Upstream(page_size=1, honor_readonly=False)
g = gw(up, settings={"readonly": False, "toolsets": mcp.GITHUB_TOOLSETS})
check("a paged tools/list is followed to the end", len(names(rpc(g, "tools/list"))) == 3)
up.expire_next = True
r = rpc(g, "tools/call", {"name": "github_get_me", "arguments": {}})
check("a session the server forgot (404) is opened again once and the call goes through", r["result"]["isError"] is False and up.sessions == 2, str(r) + str(up.sessions))
g.close()
up.stop()

print("── when GitHub cannot be used: only shellbot_status")
up = fake.Upstream()
for label, g, code in (("no token", gw(up, token=""), "not connected"),
                       ("a refused token", gw(up, token="github_pat_WRONG000000000000000000000000"), "did not accept"),
                       ("no key store", mcp.build_gateway(backend=secrets.UnsupportedBackend(), github_url=up.url), "not available on this computer")):
    r = rpc(g, "tools/list")
    check(f"{label}: tools/list is shellbot_status alone", names(r) == ["shellbot_status"], str(names(r)))
    r = rpc(g, "tools/call", {"name": "shellbot_status"})
    txt = r["result"]["content"][0]["text"]
    check(f"{label}: the status says why, and where to go", code in txt and ("Connect" in txt or "key store" in txt), txt)
    check(f"{label}: a github tool is not offered", "error" in rpc(g, "tools/call", {"name": "github_get_me"}))
    g.close()
g = mcp.build_gateway(backend=backend(), github_url="http://127.0.0.1:9/mcp/")
check("upstream unreachable: only shellbot_status", names(rpc(g, "tools/list")) == ["shellbot_status"])
check("upstream unreachable: the status says it could not be reached", "could not be reached" in rpc(g, "tools/call", {"name": "shellbot_status"})["result"]["content"][0]["text"])
g.close()

print("── the token saved after the gateway started is picked up")
b = secrets.MemoryBackend()
g = mcp.Gateway([mcp.GithubUpstream(backend=b, url=up.url, retry_s=0)])
check("no token yet: status only", names(rpc(g, "tools/list")) == ["shellbot_status"])
notes = []
g.notify = notes.append
b.set("github", "token", fake.FAKE_TOKEN)
rpc(g, "tools/call", {"name": "shellbot_status"})
check("after the token is saved the tools appear, and the AI is told the list changed",
      names(rpc(g, "tools/list")) == ["github_list_issues", "github_get_me"] and any(n.get("method") == "notifications/tools/list_changed" for n in notes), str(notes))
b.set("github", "token", "github_pat_ROTATED0000000000000000000000000")
up.token = "github_pat_ROTATED0000000000000000000000000"
r = rpc(g, "tools/call", {"name": "github_get_me", "arguments": {}})
check("a token replaced in the key store is read again after a 401", r["result"]["isError"] is False, str(r))
g.close()
up.stop()

print("── upstream errors come back as MCP error results")
up = fake.Upstream(honor_readonly=False, extra=True)
g = gw(up, settings={"readonly": False, "toolsets": "repos"})
rpc(g, "tools/list")
r = rpc(g, "tools/call", {"name": "github_boom"})
check("a JSON-RPC error of the upstream is an isError result, not an exception", r["result"]["isError"] is True and "upstream exploded" in r["result"]["content"][0]["text"], str(r))
r = rpc(g, "tools/call", {"name": "github_toolfail"})
check("an upstream isError result passes through as it is", r["result"] == {"content": [{"type": "text", "text": "not found"}], "isError": True}, str(r))
r = rpc(g, "tools/call", {"name": "github_crash500"})
check("an HTTP 500 is an isError result", r["result"]["isError"] is True and "500" in r["result"]["content"][0]["text"], str(r))
g.close()
up.stop()

print("── stdio: stdout is JSON-RPC only, the token is nowhere")
up = fake.Upstream()
root = tempfile.mkdtemp(prefix="mcp-test-root-")
cfgdir = tempfile.mkdtemp(prefix="mcp-test-cfg-")
env = {**os.environ, "SHELLFRAME_TESTS": "1", "SHELLFRAME_TEST_ROOT": root, "SHELLFRAME_CONFIG_DIR": cfgdir, "PYTHONUNBUFFERED": "1"}
runner = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import shellbot_mcp\n"
          f"from connectors import secrets\n"
          f"b = secrets.MemoryBackend(); b.set('github', 'token', {fake.FAKE_TOKEN!r})\n"
          f"_bg = shellbot_mcp.build_gateway\n"
          f"def bg(**k):\n    print('a stray print that must not reach the wire')\n    return _bg(**k)\n"
          f"shellbot_mcp.build_gateway = bg\n"
          f"sys.exit(shellbot_mcp.main(backend=b, github_url={up.url!r}))\n")
reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "github_list_issues", "arguments": {"repo": "x/y"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "nope"}]
stdin = "\n".join(json.dumps(r) for r in reqs) + "\nthis is not json\n"
p = subprocess.run([sys.executable, "-c", runner], input=stdin, capture_output=True, text=True, timeout=60, env=env)
lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
parsed = []
ok_json = True
for ln in lines:
    try:
        parsed.append(json.loads(ln))
    except ValueError:
        ok_json = False
check("every stdout line is JSON", ok_json and bool(lines), p.stdout[:300])
check("every stdout line is a JSON-RPC 2.0 message", all(isinstance(m, dict) and m.get("jsonrpc") == "2.0" for m in parsed))
by_id = {m.get("id"): m for m in parsed}
check("the five requests are answered (and the garbage line is a parse error)",
      all(i in by_id for i in (1, 2, 3, 4)) and any(m.get("error", {}).get("code") == -32700 for m in parsed), str(sorted(map(str, by_id))))
check("the stray print went to stderr", "a stray print" in p.stderr and "a stray print" not in p.stdout)
logs = p.stderr + "".join(open(os.path.join(root, f), encoding="utf-8", errors="replace").read() for f in os.listdir(root))
check("the token is not in stdout, stderr or the debug log", fake.FAKE_TOKEN not in p.stdout and fake.FAKE_TOKEN not in logs and "FAKE0000TOKEN" not in logs + p.stdout)
check("the process ends when stdin ends", p.returncode == 0, str(p.returncode))

up = fake.Upstream(honor_readonly=False, extra=True)
runner2 = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import shellbot_mcp\n"
           f"from connectors import secrets\n"
           f"b = secrets.MemoryBackend(); b.set('github', 'token', {fake.FAKE_TOKEN!r})\n"
           f"sys.exit(shellbot_mcp.main(backend=b, github_url={up.url!r}, settings={{'readonly': False, 'toolsets': 'repos'}}))\n")
leak = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "github_leak"}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "github_crash500"}}]
p = subprocess.run([sys.executable, "-c", runner2], input="\n".join(json.dumps(r) for r in leak) + "\n", capture_output=True, text=True, timeout=60, env=env)
logs = p.stderr + "".join(open(os.path.join(root, f), encoding="utf-8", errors="replace").read() for f in os.listdir(root))
check("an upstream that repeats the token (an answer, an error body) does not get it onto the wire or into a log",
      fake.FAKE_TOKEN not in p.stdout and fake.FAKE_TOKEN not in logs and "••••" in p.stdout and p.stdout.count("\n") == 4, p.stdout[:600])
up.stop()

print("── sfctl call (in this process, no app)")
up = fake.Upstream(honor_readonly=False)
g = gw(up)
code, out = mcp.cli_call("github", "list_issues", '{"repo": "a/b"}', gateway=g)
check("call: exit 0, the answer as JSON with the parsed data", code == 0 and out["ok"] and out["data"] == {"tool": "list_issues", "args": {"repo": "a/b"}}, str(out))
g = gw(up)
code, out = mcp.cli_call("github", "github_get_me", "", gateway=g)
check("call: the tool name may carry the prefix", code == 0 and out["ok"], str(out))
g = gw(up)
code, out = mcp.cli_call("github", "create_issue", "{}", gateway=g)
check("call: a write tool is refused while read-only", code == 2 and out["code"] == "unknown_tool", str(out))
g = gw(up)
code, out = mcp.cli_call("github", "list_issues", "not json", gateway=g)
check("call: bad --args is code 2", code == 2 and out["code"] == "bad_args")
code, out = mcp.cli_call("nothing", "x", "", gateway=gw(up))
check("call: an unknown service is code 2", code == 2 and out["code"] == "unknown_service")
code, out = mcp.cli_call("github", "", "", list_only=True, gateway=gw(up))
check("call --list: service, tool, name and description for each", code == 0 and [r["tool"] for r in out["tools"]] == ["list_issues", "get_me"] and out["tools"][0]["name"] == "github_list_issues", str(out))
code, out = mcp.cli_call("github", "list_issues", "{}", gateway=gw(up, token=""))
check("call without a token: exit 1 with the reason", code == 1 and out["code"] == "no_token" and "Connect" in out["message"], str(out))
code, out = mcp.cli_call("github", "", "", list_only=True, gateway=gw(up, token=""))
check("call --list without a token: no tools, and why", code == 1 and out["tools"] == [] and out["unavailable"] == {"github": "no_token"}, str(out))
up.stop()

# the real sfctl, with no token in a key store it cannot reach: it must say so and exit 1 (the refusing key store of a test)
p = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "call", "--list"], capture_output=True, text=True, timeout=60,
                   env={**env, "SHELLFRAME_TESTS": "1"})
try:
    j = json.loads(p.stdout)
except ValueError:
    j = {}
check("sfctl call --list is wired: prints JSON and exits 1 when nothing is connected", p.returncode == 1 and j.get("tools") == [] and "github" in j.get("unavailable", {}), p.stdout[:300] + p.stderr[:300])
p = subprocess.run([sys.executable, str(HERE / "sfctl.py"), "call", "github"], capture_output=True, text=True, timeout=60, env=env)
check("sfctl call github (no tool) is refused", p.returncode == 2, p.stdout + p.stderr)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
