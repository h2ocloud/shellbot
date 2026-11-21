"""What a paired computer may open here: the new-session presets, for its「新增 session」dialog.

GET /link/presets (frame_link, gated like /link/new: a one-way pairing gets 403) answers with this
machine's own presets, so the dialog on the other computer shows the agents installed HERE instead
of a fixed Claude / Codex / bash. Only each preset's name and command leave the machine (no icon
paths, accounts or anything else in the config), a command that carries something secret-looking
(an inline KEY=…, a bearer token) is left out, and the list is capped.

config.json:
  "presets":        [{"name": "…", "cmd": "…", "icon": "…"}, …]   (the existing list)
  "default_preset": "<a preset name>"                              (optional: the dialog's primary button)

The asking side (remote_presets) never marks the peer offline on a failure: an older ShellFrame on
the other end answers 404 here, and the dialog then falls back to its built-in buttons.
"""

import re

MAX_PRESETS = 24
MAX_NAME = 40
MAX_CMD = 400
FETCH_TIMEOUT = 4.0

# An inline credential in a command line: FOO_KEY=…, --token …, Bearer …, well-known key prefixes.
_SECRETISH = (
    re.compile(r"(?i)[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*\s*="),
    re.compile(r"(?i)(?:^|\s)--?(?:api[-_]?key|token|secret|password)\b"),
    re.compile(r"(?i)\bbearer\s+\S"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abps]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"),
)


def _secretish(cmd):
    return any(r.search(cmd) for r in _SECRETISH)


def _clean(text, cap):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(text or "")).strip()[:cap]


def public_presets(cfg) -> dict:
    """This machine's presets as a paired computer may see them."""
    cfg = cfg if isinstance(cfg, dict) else {}
    out, seen = [], set()
    for p in cfg.get("presets") or []:
        if not isinstance(p, dict):
            continue
        name, cmd = _clean(p.get("name"), MAX_NAME), _clean(p.get("cmd"), MAX_CMD)
        if not name or not cmd or name in seen or _secretish(cmd):
            continue
        seen.add(name)
        out.append({"name": name, "cmd": cmd})
        if len(out) >= MAX_PRESETS:
            break
    want = _clean(cfg.get("default_preset"), MAX_NAME)
    default = want if want in seen else ""
    if default:                                   # the primary button comes first
        out.sort(key=lambda p: p["name"] != default)
    return {"success": True, "presets": out, "default": default}


def remote_presets(link, peer_id: str) -> dict:
    """Ask a paired computer for its presets (FrameLink.remote_new's counterpart)."""
    peer, err = link._peer_or_err(peer_id)
    if err:
        return err
    try:
        res = link._signed_request(peer, "GET", "/link/presets", timeout=FETCH_TIMEOUT)
    except Exception as e:                        # an older peer (404), a timeout: the caller falls back
        return {"success": False, "message": str(e)}
    if not isinstance(res, dict) or not res.get("success"):
        return {"success": False, "message": (res or {}).get("message", "no presets") if isinstance(res, dict) else "bad answer"}
    return public_presets({"presets": res.get("presets"), "default_preset": res.get("default")})
