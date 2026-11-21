#!/usr/bin/env python3
"""The「新增 session」dialog for a paired computer shows that computer's own presets.

  - link_presets.public_presets: name + command only, the default first, a secret-looking command
    left out, junk ignored, capped
  - GET /link/presets end to end between two FrameLink instances (the same signed route as /link/new);
    a one-way pairing gets nothing; an older peer (no route) is a failure the dialog falls back on,
    and does not mark the peer offline
  - the wiring: api_remote answers presets_link from the config, api_link exposes link_remote_presets,
    the page loads web/remote_presets.js and draws the dialog's buttons from it

Run: .venv/bin/python tests_link_presets.py
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import frame_link  # noqa: E402
import link_presets  # noqa: E402
import tests_frame_link as tfl  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond or not detail else f"  ({detail})"))
    if not cond:
        FAILED.append(name)


PRESETS = [
    {"name": "Bash", "cmd": "bash", "icon": "▶"},
    {"name": "Claude", "cmd": "claude --permission-mode bypassPermissions --dangerously-skip-permissions", "icon": "c"},
    {"name": "Agent X", "cmd": "agent-x --flag", "icon": "x", "account": "someone"},
    {"name": "Leaky", "cmd": "EXAMPLE_API_KEY=abc123 tool"},
    {"name": "Leaky2", "cmd": "tool --token abc"},
    {"name": "Leaky3", "cmd": "curl -H 'Authorization: Bearer abc'"},
    {"name": "", "cmd": "nameless"},
    {"name": "Empty", "cmd": "  "},
    "junk",
    {"name": "Bash", "cmd": "bash --again"},
]


def unit():
    print("public_presets")
    r = link_presets.public_presets({"presets": PRESETS})
    names = [p["name"] for p in r["presets"]]
    check("ordinary presets pass, in their order", names == ["Bash", "Claude", "Agent X"], names)
    check("only name and command leave", all(set(p) == {"name", "cmd"} for p in r["presets"]))
    check("a secret-looking command is left out", not any(n.startswith("Leaky") for n in names))
    check("a flag that only looks like a word (skip-permissions) is kept", "Claude" in names)
    check("no default configured: none", r["default"] == "" and r["success"] is True)
    r = link_presets.public_presets({"presets": PRESETS, "default_preset": "Agent X"})
    check("the default comes first", [p["name"] for p in r["presets"]][0] == "Agent X" and r["default"] == "Agent X")
    r = link_presets.public_presets({"presets": PRESETS, "default_preset": "Nope"})
    check("a default that names no preset is dropped", r["default"] == "" and r["presets"][0]["name"] == "Bash")
    r = link_presets.public_presets({"presets": PRESETS, "default_preset": "Leaky"})
    check("a default naming a left-out preset is dropped", r["default"] == "")
    many = [{"name": f"p{i}", "cmd": f"c{i}"} for i in range(100)]
    check("the list is capped", len(link_presets.public_presets({"presets": many})["presets"]) == link_presets.MAX_PRESETS)
    check("no config at all: an empty list", link_presets.public_presets(None)["presets"] == [])
    r = link_presets.public_presets({"presets": [{"name": "a\x1bb" + "n" * 99, "cmd": "x\ny"}]})
    p = r["presets"][0]
    check("control characters stripped, lengths capped", "\x1b" not in p["name"] and "\n" not in p["cmd"]
          and len(p["name"]) <= link_presets.MAX_NAME)


def wire(node):
    orig = node.link._execute

    def execute(cmd, args):
        if cmd == "presets_link":
            return link_presets.public_presets(node.config)
        return orig(cmd, args)
    node.link._execute = execute


def end_to_end():
    print("GET /link/presets")
    a, b = tfl.Node("frameA"), tfl.Node("frameB")
    try:
        a.link.start(); b.link.start()
        pair = a.link.pairing_begin()
        check("paired", b.link.join("127.0.0.1", a.port, pair["code"]).get("success") is True)
        a.config["presets"] = PRESETS
        a.config["default_preset"] = "Agent X"
        res = link_presets.remote_presets(b.link, a.fid)
        check("old peer before wiring (no presets_link): a failure, not a crash", res.get("success") is False)
        check("…and the peer is not marked offline for it",
              (b.link._peer_status.get(a.fid) or {}).get("reachable") is not False)
        wire(a)
        res = link_presets.remote_presets(b.link, a.fid)
        check("the other computer's presets arrive", res.get("success") is True
              and [p["name"] for p in res["presets"]] == ["Agent X", "Bash", "Claude"], res)
        check("with its default", res.get("default") == "Agent X")
        check("nothing beyond name and command", all(set(p) == {"name", "cmd"} for p in res["presets"]))
        st, _ = tfl.signed_get(b, b.link.peers()[a.fid], "/link/presets", sig="00" * 32)
        check("a bad signature gets 403", st == 403)
        a.link._update_config(lambda c: c["frame_link"]["peers"][b.fid].__setitem__("mode", "master"))
        res = link_presets.remote_presets(b.link, a.fid)
        check("a one-way pairing (they may not control this one) gets nothing", res.get("success") is False)
        check("unknown peer: a failure", link_presets.remote_presets(b.link, "0" * 32).get("success") is False)
    finally:
        a.cleanup(); b.cleanup()


def wiring():
    print("wiring")
    src = (HERE / "api_remote.py").read_text(encoding="utf-8")
    check("api_remote answers presets_link from the config",
          'cmd == "presets_link"' in src and "link_presets.public_presets(main.load_config())" in src)
    src = (HERE / "api_link.py").read_text(encoding="utf-8")
    check("api_link exposes link_remote_presets", "def link_remote_presets" in src)
    src = (HERE / "frame_link.py").read_text(encoding="utf-8")
    check("frame_link routes /link/presets through the control gate", '"/link/presets": "presets_link"' in src)
    html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    check("the page loads web/remote_presets.js", '<script src="remote_presets.js"></script>' in html)
    body = html[html.index("function newRemoteSession("):html.index("function confirmKillRemote(")]
    check("the dialog asks for the presets and draws from them",
          "link_remote_presets(peer.id)" in body and "SFRemotePresets.buttons(" in body)
    check("the custom-command field stays", 'id="rn-cmd"' in body)
    check("no fixed button list left in the dialog", 'data-cmd="claude"' not in body)


if __name__ == "__main__":
    unit()
    end_to_end()
    wiring()
    print("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED")
    sys.exit(1 if FAILED else 0)
