#!/usr/bin/env python3
"""The office for the phone app over Frame Link (link_office.py: POST /link/office/api, GET /link/office/asset, the
`office` field of GET /link/ping, and the desktop's `office_tabs` call).

Two in-process computers over a real signed link; the desktop's calls are stubs (nothing is written to the real state
or config folder, no pairing key but the test's own):
  1. /link/office/api: every call in the table answers {success, result}; what the page must not get is cleaned
     (a path on this computer, a schedule's command, a member's name); the ops calls go through link_ops
  2. what is refused: a call off the table is 403 (`readonly` for the ones that write, `no_method` for the rest),
     wrong arguments 400 `bad_args`, a call that raises is `error`, one that takes too long `timeout`, an answer over
     the cap `too_big`
  3. who may ask: unsigned, a bad signature, an unknown peer and a one-way pairing are 403 (the route, the asset and
     nothing is called); the office off is `code: "off"` (the api answers 200 with it, the asset 404)
  4. /link/office/asset: the files in the table only (exact names: `..`, absolute paths, sub folders, other case, a
     file that is not in the table, a file of the table that is not there: 404); the ETag is the file's sha256, quoted;
     If-None-Match (quoted, bare, W/, a list, *) gives 304 with the same ETag and signature; both signatures check
  5. /link/ping: office {bridge, assets, enabled}: `assets` changes with a file, `enabled` is the office and the bots
  6. office_tabs: the shape the page's pageTabs() needs, from the status monitor's reading, with fake tabs
Demo data only. Run: .venv/bin/python tests_link_office.py
"""
import hashlib
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="sf-link-office-"))
os.environ["SHELLFRAME_TESTS"] = "1"
os.environ["SHELLFRAME_CONFIG_DIR"] = str(TMP / "config")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules.setdefault("webview", MagicMock())
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import api_office  # noqa: E402
import bridge_home  # noqa: E402
import frame_link  # noqa: E402
import link_office  # noqa: E402
import office_ops as O  # noqa: E402
import sf_log  # noqa: E402

sf_log.DEBUG_LOG = str(TMP / "debug.log")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ── the desktop's side: stubs for the calls, the real office_tabs and _link_office ──────────────────────
class Host(api_office.OfficeApiMixin):
    def __init__(self):
        self.called = []
        self.office = True
        self.ops = True
        self.sessions, self.order, self.roles, self.snaps = {}, [], {}, {}

    def _office_on(self):
        return self.office

    def _ops_sync(self):
        return self.ops

    def _rec(self, name, *a):
        self.called.append((name,) + a)

    def office_prefs(self):
        self._rec("office_prefs")
        return json.dumps({"success": True, "prefs": {"seats": {}}})

    def bots_state(self):
        self._rec("bots_state")
        return json.dumps({"enabled": True, "roster": {"a": {"label": "A"}}, "sessions": {}, "kinds": {}, "primary": "a",
                           "memory_root": "/Users/someone/memory"})

    def office_sky(self, tz="", want_weather=True):
        self._rec("office_sky", tz, want_weather)
        return json.dumps({"success": True, "place": None, "weather": None, "weather_on": True, "credit": "MET Norway"})

    def tasks_overview(self):
        self._rec("tasks_overview")
        return json.dumps({"success": True, "decisions": [], "stuck": [], "open": [], "deliverables": {"new": 0}})

    def task_detail(self, task_id):
        self._rec("task_detail", task_id)
        return json.dumps({"success": True, "task": {"id": task_id, "events": []}})

    def schedules_list(self):
        self._rec("schedules_list")
        return json.dumps({"schedules": [{"id": "x.daily", "title": "Daily", "when": {"en": "daily", "zh": "每天"}, "enabled": True,
                                          "running": False, "kind": "once", "members": [{"label": "m1", "command": "/bin/secret-thing"},
                                                                                       {"label": "m2", "command": "/bin/other"}],
                                          "owner": "me", "toggle": True, "state": "ok", "slots": [], "command": "/bin/run-it",
                                          "plist": "/Users/someone/Library/LaunchAgents/x.plist"}],
                           "unsupported": False, "configured": True, "launchd_ok": True, "error": "/Users/someone/boom"})

    def link_peer_bots(self):
        self._rec("link_peer_bots")
        return json.dumps({"peers": {}, "pending": 0})

    def link_peer_tasks(self):
        self._rec("link_peer_tasks")
        return json.dumps({"peers": {}, "pending": 0})

    def office_objects(self, lang=""):
        self._rec("office_objects", lang)
        return json.dumps({"success": True, "objects": []})

    def office_object_list(self, obj_id, args_json="{}", lang=""):
        self._rec("office_object_list", obj_id, args_json, lang)
        return json.dumps({"groups": [], "items": [], "total": 0})

    def office_object_item(self, obj_id, item_id, lang=""):
        self._rec("office_object_item", obj_id, item_id, lang)
        return json.dumps({"success": True, "item": {"id": item_id}})

    def office_helpers(self):
        return json.dumps({"success": True, "now": 1.0, "helpers": {"t1": [{"id": "h1", "description": "d", "state": "running"}]}})

    # the tabs' side
    def _live_sids(self):
        return [s for s, x in self.sessions.items() if getattr(x, "alive", True)]

    def _ordered_sids(self, cfg=None):
        return [s for s in self.order if s in self.sessions] + [s for s in self.sessions if s not in self.order]

    def _bots_role_of(self, sid, cfg=None):
        return self.roles.get(sid, "")

    def _agent_status_snapshot(self, sid):
        got = self.snaps.get(sid)
        return (got, 0.1) if got is not None else None

    @staticmethod
    def _tab_cli(s):
        import agent_status
        kind = agent_status.worker_kind(str(getattr(s, "cmd", "") or ""))
        return "" if kind == "other" else kind


class Node:
    def __init__(self, name):
        self.tmp = tempfile.mkdtemp(prefix=f"sf-link-office-{name}-", dir=TMP)
        self.port = free_port()
        self.host = Host()
        self.config = {"settings": {"experimental_office": True, "experimental_bots": True},
                       "frame_link": {"enabled": True, "listen_host": "127.0.0.1", "listen_port": self.port,
                                      "frame_name": name, "frame_id": uuid.uuid4().hex, "peers": {}}}

        def execute(cmd, args):
            if cmd == "office_api":
                return self.host._link_office(args)
            return {"success": False, "message": f"unknown {cmd}"}

        def update_config(mutator):
            mutator(self.config)
            return self.config

        self.link = frame_link.FrameLink(get_config=lambda: self.config, update_config=update_config, execute_fn=execute,
                                         log=lambda m: None, version="test", state_dir=os.path.join(self.tmp, "state"),
                                         downloads_dir=os.path.join(self.tmp, "dl"))

    @property
    def fid(self):
        return self.config["frame_link"]["frame_id"]


class Got:
    def __init__(self, status, headers, body, nonce, peer):
        self.status, self.headers, self.body, self.nonce, self.peer = status, headers, body, nonce, peer

    def json(self):
        return json.loads(self.body.decode() or "{}")

    def signed_over(self, data: bytes) -> bool:
        """The response's X-SF-Sign is the answer's signature over `data` (the body for JSON, the file's hash for a file)."""
        want = frame_link._hmac_hex(bytes.fromhex(self.peer["secret"]), f"resp\n{self.nonce}\n{sha(data)}".encode())
        return self.headers.get("X-SF-Sign") == want


def ask(node_from, peer, method, path, body=b"", sig=None, who=None, extra=None):
    ts, nonce = str(time.time()), os.urandom(16).hex()
    if sig is None:
        sig = frame_link._hmac_hex(bytes.fromhex(peer["secret"]), frame_link.FrameLink._string_to_sign(
            method, path, ts, nonce, frame_link._sha256_hex(body)))
    headers = {} if who == "none" else {"X-SF-Peer": who or node_from.fid, "X-SF-Ts": ts, "X-SF-Nonce": nonce, "X-SF-Sign": sig}
    headers.update(extra or {})
    req = urllib.request.Request(f"http://{peer['host']}:{peer['port']}{path}", data=body if method == "POST" else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return Got(r.status, dict(r.headers), r.read(), nonce, peer)
    except urllib.error.HTTPError as e:
        return Got(e.code, dict(e.headers), e.read(), nonce, peer)


def api(node_from, peer, method, args=None, lang="en", **kw):
    body = json.dumps({"method": method, "args": [] if args is None else args, "lang": lang}).encode()
    return ask(node_from, peer, "POST", "/link/office/api", body, **kw)


def asset(node_from, peer, name, **kw):
    return ask(node_from, peer, "GET", "/link/office/asset?path=" + urllib.parse.quote(name, safe=""), **kw)


# ── a web folder of our own for the asset checks (the real one is used once, at the end) ──────────
WEB = TMP / "web"
(WEB / "sub").mkdir(parents=True)
FILES = {"office.html": b"<html>office</html>", "icons.js": b"var a = 1;\n", "office_core.js": b"/* core */\n" * 50,
         "icons.css": b"b{}"}
for n, d in FILES.items():
    (WEB / n).write_bytes(d)
(WEB / "secret.txt").write_bytes(b"not for the phone")
(WEB / "sub" / "office.html").write_bytes(b"nested")
(TMP / "outside.js").write_bytes(b"outside")
REAL_WEB = link_office.WEB
link_office.WEB = WEB

a, b = Node("desk-a"), Node("desk-b")
coll = MagicMock()
dv = {"success": True, "title": "t", "banner": {"level": "ok", "text": "fine"}, "sections": [
    {"id": "rules", "title": "r", "widgets": [{"kind": "rules", "readonly": False, "items": [{"id": "a", "label": "A", "on": True, "value": 1}]}]}]}
coll.view = lambda lang, sort: dv
O.set_collector(coll)
try:
    a.link.start()
    b.link.start()
    pair = a.link.pairing_begin()
    check("A and B pair", b.link.join("127.0.0.1", a.port, pair["code"]).get("success") is True)
    pa = b.link.peers()[a.fid]
    ha = a.host

    print("1. /link/office/api: the table")
    expect = {"office_prefs": [], "bots_state": [], "office_tabs": [], "tasks_overview": [], "task_detail": [12],
              "schedules_list": [], "link_peer_bots": [], "link_peer_tasks": [], "office_objects": [], "office_sky": [],
              "office_object_list": ["deliverables", "{}"], "office_object_item": ["deliverables", "d-1"],
              "office_ops_view": ["cpu"], "office_ops_live": [True]}
    check("the table is exactly the fourteen calls", set(expect) == set(link_office.METHODS), sorted(set(expect) ^ set(link_office.METHODS)))
    ha.sessions = {}
    for m, args in expect.items():
        r = api(b, pa, m, args, lang="zh")
        j = r.json()
        check(f"{m}: 200 {{success, result}}, signed", r.status == 200 and j.get("success") is True and isinstance(j.get("result"), dict)
              and r.signed_over(r.body) and "_http" not in j, (r.status, j))
    check("the language reached the calls that take it", ("office_objects", "zh") in ha.called and ("office_object_list", "deliverables", "{}", "zh") in ha.called
          and ("office_object_item", "deliverables", "d-1", "zh") in ha.called, ha.called)
    check("office_sky is asked with no zone (the phone cannot pass one) and the weather on", ("office_sky", "", True) in ha.called, ha.called)
    check("task_detail got a number (and '#12' too)", ("task_detail", 12) in ha.called and api(b, pa, "task_detail", ["#7"]).json()["success"]
          and ("task_detail", 7) in ha.called)
    bs = api(b, pa, "bots_state").json()["result"]
    check("bots_state: the path of the memory folder is gone, the roster stays", "memory_root" not in bs and bs["roster"]["a"]["label"] == "A", bs)
    sc = api(b, pa, "schedules_list").json()["result"]
    row = sc["schedules"][0]
    check("schedules_list: a member is only counted, no command, no plist, no error text",
          row["members"] == [{}, {}] and "command" not in row and "plist" not in row and sc["error"] == "error"
          and row["id"] == "x.daily" and row["when"]["zh"] == "每天" and "/Users" not in json.dumps(sc), sc)
    ov = api(b, pa, "office_ops_view", ["cpu"]).json()["result"]
    check("office_ops_view: the rules widget is readonly", ov["success"] and ov["sections"][0]["widgets"][0]["readonly"] is True, ov)
    api(b, pa, "office_ops_live", [True])
    check("office_ops_live: asked on this computer's collector, as this peer, apart from its own",
          coll.live.call_args.args == (True,) and coll.live.call_args.kwargs.get("who") == "peer:" + b.fid, coll.live.call_args)
    ha.ops = False
    r = api(b, pa, "office_ops_view", ["cpu"]).json()
    check("ops off: the call goes through and says code off", r["success"] and r["result"]["code"] == "off", r)
    ha.ops = True

    print("2. what is refused")
    ha.called.clear()
    for m in sorted(link_office.WRITE_METHODS):
        r = api(b, pa, m, [])
        check(f"{m}: 403 readonly", r.status == 403 and r.json()["code"] == "readonly" and r.json()["success"] is False and "_http" not in r.json())
    for m in ("nope", "__class__", "office_prefs ", "OFFICE_PREFS", "../office_prefs", "", "_office_on", "office_tabs\n"):
        r = api(b, pa, m, [])
        check(f"{m!r}: 403 no_method", r.status == 403 and r.json()["code"] == "no_method", (r.status, r.json()))
    body = json.dumps({"method": ["office_prefs"], "args": []}).encode()
    check("a method that is not a string: 403", ask(b, pa, "POST", "/link/office/api", body).status == 403)
    check("no method at all: 403", ask(b, pa, "POST", "/link/office/api", b"{}").status == 403)
    check("…nothing was called", ha.called == [], ha.called)
    for name, m, args in (("an argument for a call that takes none", "office_prefs", [1]), ("task number text", "task_detail", ["abc"]),
                          ("task number negative", "task_detail", [-1]), ("task number a bool", "task_detail", [True]),
                          ("task number too long", "task_detail", [10 ** 12]), ("object id with a slash", "office_object_list", ["../x", "{}"]),
                          ("object id upper case", "office_object_item", ["Deliverables", "d"]), ("item id empty", "office_object_item", ["deliverables", ""]),
                          ("item id too long", "office_object_item", ["deliverables", "x" * 5000]), ("json too long", "office_object_list", ["deliverables", "x" * 5000]),
                          ("one argument too few", "office_object_item", ["deliverables"]), ("flag not a bool", "office_ops_live", ["true"]),
                          ("sort not a string", "office_ops_view", [3]), ("args a string", "task_detail", "12"), ("args an object", "task_detail", {"id": 1})):
        r = api(b, pa, m, args)
        check(f"bad_args: {name}: 400", r.status == 400 and r.json()["code"] == "bad_args", (r.status, r.json()))
    check("…and still nothing was called", ha.called == [], ha.called)
    ha.office_prefs = lambda: (_ for _ in ()).throw(RuntimeError("boom /Users/someone"))
    r = api(b, pa, "office_prefs")
    check("a call that raises: code error, the text of the error does not leave", r.status == 200 and r.json()["code"] == "error"
          and "boom" not in r.body.decode() and "/Users" not in r.body.decode(), r.body)
    slow_for = link_office.TIMEOUT_S
    link_office.TIMEOUT_S = 0.3
    ha.office_prefs = lambda: time.sleep(1.5) or "{}"
    t0 = time.time()
    r = api(b, pa, "office_prefs")
    check("a call that takes too long: code timeout, at the time limit", r.json()["code"] == "timeout" and time.time() - t0 < 1.2, (r.json(), time.time() - t0))
    link_office.TIMEOUT_S = slow_for
    ha.office_prefs = lambda: json.dumps({"success": True, "blob": "x" * (link_office.MAX_BODY + 10)})
    check("an answer over the cap: code too_big", api(b, pa, "office_prefs").json()["code"] == "too_big")
    ha.office_prefs = lambda: "not json"
    check("an answer that is not JSON: code error", api(b, pa, "office_prefs").json()["code"] == "error")
    del ha.office_prefs

    print("3. who may ask")
    ha.called.clear()
    for label, kw in (("unsigned", {"who": "none"}), ("a bad signature", {"sig": "0" * 64}), ("an unknown peer", {"who": "f" * 32})):
        r = api(b, pa, "office_prefs", **kw)
        r2 = asset(b, pa, "office.html", **kw)
        check(f"{label}: 403 on the api and the asset, nothing called", r.status == 403 and r2.status == 403 and ha.called == [], (r.status, r2.status))
    a.config["frame_link"]["peers"][b.fid]["mode"] = "master"
    r, r2 = api(b, pa, "office_prefs"), asset(b, pa, "office.html")
    check("one-way pairing: 403 on the api and on the asset, nothing called", r.status == 403 and r2.status == 403 and ha.called == [], (r.status, r2.status))
    ping = ask(b, pa, "GET", "/link/ping")
    check("…the ping still answers (the app looks at it first)", ping.status == 200 and "office" in ping.json())
    a.config["frame_link"]["peers"][b.fid]["mode"] = "duplex"
    check("back to duplex: answered", api(b, pa, "office_prefs").json()["success"] is True)
    a.config["settings"]["experimental_office"] = False
    ha.office = False
    r = api(b, pa, "office_prefs")
    check("the office off: the api says code off (200), a call off the table is still 403",
          r.status == 200 and r.json()["code"] == "off" and r.json()["success"] is False and api(b, pa, "task_user_act").status == 403, (r.status, r.json()))
    r = asset(b, pa, "office.html")
    check("the office off: the asset is 404 with code off", r.status == 404 and r.json()["code"] == "off", (r.status, r.json()))
    ping = ask(b, pa, "GET", "/link/ping").json()["office"]
    check("…and the ping says enabled false", ping["enabled"] is False and ping["bridge"] == 1, ping)
    check("office_tabs (the desktop's own call) says off too", json.loads(ha.office_tabs())["code"] == "off")
    a.config["settings"]["experimental_office"] = True
    ha.office = True

    print("4. /link/office/asset")
    for name, data in FILES.items():
        r = asset(b, pa, name)
        etag = f'"{sha(data)}"'
        check(f"{name}: 200, the bytes, the ETag is the sha256 quoted", r.status == 200 and r.body == data and r.headers.get("ETag") == etag, (r.status, r.headers.get("ETag")))
        check(f"{name}: X-SF-Body-Sha256 and the signature over the sha256 hex", r.headers.get("X-SF-Body-Sha256") == sha(data)
              and r.headers.get("X-SF-Sign") == frame_link._hmac_hex(bytes.fromhex(pa["secret"]), f"resp\n{r.nonce}\n{sha(sha(data).encode())}".encode()))
    check("Content-Type by name", asset(b, pa, "office.html").headers["Content-Type"] == "text/html; charset=utf-8"
          and asset(b, pa, "icons.js").headers["Content-Type"] == "text/javascript; charset=utf-8"
          and asset(b, pa, "icons.css").headers["Content-Type"] == "text/css; charset=utf-8")
    d = FILES["icons.js"]
    for label, inm in (("quoted", f'"{sha(d)}"'), ("bare", sha(d)), ("weak", f'W/"{sha(d)}"'), ("in a list", f'"abc", "{sha(d)}"'), ("star", "*")):
        r = asset(b, pa, "icons.js", extra={"If-None-Match": inm})
        check(f"If-None-Match {label}: 304, no body, the same ETag, the signature over the same hash",
              r.status == 304 and r.body == b"" and r.headers.get("ETag") == f'"{sha(d)}"' and r.headers.get("X-SF-Body-Sha256") == sha(d)
              and r.headers.get("X-SF-Sign") == frame_link._hmac_hex(bytes.fromhex(pa["secret"]), f"resp\n{r.nonce}\n{sha(sha(d).encode())}".encode()), (r.status, r.headers))
    r = asset(b, pa, "icons.js", extra={"If-None-Match": '"0000"'})
    check("If-None-Match that does not match: 200 with the bytes", r.status == 200 and r.body == d)
    for name in ("secret.txt", "../outside.js", "..", "../web/office.html", "/etc/passwd", str(WEB / "office.html"), "sub/office.html",
                 "./office.html", "office.html/", "OFFICE.HTML", "office.html ", "office.html\x00.js", "%2e%2e/outside.js", "", "web/office.html",
                 "office.js", "main.py", "bots_kit.js", "office_app_host.js", "chat_rules.js"):
        r = asset(b, pa, name)
        check(f"{name!r}: 404", r.status == 404 and r.body and r.json()["success"] is False, (r.status, r.body[:60]))
    r = ask(b, pa, "GET", "/link/office/asset")
    check("no path: 404", r.status == 404)
    r = ask(b, pa, "GET", "/link/office/asset?path=icons.js&path=secret.txt")
    check("two paths: the first one counts", r.status == 200 and r.body == FILES["icons.js"])
    check("the table is the thirteen files of the spec", sorted(link_office.ASSETS) == sorted(
        ["office.html", "icons.js", "icons.css", "deliv_draw.js", "office_objects.js", "office_objects.css", "office_arrange.js", "office_ops.js",
         "office_ops.css", "office_core.js", "chat_rules.js", "bots_kit.js", "office_app_host.js"]))

    print("5. /link/ping")
    p1 = ask(b, pa, "GET", "/link/ping").json()
    o1 = p1["office"]
    check("office: bridge 1, a 12 character hash, enabled; no `mode`; the version of the link is not touched",
          o1["bridge"] == 1 and len(o1["assets"]) == 12 and o1["enabled"] is True and set(o1) == {"bridge", "assets", "enabled"} and p1["success"] is True, o1)
    check("…the same files, the same hash", ask(b, pa, "GET", "/link/ping").json()["office"]["assets"] == o1["assets"])
    (WEB / "icons.js").write_bytes(b"var a = 2;\n")
    time.sleep(0.01)
    o2 = ask(b, pa, "GET", "/link/ping").json()["office"]
    check("a file changed: the hash changed (and the asset is the new file)", o2["assets"] != o1["assets"] and asset(b, pa, "icons.js").body == b"var a = 2;\n")
    (WEB / "icons.js").write_bytes(b"var a = 1;\n")
    check("…put back: the first hash again", ask(b, pa, "GET", "/link/ping").json()["office"]["assets"] == o1["assets"])
    (WEB / "bots_kit.js").write_bytes(b"var kit = 1;")
    o3 = ask(b, pa, "GET", "/link/ping").json()["office"]["assets"]
    check("a file of the table that was not there yet is counted once it is, and served", o3 != o1["assets"] and asset(b, pa, "bots_kit.js").body == b"var kit = 1;")
    (WEB / "secret.txt").write_bytes(b"changed")
    check("a file that is not in the table does not count", ask(b, pa, "GET", "/link/ping").json()["office"]["assets"] == o3)
    a.config["settings"]["experimental_bots"] = False
    check("the bots off: enabled false (the office on)", ask(b, pa, "GET", "/link/ping").json()["office"]["enabled"] is False)
    a.config["settings"]["experimental_bots"] = True
    check("the ping of a computer is still signed and still has frame_id, name, version", p1["frame_id"] == a.fid and p1["version"] == "test")
finally:
    a.link.stop()
    b.link.stop()

print("6. office_tabs")


class S:
    def __init__(self, cmd, label=None, alive=True, source="", spawn=1_800_000_000.0):
        self.cmd, self._custom_label, self.alive, self._lifecycle_source, self._spawn_ts = cmd, label, alive, source, spawn


h = Host()
h.sessions = {"t1": S("cd /work/project && ANTHROPIC_API_KEY=sk-secret-value claude --model x", "Alice"),
              "t2": S("sf-codex", None), "t3": S("bash"), "t4": S("claude", "probe", source="connect"), "t5": S("claude", "gone", alive=False),
              "t6": S("/usr/local/bin/opencode --flag", "Review")}
h.order = ["t3", "t1", "t6", "t2", "t5", "t4"]
h.roles = {"t1": "alice"}
h.snaps = {"t1": {"state": "working", "action": "Editing the `plan`", "narration": "I will edit the plan", "loop": {
               "kind": "loop", "status": "sleeping", "next_ts": 1_800_000_100, "reason": "wait", "rounds": 2, "delay": 60,
               "crons": [{"desc": "d", "schedule": "* * * * *", "secret": "no"}], "transcript": "/Users/someone/t.jsonl"}},
           "t2": {"state": "idle", "narration": "Done with **the** parts", "task": "ignored"},
           "t3": {"state": "idle"},
           "t6": {"state": "stuck", "narration": "x" * 600}}
cfg = {"settings": {"experimental_bots": True}, "agent_roster": {"alice": {"responsibility": "Plans the work"}}}
now = 1_800_000_050.0
out = link_office.tabs(h, cfg, now)
tabs = out["tabs"]
by = {t["sid"]: t for t in tabs}
check("success, now, bots_on, tabs, helpers", out["success"] is True and out["now"] == now and out["bots_on"] is True and isinstance(tabs, list) and "t1" in out["helpers"], out.keys())
check("the sidebar's order, the dead tab and the connection's tab left out", [t["sid"] for t in tabs] == ["t3", "t1", "t6", "t2"], [t["sid"] for t in tabs])
check("every tab has the page's fields", all(set(t) == {"sid", "label", "cmd", "cli", "role", "kind", "state", "line", "narration", "loop", "opened_at"} for t in tabs), tabs)
t1 = by["t1"]
check("a bot's tab: role, kind bot, state working, line is the action (marks stripped), the CLI", t1["role"] == "alice" and t1["kind"] == "bot" and t1["state"] == "working"
      and t1["line"] == "Editing the plan" and t1["label"] == "Alice" and t1["cli"] == "claude" and t1["opened_at"] == 1_800_000_000.0, t1)
check("`cmd` is the program only: no path, variable or argument", t1["cmd"] == "claude" and "secret" not in json.dumps(tabs) and by["t6"]["cmd"] == "opencode"
      and by["t2"]["cmd"] == "sf-codex" and by["t3"]["cmd"] == "bash", [t["cmd"] for t in tabs])
check("the loop is cleaned to the page's fields (no transcript path, no extra keys)", t1["loop"] == {"kind": "loop", "status": "sleeping", "next_ts": 1_800_000_100, "delay": 60, "rounds": 2,
      "reason": "wait", "crons": [{"desc": "d", "schedule": "* * * * *"}]} and by["t2"]["loop"] is None, t1["loop"])
check("a tab with a CLI and no bot is temp; the label falls back to the program", by["t2"]["kind"] == "temp" and by["t2"]["label"] == "sf-codex" and by["t2"]["role"] == "")
check("a terminal is terminal and idle, its line empty", by["t3"]["kind"] == "terminal" and by["t3"]["state"] == "idle" and by["t3"]["line"] == "" and by["t3"]["cli"] == "")
check("not working: the line is the narration, marks stripped", by["t2"]["line"] == "Done with the parts" and by["t2"]["narration"] == "Done with **the** parts", by["t2"])
check("stuck stays stuck; the line is cut to 140 and the narration to 400", by["t6"]["state"] == "stuck" and len(by["t6"]["line"]) <= 140 and len(by["t6"]["narration"]) <= 400, by["t6"])
h.snaps["t3"] = {"state": "idle", "blocked": "a menu is open"}
check("a tab waiting on a menu is decision", {t["sid"]: t for t in link_office.tabs(h, cfg, now)["tabs"]}["t3"]["state"] == "decision")
h.snaps["t3"] = {"state": "idle", "prompt_at": 10.0}
with bridge_home._lock:
    bridge_home._red["t3"] = {"reason": "which?", "options": [(1, "a")], "at": 20.0}
try:
    check("…or on a RED question not answered since", {t["sid"]: t for t in link_office.tabs(h, cfg, now)["tabs"]}["t3"]["state"] == "decision")
    h.snaps["t3"] = {"state": "idle", "prompt_at": 30.0}
    check("…not once a prompt went in after it", {t["sid"]: t for t in link_office.tabs(h, cfg, now)["tabs"]}["t3"]["state"] == "idle")
    h.snaps["t1"]["state"] = "working"
    h.snaps["t1"]["blocked"] = "x"
    check("a working tab stays working", {t["sid"]: t for t in link_office.tabs(h, cfg, now)["tabs"]}["t1"]["state"] == "working")
finally:
    bridge_home.forget("t3")
del h.snaps["t3"]
check("a tab the monitor has not read yet is idle, not an error", {t["sid"]: t for t in link_office.tabs(h, cfg, now)["tabs"]}["t3"]["state"] == "idle")
off_cfg = {"settings": {}, "agent_roster": cfg["agent_roster"]}
roles_called = []
h._bots_role_of = lambda sid, c=None: roles_called.append(sid) or "alice"
out = link_office.tabs(h, off_cfg, now)
by = {t["sid"]: t for t in out["tabs"]}
check("the bots off: every role empty, a tab with a CLI is temp (as the desktop's office draws it), no role asked",
      out["bots_on"] is False and all(t["role"] == "" for t in out["tabs"]) and by["t1"]["kind"] == "temp" and by["t3"]["kind"] == "terminal" and roles_called == [], out)
check("…and a tab with no bot has no duty as its line", by["t1"]["line"] == "Editing the plan")
h2 = Host()
h2.sessions = {"t1": S("claude")}
h2.snaps = {"t1": {"state": "idle"}}
h2.roles = {"t1": "alice"}
api_office.main = SimpleNamespace(load_config=lambda: dict(cfg, settings={"experimental_bots": True, "experimental_office": True}))
js = json.loads(h2.office_tabs())
check("the js_api method: a JSON string of the same, with the duty as a quiet bot's line", js["success"] and js["tabs"][0]["line"] == "Plans the work" and js["tabs"][0]["kind"] == "bot", js)
h2.office = False
check("…off while the office is off", json.loads(h2.office_tabs())["code"] == "off")
check("whole thing is JSON-able and small", len(json.dumps(out)) < 20000)

print("7. the files that are really there")
link_office.WEB = REAL_WEB
real = {n for n in link_office.ASSETS if (REAL_WEB / n).is_file()}
check("the table's files that exist in web/ are read (office.html at least)", "office.html" in real and len(link_office.assets_hash()) == 12, real)
check("a hash over web/ is the same twice", link_office.assets_hash() == link_office.assets_hash())

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
