"""A remote MCP server (an https address) added to the AI programs that are allowed to use it, one account
folder at a time.

Some services only let the programs on their own approved list register with them (OAuth dynamic client
registration), so ShellBot cannot hold the sign-in. What it can do is put the server's address into each
AI program the person uses; the sign-in is then that program's own (Claude Code's `/mcp`, `codex mcp login`,
Cursor's Connect button). A route of kind `remote_mcp` says `remote: {"name", "url"}` and which programs
reach it (`clis.<cli>.via == "remote"`).

What this module promises, different from `clis.py` (which owns `shellbot-…` entries and replaces them):

  * The name is the person's own (`figma`, not `shellbot-…`). It is looked at first, and never replaced or removed:
      no entry              -> add it
      same address          -> "present": it counts as added, nothing is changed (the sign-in it holds is kept)
      another address       -> "conflict": left alone, said so
      no answer in time     -> "unknown": nothing added
  * Every account folder a tab may use is covered: the program's default folder and each profile ShellBot made
    (`<config dir>/account-profiles/<cli>/*`), each run with its own CLAUDE_CONFIG_DIR / CODEX_HOME.
  * Nothing a program prints is kept: the address and a sign-in state are read out of it and the rest is dropped.
    A row is a short code, never a command line, a header or a token.

A row is {"cli", "profile" ("default" or the profile's ref), "label", "state"}; `rows_text` turns them into the
plain strings a result may carry ("cli|profile|state|label").
Row states   add:   added | present | conflict | failed | unknown | missing
             check: signed_in | needs_login | unverified | failed | absent | conflict | unknown | missing
"""

import json
import os
import queue
import re
import threading
import time
from pathlib import Path

import sf_config

from . import detect
from .detect import same_url  # noqa: F401 (re-exported)

REMOTE_CLIS = ("claude", "codex", "cursor")          # the programs that can take a remote server here: see clis.REMOTE_MODES
ENV_VAR = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
DEFAULT_DIR = {"claude": ".claude", "codex": ".codex"}
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
ADD_TIMEOUT_S = 40.0
CODEX_ADDED = "added global mcp server"              # `codex mcp add --url` prints this once the entry is written, then starts a sign-in
_ROW_SAFE = re.compile(r"[^\w .@+-]", re.U)


def rows_text(rows) -> list:
    """The rows as short strings ("cli|profile|state|label"), the form a result may carry."""
    return [f"{r['cli']}|{r['profile']}|{r['state']}|{_ROW_SAFE.sub('', str(r.get('label') or ''))[:40]}" for r in rows][:20]


def spawn_default(argv, env):
    """Start a program with its output in one pipe and a process group of its own (so its tree can be taken down).
    Refuses under test: a test passes its own `launcher`."""
    if sf_config._running_tests():
        raise RuntimeError("a test must pass its own launcher")
    import subprocess
    kw = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, **kw)


def launch_until(argv, env, marker: str, timeout: float, spawn=None) -> bool:
    """Start a program that goes on to wait for a person (a sign-in), read its output until `marker` shows (lowercase
    compare), then take the whole process tree down. True when the marker was seen. The program is always gone
    afterwards: what it printed is dropped, and the sign-in it was about to start never gets going."""
    from connectors.services._local_mcp import kill_tree
    try:
        proc = (spawn or spawn_default)(argv, env)
    except RuntimeError:
        raise
    except Exception:
        return False
    lines = queue.Queue()

    def pump():                                  # a pipe's readline: bytes from a real program, text from a stand-in
        try:
            while True:
                raw = proc.stdout.readline()
                if not raw:
                    break
                lines.put(raw)
        except Exception:
            pass
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    seen, end = False, time.time() + timeout
    try:
        while time.time() < end:
            try:
                raw = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if raw is None:
                break
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            if marker in text.lower():
                seen = True
                break
    finally:
        kill_tree(proc)
    return seen


class Profile:
    __slots__ = ("cli", "key", "label", "env")

    def __init__(self, cli, key, label, env):
        self.cli, self.key, self.label, self.env = cli, key, label, env


class Remote:
    """What the AI programs on this computer hold for one remote server (`name` at `url`).

    run(argv, timeout, env) -> Result(rc, out)   how a program is run (clis.Clis.runner); `out` is lowercase
    find(cli) -> path | None                      where a program is (clis.Clis.finder)
    env                                           the base environment (a dict, or a function of the cli name)
    cfg                                           ShellBot's config (the account labels), or None
    launcher(argv, env, marker, timeout) -> bool  how `codex mcp add --url` is run (launch_until); a test passes a stand-in
    home / cfg_dir                                where ~ and the app's folder are (a test passes its own)
    """

    def __init__(self, run, find, env=None, cfg=None, launcher=None, home=None, cfg_dir=None):
        self.run, self.find, self.env, self.cfg = run, find, env, cfg or {}
        self.launcher = launcher or launch_until
        self.home = Path(home) if home else Path.home()
        self.cfg_dir = Path(cfg_dir) if cfg_dir else sf_config.config_dir()

    # ── which folders ──────────────────────────────────────────────────────
    def _base(self, cli) -> dict:
        env = self.env(cli) if callable(self.env) else (self.env if self.env is not None else dict(os.environ))
        return {k: v for k, v in dict(env).items() if k != ENV_VAR.get(cli)}

    def _label_of(self, cli, ref) -> str:
        for item in ((self.cfg.get("accounts") or {}).get("profiles") or {}).get(cli) or []:
            if isinstance(item, dict) and item.get("id") == ref:
                return str(item.get("label") or "")
        return ""

    def profiles(self, cli) -> list:
        """Every account folder of this program a tab may use: its default folder (when it exists) and each profile
        ShellBot made. Cursor has one place (~/.cursor/mcp.json)."""
        base = self._base(cli)
        if cli == "cursor":
            return [Profile(cli, "default", "", base)]
        out = []
        var, default = ENV_VAR[cli], self.home / DEFAULT_DIR[cli]
        if default.is_dir() or (cli == "claude" and (self.home / ".claude.json").exists()):
            out.append(Profile(cli, "default", "", base))
        root = self.cfg_dir / "account-profiles" / cli
        try:
            refs = sorted(p.name for p in root.iterdir() if p.is_dir())
        except OSError:
            refs = []
        for ref in refs:
            out.append(Profile(cli, ref, self._label_of(cli, ref), {**base, var: str(root / ref)}))
        return out

    def installed(self, cli) -> bool:
        try:
            return bool(self.find(cli))
        except Exception:
            return False

    # ── looking ────────────────────────────────────────────────────────────
    def _cursor_file(self, prof) -> Path:
        home = prof.env.get("HOME") or prof.env.get("USERPROFILE")
        return (Path(home) if home else self.home) / ".cursor" / "mcp.json"

    def look(self, prof, name: str, url: str) -> dict:
        """What this folder holds under `name`: {"entry": absent | same | conflict | unknown, "auth": signed_in |
        needs_login | failed | unverified | unknown}. Read-only."""
        if prof.cli == "claude":
            return self._look_claude(prof, name, url)
        if prof.cli == "codex":
            return self._look_codex(prof, name, url)
        return self._look_cursor(prof, name, url)

    def _look_claude(self, prof, name, url) -> dict:
        r = self.run([self.find("claude"), "mcp", "get", name], ADD_TIMEOUT_S, prof.env)
        if r.rc in (-1, -2):
            return {"entry": "unknown", "auth": "unknown"}
        out = r.out or ""
        if r.rc != 0:
            return {"entry": "absent" if re.search(r"no mcp server named", out) else "unknown", "auth": "unknown"}
        m = re.search(r"^\s*url:\s*(\S+)", out, re.M)
        if not m or not same_url(m.group(1), url):
            return {"entry": "conflict", "auth": "unknown"}
        st = (re.search(r"^\s*status:\s*(.*)$", out, re.M) or [None, ""])[1]
        _, why = detect._claude_state(st)
        auth = ("needs_login" if why == "auth" else "failed" if why == "failed"
                else "signed_in" if detect._claude_state(st)[0] == "checked" else "unknown")
        return {"entry": "same", "auth": auth}

    def _look_codex(self, prof, name, url) -> dict:
        r = self.run([self.find("codex"), "mcp", "list"], ADD_TIMEOUT_S, prof.env)
        if r.rc in (-1, -2):
            return {"entry": "unknown", "auth": "unknown"}
        entries = detect.parse_codex(r.out) if r.rc == 0 else None
        if entries is None:
            return {"entry": "unknown", "auth": "unknown"}
        e = next((x for x in entries if x["name"] == name), None)
        if not e:
            return {"entry": "absent", "auth": "unknown"}
        if e["kind"] != "remote" or not same_url(e.get("url"), url):
            return {"entry": "conflict", "auth": "unknown"}
        a = e.get("auth", "")
        if "not logged in" in a:
            auth = "needs_login"
        elif a in ("oauth", "bearer token"):
            auth = "signed_in"
        else:
            auth = "unknown"
        if e["state"] == "broken" and e["why"] == "off":
            auth = "unknown"
        return {"entry": "same", "auth": auth}

    def _read_cursor(self, path):
        """(servers dict | None, whole dict | None): None when the file is there and cannot be read as the JSON it should be."""
        if not path.exists():
            return {}, {}
        try:
            whole = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        servers = whole.get("mcpServers", {}) if isinstance(whole, dict) else None
        return (servers, whole) if isinstance(servers, dict) else (None, None)

    def _look_cursor(self, prof, name, url) -> dict:
        servers, _ = self._read_cursor(self._cursor_file(prof))
        if servers is None:
            return {"entry": "unknown", "auth": "unknown"}
        e = servers.get(name)
        if e is None:
            return {"entry": "absent", "auth": "unknown"}
        same = isinstance(e, dict) and same_url(e.get("url"), url)
        return {"entry": "same" if same else "conflict", "auth": "unverified" if same else "unknown"}   # Cursor's own sign-in cannot be read from here

    # ── adding ─────────────────────────────────────────────────────────────
    def add(self, prof, name: str, url: str) -> str:
        """One of added | present | conflict | failed | unknown | missing. Looks first and never replaces."""
        if not self.installed(prof.cli) and not (prof.cli == "cursor" and (self.home / ".cursor").is_dir()):
            return "missing"
        before = self.look(prof, name, url)["entry"]
        if before == "same":
            return "present"
        if before in ("conflict", "unknown"):
            return before
        try:
            if prof.cli == "claude":
                r = self.run([self.find("claude"), "mcp", "add", "--transport", "http", "-s", "user", name, url], ADD_TIMEOUT_S, prof.env)
                if r.rc != 0:
                    return "failed"
            elif prof.cli == "codex":
                # `codex mcp add --url` writes the entry and then waits for a browser sign-in; ShellBot only wants the entry.
                self.launcher([self.find("codex"), "mcp", "add", name, "--url", url], prof.env, CODEX_ADDED, ADD_TIMEOUT_S)
            elif not self._write_cursor(prof, name, url):
                return "failed"
        except RuntimeError:
            raise
        except Exception:
            return "failed"
        return "added" if self.look(prof, name, url)["entry"] == "same" else "failed"     # what counts is what the program now holds

    def _write_cursor(self, prof, name, url) -> bool:
        """Merge the server into ~/.cursor/mcp.json: every other server and key kept; a file that is not the JSON it
        should be is not touched."""
        path = self._cursor_file(prof)
        servers, whole = self._read_cursor(path)
        if servers is None or name in servers:
            return False
        whole = dict(whole)
        whole["mcpServers"] = {**servers, name: {"url": url}}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + f".sf{os.getpid()}.tmp")
            tmp.write_text(json.dumps(whole, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            if path.exists():
                try:
                    os.chmod(tmp, path.stat().st_mode & 0o777)
                except OSError:
                    pass
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    # ── the whole set ──────────────────────────────────────────────────────
    def _each(self, clis):
        for cli in (clis or REMOTE_CLIS):
            if cli not in REMOTE_CLIS:
                continue
            if not self.installed(cli) and not (cli == "cursor" and (self.home / ".cursor").is_dir()):
                yield Profile(cli, "default", "", {}), "missing"
                continue
            for prof in self.profiles(cli):
                yield prof, None

    def add_all(self, name: str, url: str, clis=None) -> list:
        """Add `name` to every folder of every program in `clis` (default: all that can take it). The rows, in order.
        A program that is not installed has one row, "missing", and is skipped."""
        rows = []
        for prof, gone in self._each(clis):
            rows.append({"cli": prof.cli, "profile": prof.key, "label": prof.label, "state": gone or self.add(prof, name, url)})
        return rows

    def check_all(self, name: str, url: str, clis=None) -> list:
        """The same folders, only looked at: the sign-in each holds (check states above)."""
        rows = []
        for prof, gone in self._each(clis):
            if gone:
                state = gone
            else:
                got = self.look(prof, name, url)
                state = (("unverified" if got["auth"] == "unknown" else got["auth"]) if got["entry"] == "same" else got["entry"])
            rows.append({"cli": prof.cli, "profile": prof.key, "label": prof.label, "state": state})
        return rows
