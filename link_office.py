"""Frame Link's read-only view of the office, for the phone app (the office page runs inside the app; this computer
answers what it asks). Three signed routes and one ping field, all of them here so frame_link.py (at its line budget)
only lists the paths:

  POST /link/office/api {method, args, lang}   one of the read-only desktop calls the office page makes, by name
                                               (METHODS: a fixed table, never a name looked up on the Api object). The
                                               answer is {success: true, result: <what that call returns>}, or
                                               {success: false, code}: `no_method` / `readonly` (403: not in the table;
                                               `readonly` for the calls that write), `bad_args` (400), `off` (the office
                                               is off here), `timeout`, `too_big`, `error`. One-way pairing is refused
                                               by frame_link's _bots_route before it gets here (403).
  GET  /link/office/asset?path=                a file of the office page (ASSETS, from web/), with its ETag; 304 for a
                                               matching If-None-Match. A path that is not in the table is 404.
  GET  /link/ping                              gains office: {bridge, assets, enabled}: `assets` is one hash of all of
                                               the files (a phone keeps them per computer and fetches again when it
                                               changes), `enabled` is the office on and the bots on.

Nothing here writes. Two reads have a side effect the desktop's page has too: opening a deliverable clears its "new" mark,
and the ops detail window asks for 2 s samples (kept apart per computer, and ending by itself after 60 s).
`tabs` is the new `office_tabs` call: this computer's tabs as the status monitor's last reading has them.
"""
import concurrent.futures
import hashlib
import json
import re
import threading
from pathlib import Path

import link_ops
import office_objects as oo

BRIDGE_V = 1
WEB = Path(__file__).resolve().parent / "web"
JS, CSS, HTML = "text/javascript; charset=utf-8", "text/css; charset=utf-8", "text/html; charset=utf-8"
ASSETS = {"office.html": HTML, "icons.js": JS, "icons.css": CSS, "deliv_draw.js": JS, "office_objects.js": JS,
          "office_objects.css": CSS, "office_arrange.js": JS, "office_ops.js": JS, "office_ops.css": CSS,
          "office_core.js": JS, "chat_rules.js": JS, "bots_kit.js": JS, "office_app_host.js": JS}
TIMEOUT_S = 8.0
MAX_BODY = 1 << 20          # an answer bigger than this is refused (a page asking for the whole world)
MAX_STR = 4096
HTTP = {"no_method": 403, "readonly": 403, "bad_args": 400}
WORDS = {"off": ("The office is off on this computer.", "這台沒有打開辦公室。"),
         "no_method": ("No such call.", "沒有這個呼叫。"),
         "readonly": ("The phone's office only reads.", "手機上的辦公室只能看。"),
         "bad_args": ("Bad arguments.", "參數有誤。"),
         "timeout": ("The computer took too long to answer.", "這台電腦回答太久。"),
         "too_big": ("The answer is too big.", "答案太大。"),
         "error": ("The call failed.", "呼叫失敗。")}
# The calls the office page makes that write (or act), named so a refusal says `readonly` and not "no such call".
WRITE_METHODS = frozenset((
    "office_prefs_save", "office_object_action", "office_object_view", "office_object_live", "task_user_act",
    "schedule_set_enabled", "link_chat", "chat_send", "chat_answer", "chat_view", "link_remote_maintenance",
    "ops_peer", "ops_peer_view", "ops_peer_live", "save_settings", "open_url", "open_local_file", "office_helpers",
    "bots_avatar", "bots_wake", "bots_keep", "bots_rename"))


def _words(code: str, lang) -> str:
    en, zh = WORDS[code]
    return zh if oo.lang_of(lang) == "zh" else en


def fail(code: str, lang="en") -> dict:
    out = {"success": False, "code": code, "message": _words(code, lang)}
    if code in HTTP:
        out["_http"] = HTTP[code]           # frame_link sends it as the status and takes it out of the body
    return out


# ── the page's assets ────────────────────────────────────────────────────────
_assets_lock = threading.Lock()
_assets_seen = {"sig": None, "hash": ""}


def etag_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files() -> dict:
    out = {}
    for name in ASSETS:
        p = WEB / name
        try:
            if p.is_file():
                out[name] = p
        except OSError:
            continue
    return out


def assets_hash() -> str:
    """One hash over every served file's name and ETag (12 characters). Re-read only when a file's size or time moved:
    the phone's ping comes every few seconds."""
    files = _files()
    try:
        sig = tuple((n, p.stat().st_mtime_ns, p.stat().st_size) for n, p in sorted(files.items()))
    except OSError:
        sig = None
    with _assets_lock:
        if sig is not None and sig == _assets_seen["sig"]:
            return _assets_seen["hash"]
    try:
        rows = sorted(f"{n}:{etag_of(p.read_bytes())}" for n, p in files.items())
    except OSError:
        return ""
    h = hashlib.sha256("\n".join(rows).encode()).hexdigest()[:12]
    with _assets_lock:
        _assets_seen.update(sig=sig, hash=h)
    return h


def ping_fields(cfg) -> dict:
    st = (cfg or {}).get("settings") or {}
    return {"bridge": BRIDGE_V, "assets": assets_hash(),
            "enabled": st.get("experimental_office") is True and st.get("experimental_bots") is True}


def _matches(header: str, etag: str) -> bool:
    for tok in str(header or "").split(","):
        tok = tok.strip()
        if tok == "*" or (tok[2:] if tok.startswith("W/") else tok).strip('"') == etag:
            return True
    return False


def serve_asset(handler, link, peer_id: str, peer: dict, nonce: str, q: dict) -> None:
    """GET /link/office/asset?path=: the answer, signed like a staged file (the signature covers the file's sha256 hex,
    which is also the ETag, quoted; a 304 carries the same ETag and signature with no body). The Content-Type is not
    signed, so the phone decides a type from the name itself."""
    def refuse(code, why):
        return handler._send(code, {"success": False, "code": why, "message": _words(why, "en") if why in WORDS else why},
                             sign_for=peer, nonce=nonce)
    if not link._peer_may_control(peer_id):
        return handler._send(403, {"success": False, "message": "單向配對：對方無權查看這台"}, sign_for=peer, nonce=nonce)
    st = ((link._get_config() or {}).get("settings") or {})
    if st.get("experimental_office") is not True:
        return refuse(404, "off")
    name = (q.get("path") or [""])[0]
    path = _files().get(name) if name in ASSETS else None           # the exact name in the table, nothing joined or resolved
    if path is None:
        return refuse(404, "no such asset")
    try:
        data = path.read_bytes()
    except OSError:
        return refuse(404, "no such asset")
    etag = etag_of(data)
    same = _matches(handler.headers.get("If-None-Match") or "", etag)
    body = b"" if same else data
    handler.send_response(304 if same else 200)
    handler.send_header("Content-Type", ASSETS[name])
    handler.send_header("ETag", f'"{etag}"')
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-SF-Body-Sha256", etag)               # the file's hash, on a 304 too: one check for both
    handler.send_header("X-SF-Sign", link._sign_response(peer, nonce, etag.encode()))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


# ── office_tabs: this computer's tabs, for the page's pageTabs() ─────────────
def _program(cmd: str) -> str:
    """The program a tab's command runs (past `cd … &&`, `env`, `VAR=…`): all the page needs of `cmd` (its cliName),
    so no argument, path or variable leaves this computer."""
    words = str(cmd or "").rsplit("&&", 1)[-1].split()
    words = [w for w in words if w not in ("env", "exec") and not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w)]
    return re.split(r"[\\/]", words[0])[-1][:60] if words else ""


def _preview(res: dict, state: str, role_duty: str) -> str:
    """What the page shows a tab is doing: bots.js previewOf (and api_bots._link_tab's `latest`, the same rule)."""
    if state == "working":
        pick = res.get("action") or res.get("narration") or res.get("summary")
    else:
        pick = res.get("narration") or res.get("task") or (res.get("summary") if state not in ("", "unknown", "idle") else "")
    return " ".join(re.sub(r"[`*#>_~]+", "", str(pick or "")).split()) or role_duty


def _loop(raw):
    if not isinstance(raw, dict) or raw.get("kind") not in ("loop", "cron"):
        return None
    import agent_status
    out = {k: raw.get(k) for k in ("kind", "status", "next_ts", "delay", "rounds") if isinstance(raw.get(k), (str, int, float))}
    out["reason"] = agent_status.redact(str(raw.get("reason") or ""), 90)
    out["crons"] = [{"desc": agent_status.redact(str((c or {}).get("desc") or ""), 60), "schedule": str((c or {}).get("schedule") or "")[:40]}
                    for c in (raw.get("crons") or [])[:5] if isinstance(c, dict)]
    return out


def tabs(host, cfg: dict, now: float) -> dict:
    """{success, now, bots_on, tabs: [{sid, label, cmd, cli, role, kind, state, line, narration, loop, opened_at}], helpers}

    The tabs open on this computer, in the sidebar's order, from the status monitor's last reading (its 0.6 s thread
    does the work: nothing is read from a transcript or a screen here). `state` is working / decision / stuck / done /
    idle, the page's `sig-<state>` dots; a tab waiting on a menu or on a RED question is `decision`. With the bots off
    every role is empty and a tab with a CLI is `temp`, as the desktop's office draws it."""
    import agent_status
    import bridge_home
    on = ((cfg.get("settings") or {}).get("experimental_bots")) is True
    roster = cfg.get("agent_roster") or {}
    live, rows = set(host._live_sids()), []
    for sid in host._ordered_sids(cfg):
        s = host.sessions.get(sid)
        if sid not in live or s is None or getattr(s, "_lifecycle_source", "") == "connect":
            continue                                          # a connection's test tab is not a person
        role = host._bots_role_of(sid, cfg) if on else ""
        snap = host._agent_status_snapshot(sid)
        res = (snap[0] if isinstance(snap, tuple) else snap) or {}
        state = str(res.get("state") or "")
        state = state if state in ("working", "decision", "stuck", "done") else "idle"
        if state != "working":
            red = bridge_home.red_pending(sid)
            if res.get("blocked") or (red and red.get("at", 0) >= float(res.get("prompt_at") or 0)):
                state = "decision"
        cmd = str(getattr(s, "cmd", "") or "")
        cli = host._tab_cli(s)
        duty = str((roster.get(role) or {}).get("responsibility") or "") if role else ""
        rows.append({"sid": sid, "label": str(getattr(s, "_custom_label", None) or _program(cmd)), "cmd": _program(cmd), "cli": cli,
                     "role": role, "kind": "bot" if role else ("temp" if cli else "terminal"), "state": state,
                     "line": agent_status.redact(_preview(res, state, duty), 140),
                     "narration": agent_status.redact(str(res.get("narration") or ""), 400), "loop": _loop(res.get("loop")),
                     "opened_at": float(getattr(s, "_spawn_ts", 0) or 0)})
    try:
        helpers = json.loads(host.office_helpers()).get("helpers") or {}
    except Exception:
        helpers = {}
    return {"success": True, "now": now, "bots_on": on, "tabs": rows, "helpers": helpers}


# ── POST /link/office/api ────────────────────────────────────────────────────
def _none(a):
    if a:
        raise ValueError("no arguments")
    return ()


def _str(v, pat=None):
    if not isinstance(v, str) or not v or len(v) > MAX_STR or (pat and not pat.fullmatch(v)):
        raise ValueError("bad string")
    return v


def _args(*kinds):
    def check(a):
        if len(a) != len(kinds):
            raise ValueError("wrong number of arguments")
        return tuple(k(v) for k, v in zip(kinds, a))
    return check


def _id(v):
    return _str(v, oo._ID)


def _task(v):
    if isinstance(v, bool) or not isinstance(v, (int, str)) or not re.fullmatch(r"#?\d{1,9}", str(v)):
        raise ValueError("task number")
    return int(str(v).lstrip("#"))


def _json_arg(v):
    if not isinstance(v, str) or len(v) > MAX_STR:
        raise ValueError("bad json")
    return v


def _sort(v):
    if not isinstance(v, str) or len(v) > 16:
        raise ValueError("bad sort")
    return v


def _flag(v):
    if not isinstance(v, bool):
        raise ValueError("bad flag")
    return v


def _schedules(res: dict) -> dict:
    """The calendar's rows without what the page does not draw: a member is only counted (its command and paths stay
    here), a failure is a word and not a message."""
    keep = ("id", "title", "when", "enabled", "running", "kind", "owner", "toggle", "state", "slots")
    out = {k: res[k] for k in ("schedules", "unsupported", "configured", "launchd_ok") if k in res}
    out["schedules"] = [dict({k: r[k] for k in keep if k in r}, members=[{} for _ in (r.get("members") or [])])
                        for r in (res.get("schedules") or []) if isinstance(r, dict)]
    if res.get("error"):
        out["error"] = "error"
    return out


def _bots_state(res: dict) -> dict:
    res.pop("memory_root", None)                       # a path on this computer
    return res


# name → (argument check, takes the page's language last, call, clean-up of the answer)
METHODS = {
    "office_prefs": (_none, False, lambda h, v, l, p: h.office_prefs(), None),
    "bots_state": (_none, False, lambda h, v, l, p: h.bots_state(), _bots_state),
    "office_tabs": (_none, False, lambda h, v, l, p: h.office_tabs(), None),
    "tasks_overview": (_none, False, lambda h, v, l, p: h.tasks_overview(), None),
    "task_detail": (_args(_task), False, lambda h, v, l, p: h.task_detail(*v), None),
    "schedules_list": (_none, False, lambda h, v, l, p: h.schedules_list(), _schedules),
    "link_peer_bots": (_none, False, lambda h, v, l, p: h.link_peer_bots(), None),
    "link_peer_tasks": (_none, False, lambda h, v, l, p: h.link_peer_tasks(), None),
    "office_objects": (_none, True, lambda h, v, l, p: h.office_objects(l), None),
    "office_object_list": (_args(_id, _json_arg), True, lambda h, v, l, p: h.office_object_list(*v, l), None),
    "office_object_item": (_args(_id, _str), True, lambda h, v, l, p: h.office_object_item(*v, l), None),
    "office_ops_view": (_args(_sort), True, lambda h, v, l, p: link_ops.serve(h._ops_sync(), "ops_view", {"lang": l, "sort": v[0]}), None),
    "office_ops_live": (_args(_flag), False, lambda h, v, l, p: link_ops.serve(h._ops_sync(), "ops_live", {"on": v[0], "peer_id": p}), None),
}
_pool_lock = threading.Lock()
_pool = []


def _run(fn):
    with _pool_lock:
        if not _pool:
            _pool.append(concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="link-office"))
    return _pool[0].submit(fn).result(timeout=TIMEOUT_S)


def serve(host, on: bool, args: dict) -> dict:
    """The answer to one signed POST /link/office/api. `on`: this computer's office switch. `host`: the Api (the
    desktop's js_api, which the table calls by name)."""
    args = args if isinstance(args, dict) else {}
    lang = oo.lang_of(args.get("lang"))
    name = args.get("method")
    if not isinstance(name, str) or name not in METHODS:            # first: a call off the table is refused whether or not the office is on
        return fail("readonly" if isinstance(name, str) and name in WRITE_METHODS else "no_method", lang)
    if not on:
        return fail("off", lang)
    given = args.get("args")
    check, takes_lang, call, clean = METHODS[name]
    try:
        if given is None:
            given = []
        if not isinstance(given, list):
            raise ValueError("args is a list")
        vals = check(given)
    except (ValueError, TypeError):
        return fail("bad_args", lang)
    peer = str(args.get("peer_id") or "")
    try:
        raw = _run(lambda: call(host, vals, lang, peer))
        res = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except concurrent.futures.TimeoutError:
        return fail("timeout", lang)
    except Exception:
        return fail("error", lang)
    if isinstance(res, dict) and clean:
        res = clean(res)
    out = {"success": True, "result": res}
    try:
        if len(json.dumps(out, ensure_ascii=False, default=str)) > MAX_BODY:
            return fail("too_big", lang)
    except (TypeError, ValueError):
        return fail("error", lang)
    return out
