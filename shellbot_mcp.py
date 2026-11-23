#!/usr/bin/env python3
"""ShellBot's MCP gateway: one MCP server (stdio) that every AI program on this computer can use.

An AI program starts it (`<install>/.venv/bin/python <install>/shellbot_mcp.py`, absolute paths, added by the
Connect page) and talks newline-delimited JSON-RPC 2.0 to it. Behind it sit "upstreams": a service ShellBot has
connected, reached with the login ShellBot keeps in the system key store. This build has one, GitHub's own remote
MCP server (https://api.githubcopilot.com/mcp/, Streamable HTTP). Its tools come out with a `github_` prefix.

  * The token is read from the key store when the gateway starts (`connectors/secrets.py`, service "github",
    name "token"). It is never in an argument list, an environment, an AI program's settings file, a log or any
    message: every outgoing line and every log line has the exact value replaced, and logs go through `mask`.
  * stdout carries JSON-RPC and nothing else (stdout is pointed at stderr for the whole process; only the one
    writer below uses the real one). Logs go to stderr and the debug log.
  * Read-only by default. ShellBot's config.json `connect.github.readonly` (default true) is sent upstream as
    `X-MCP-Readonly: true`, and a tool that is not marked read-only is also dropped here. `connect.github.toolsets`
    (default `repos,issues,pull_requests,actions`) goes upstream as `X-MCP-Toolsets`. Writing is turned on by the
    person on the Connect page, never by an AI.
  * No token, no key store (Linux), a token GitHub refuses, or GitHub unreachable: `tools/list` holds one tool,
    `shellbot_status`, that says why and where to set it up. An upstream's error comes back as an MCP error result,
    never an exception.
  * `Upstream` is the seam: Gmail and others plug in by adding a class with `name`, `available()`, `list_tools()`
    and `call()`.

`sfctl call` uses the same classes in its own process (`build_gateway`, `Gateway.call_tool`), not through the app.
stdlib only.
"""

import io
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_NAME = "shellbot"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
STATUS_TOOL = "shellbot_status"

# An AI program starts the gateway and asks for its tools within its own start-up limit (Codex: 10 s by default), so
# opening the session and listing the tools get a short budget; a stuck GitHub then shows as the status tool, not as a
# server that never answered. A tool call may take longer.
INIT_TIMEOUT_S = 3.0
LIST_BUDGET_S = 5.0
CALL_TIMEOUT_S = 45.0

GITHUB_URL = "https://api.githubcopilot.com/mcp/"
GITHUB_TOOLSETS = "repos,issues,pull_requests,actions"
_TOOLSET_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}(,[a-z][a-z0-9_]{0,40}){0,20}$")
BULLETS = "••••"


# ── small helpers ──────────────────────────────────────────────────────────
def _version() -> str:
    try:
        return str(json.loads((Path(__file__).resolve().parent / "version.json").read_text(encoding="utf-8")).get("version") or "0")
    except (OSError, ValueError):
        return "0"


def _mask(text) -> str:
    try:
        from connectors import secrets
        return secrets.mask(text)
    except Exception:
        return BULLETS if "token" in str(text).lower() else str(text)


def log(msg) -> None:
    """One log line to stderr and the debug log, after the mask. Never raises."""
    line = _mask(msg)
    try:
        sys.stderr.write(f"shellbot-mcp: {line}\n")
        sys.stderr.flush()
    except Exception:
        pass
    try:
        from sf_log import _dlog
        _dlog("mcp", line)
    except Exception:
        pass


def _words(en: str, zh: str) -> str:
    try:
        import sf_lang
        return sf_lang.pick(en, zh)
    except Exception:
        return en


def _config() -> dict:
    """config.json as a plain read (the gateway is not the app and takes no lock). {} when unreadable."""
    try:
        import sf_config
        data = json.loads(sf_config.config_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def github_settings(cfg: dict = None) -> dict:
    """{"readonly": bool, "toolsets": str} from config.json `connect.github`: read-only unless it says `false`
    outright, and only a well-formed toolset list is taken (else the default)."""
    block = (((cfg if cfg is not None else _config()).get("connect") or {}).get("github") or {})
    block = block if isinstance(block, dict) else {}
    ro = block.get("readonly")
    ts = block.get("toolsets")
    if isinstance(ts, (list, tuple)):
        ts = ",".join(str(x).strip() for x in ts)
    ts = str(ts or "").replace(" ", "").lower()
    return {"readonly": False if ro is False else True, "toolsets": ts if _TOOLSET_RE.match(ts) else GITHUB_TOOLSETS}


# ── an MCP server reached over Streamable HTTP ─────────────────────────────
class UpstreamError(Exception):
    """kind: auth | network | http | rpc | protocol; `status` the HTTP status when there was one."""

    def __init__(self, kind: str, message: str = "", status: int = 0, code=None):
        super().__init__(message)
        self.kind, self.status, self.code = kind, status, code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):            # the bearer token must never follow a redirect to another host
        return None


def _sse_messages(resp):
    """The JSON messages of a `text/event-stream` body, one at a time (an event is lines up to a blank one)."""
    data = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            if data:
                try:
                    yield json.loads("\n".join(data))
                except ValueError:
                    pass
                data = []
        elif line.startswith("data:"):
            data.append(line[5:][1:] if line[5:].startswith(" ") else line[5:])
    if data:
        try:
            yield json.loads("\n".join(data))
        except ValueError:
            pass


class HttpMcpClient:
    """A minimal Streamable-HTTP MCP client: POST JSON-RPC, the answer is JSON or an event stream, the
    `Mcp-Session-Id` the server hands out is kept and sent back, the session is re-opened once when the server
    forgot it."""

    def __init__(self, url: str, headers: dict, timeout: float = 30.0, opener=None, init_timeout: float = INIT_TIMEOUT_S):
        self.url = url
        self.headers = dict(headers)
        self.timeout = timeout
        self.init_timeout = init_timeout
        self.session_id = ""
        self.protocol = PROTOCOLS[1]
        self._id = 0
        self._lock = threading.Lock()
        self._init_lock = threading.RLock()
        self._ready = False
        self._opener = opener or urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _post(self, payload: dict, want_id=None, timeout: float = None):
        hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            hdrs["Mcp-Session-Id"] = self.session_id
            hdrs["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
        try:
            resp = self._opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(600).decode("utf-8", "replace")
            except Exception:
                pass
            kind = "auth" if e.code in (401, 403) else "http"
            raise UpstreamError(kind, f"HTTP {e.code} {body[:300]}".strip(), status=e.code)
        except (urllib.error.URLError, OSError, ssl.SSLError, TimeoutError) as e:
            raise UpstreamError("network", f"{type(e).__name__}: {getattr(e, 'reason', e)}")
        try:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid and want_id == "init":
                self.session_id = sid
            if want_id is None:
                resp.read()
                return None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            ident = payload.get("id")
            if "text/event-stream" in ctype:
                for msg in _sse_messages(resp):
                    if isinstance(msg, dict) and msg.get("id") == ident and ("result" in msg or "error" in msg):
                        return msg
                raise UpstreamError("protocol", "the stream ended without an answer")
            body = resp.read(8 * 1024 * 1024)
            try:
                data = json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                raise UpstreamError("protocol", "the answer is not JSON")
            for msg in data if isinstance(data, list) else [data]:
                if isinstance(msg, dict) and msg.get("id") == ident and ("result" in msg or "error" in msg):
                    return msg
            raise UpstreamError("protocol", "no answer with this id")
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def initialize(self) -> None:
        with self._init_lock:
            self.session_id, self._ready = "", False
            msg = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
                              "params": {"protocolVersion": self.protocol, "capabilities": {},
                                         "clientInfo": {"name": SERVER_NAME, "version": _version()}}}, want_id="init",
                             timeout=self.init_timeout)
            if "error" in msg:
                raise UpstreamError("rpc", str((msg["error"] or {}).get("message") or "initialize failed"), code=(msg["error"] or {}).get("code"))
            got = ((msg.get("result") or {}).get("protocolVersion"))
            if isinstance(got, str) and got in PROTOCOLS:
                self.protocol = got
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=self.init_timeout)
            self._ready = True

    def request(self, method: str, params: dict = None, timeout: float = None) -> dict:
        """The `result` of a JSON-RPC call; UpstreamError("rpc") carries a JSON-RPC error. The session is opened
        first, and once more when the server says it no longer knows it (404)."""
        for attempt in (0, 1):
            with self._init_lock:
                if not self._ready:
                    self.initialize()
            payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, **({"params": params} if params is not None else {})}
            try:
                msg = self._post(payload, want_id=payload["id"], timeout=timeout)
            except UpstreamError as e:
                if e.status == 404 and self.session_id and attempt == 0:
                    self._ready = False
                    continue
                raise
            if "error" in msg:
                err = msg["error"] if isinstance(msg["error"], dict) else {}
                raise UpstreamError("rpc", str(err.get("message") or "error"), code=err.get("code"))
            return msg.get("result") if isinstance(msg.get("result"), dict) else {}
        raise UpstreamError("protocol", "no session")

    def close(self) -> None:
        if not self.session_id:
            return
        try:
            req = urllib.request.Request(self.url, headers={**self.headers, "Mcp-Session-Id": self.session_id}, method="DELETE")
            self._opener.open(req, timeout=5).close()
        except Exception:
            pass


# ── upstreams ──────────────────────────────────────────────────────────────
def _text_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": bool(error)}


class Upstream:
    """One connected service behind the gateway. `name` prefixes its tools (`<name>_<tool>`)."""
    name = ""

    def available(self) -> str:
        """'' when it can be used now, else a code: no_token | unsupported | auth | unreachable."""
        raise NotImplementedError

    def list_tools(self) -> list:
        """The upstream's own tool dicts (name, description, inputSchema, annotations …), unprefixed."""
        raise NotImplementedError

    def call(self, tool: str, arguments: dict) -> dict:
        """An MCP tool result {"content": [...], "isError": bool}. An error of the upstream is a result, not an exception."""
        raise NotImplementedError

    def why(self, code: str) -> str:
        """What to tell the AI (and so the person) for an `available()` code."""
        return code

    def close(self) -> None:
        pass


class GithubUpstream(Upstream):
    name = "github"

    def __init__(self, backend=None, url: str = GITHUB_URL, settings: dict = None, opener=None, timeout: float = CALL_TIMEOUT_S,
                 retry_s: float = 3.0, init_timeout: float = INIT_TIMEOUT_S, list_budget: float = LIST_BUDGET_S):
        from connectors import secrets
        self._store = secrets.SecretStore("github", backend)
        self.url = url
        self.settings = settings if settings is not None else github_settings()
        self.timeout = timeout
        self.init_timeout, self.list_budget = init_timeout, list_budget
        self._opener = opener
        self._retry_s = retry_s
        self._token = ""
        self._client = None
        self._code = "no_token"
        self._checked = 0.0
        self._lock = threading.RLock()
        self._load_token()

    # -- the token: from the key store, kept in this process only ------------
    def _load_token(self) -> None:
        from connectors import secrets
        self._checked = time.monotonic()
        try:
            tok = self._store.get("token")
        except secrets.Unsupported:
            self._token, self._code = "", "unsupported"
            return
        except Exception:
            self._token, self._code = "", "no_token"
            return
        tok = str(tok or "").strip()
        if tok != self._token:
            self._client = None                          # a new token: a new session
        self._token = tok
        self._code = "" if tok else "no_token"

    @property
    def token(self) -> str:
        return self._token

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self._token}", "X-MCP-Toolsets": self.settings["toolsets"]}
        if self.settings["readonly"]:
            h["X-MCP-Readonly"] = "true"
        return h

    def _get_client(self) -> HttpMcpClient:
        with self._lock:
            if self._client is None:
                self._client = HttpMcpClient(self.url, self._headers(), timeout=self.timeout, opener=self._opener, init_timeout=self.init_timeout)
            return self._client

    def _refresh_if_due(self) -> None:
        """Not usable: look in the key store again (the person may have just pasted the token), at most every few seconds."""
        with self._lock:
            if self._code in ("no_token", "auth") and time.monotonic() - self._checked >= self._retry_s:
                self._load_token()

    # -- the Upstream interface ----------------------------------------------
    def available(self) -> str:
        self._refresh_if_due()
        return self._code

    def _run(self, method: str, params: dict = None, timeout: float = None):
        """request() with one fresh read of the key store when GitHub says the token is wrong."""
        timeout = timeout or self.timeout
        try:
            return self._get_client().request(method, params, timeout=timeout)
        except UpstreamError as e:
            if e.kind != "auth":
                raise
            before = self._token
            with self._lock:
                self._load_token()
            if not self._token or self._token == before:
                self._code = "auth"
                raise
            return self._get_client().request(method, params, timeout=timeout)

    def list_tools(self) -> list:
        tools, cursor = [], None
        deadline = time.monotonic() + self.list_budget
        for _ in range(20):
            left = deadline - time.monotonic()
            if left <= 0:
                raise UpstreamError("network", "listing the tools took too long")
            res = self._run("tools/list", {"cursor": cursor} if cursor else {}, timeout=max(1.0, left))
            tools += [t for t in (res.get("tools") or []) if isinstance(t, dict) and isinstance(t.get("name"), str)]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        if self.settings["readonly"]:                    # also here: only what is marked read-only (or says nothing the other way)
            tools = [t for t in tools if (t.get("annotations") or {}).get("readOnlyHint") is not False]
        return tools

    def call(self, tool: str, arguments: dict) -> dict:
        try:
            res = self._run("tools/call", {"name": tool, "arguments": arguments if isinstance(arguments, dict) else {}})
        except UpstreamError as e:
            if e.kind == "rpc":
                return _text_result(f"GitHub: {e}" + (f" (code {e.code})" if e.code is not None else ""), True)
            if e.kind == "auth":
                return _text_result("GitHub refused the token. " + self.why("auth"), True)
            return _text_result(f"GitHub could not be reached: {e}", True)
        return res if isinstance(res.get("content"), list) else _text_result(json.dumps(res, ensure_ascii=False), bool(res.get("isError")))

    def why(self, code: str) -> str:
        where = _words("Open ShellBot, Settings → Connect → GitHub.", "請開 ShellBot 的 設定 → 連結 → GitHub。")
        if code == "no_token":
            return _words(f"GitHub is not connected yet: no token is saved. {where} Paste a fine-grained personal access token there.",
                          f"GitHub 還沒連結：沒有存任何 token。{where}在那裡貼上細粒度個人 token。")
        if code == "unsupported":
            return _words("GitHub through ShellBot is not available on this computer: it keeps the token in the system key store, "
                          "and this system has none ShellBot can use.", "這台電腦不能用 ShellBot 連 GitHub：token 要存在系統鑰匙圈，而這個系統沒有 ShellBot 能用的。")
        if code == "auth":
            return _words(f"GitHub did not accept the saved token (expired, revoked or missing a permission). {where} Make a new token and paste it again.",
                          f"GitHub 不接受存著的 token（過期、被收回或少了權限）。{where}重新建一個 token 貼上。")
        return _words(f"GitHub could not be reached just now. Try again in a moment. If it keeps happening, check this computer's network. {where}",
                      f"現在連不上 GitHub。稍後再試；一直這樣的話請檢查這台電腦的網路。{where}")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


# ── the gateway: MCP server over the upstreams ─────────────────────────────
class Gateway:
    def __init__(self, upstreams: list):
        self.upstreams = list(upstreams)
        self._exposed = {}                  # prefixed name -> (upstream, raw name)
        self._lock = threading.Lock()
        self._lister = threading.Lock()
        self.reasons = {}                   # upstream name -> the code it last could not be used for ('' when fine)
        self.notify = None                  # fn(dict): where a server notification goes (the stdio writer)

    def secret_values(self) -> list:
        """The secrets the upstreams hold (the exact values), to be taken out of anything that leaves this process."""
        return [v for v in (getattr(up, "token", "") for up in self.upstreams) if v and len(v) >= 4]

    def clean(self, obj):
        """`obj` (JSON-able) with every secret value replaced by ••••: an upstream's answer or error text may repeat the
        token it was sent. Applied to every tool result, whoever asks (the stdio server, `sfctl call`)."""
        vals = self.secret_values()
        if not vals:
            return obj
        text = json.dumps(obj, ensure_ascii=False)
        for v in vals:
            text = text.replace(json.dumps(v, ensure_ascii=False)[1:-1], BULLETS).replace(v, BULLETS)
        return json.loads(text)

    # -- tools ---------------------------------------------------------------
    def _status_tool(self) -> dict:
        return {"name": STATUS_TOOL, "description": "ShellBot's connections: which services are connected, and why one is not working.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "title": "ShellBot status"}}

    def tools(self) -> list:
        """The tools to offer now: every usable upstream's (prefixed), plus `shellbot_status` while one is not usable."""
        with self._lister:
            out, exposed, broken = [], {}, False
            for up in self.upstreams:
                code = up.available()
                if not code:
                    try:
                        for t in up.list_tools():
                            name = f"{up.name}_{t['name']}"
                            exposed[name] = (up, t["name"])
                            out.append({**t, "name": name})
                    except UpstreamError as e:
                        code = "auth" if e.kind == "auth" else "unreachable"
                        log(f"{up.name} tools/list failed: {e.kind} {e}")
                    except Exception as e:                    # an upstream must never take the gateway down
                        code = "unreachable"
                        log(f"{up.name} tools/list crashed: {type(e).__name__}")
                self.reasons[up.name] = code
                broken = broken or bool(code)
            with self._lock:
                self._exposed = exposed
            if broken or not self.upstreams:
                out.append(self._status_tool())
            return out

    def status_text(self) -> str:
        lines = []
        for up in self.upstreams:
            code = up.available()
            if not code:
                try:
                    up.list_tools()
                except UpstreamError as e:
                    code = "auth" if e.kind == "auth" else "unreachable"
                except Exception:
                    code = "unreachable"
            self.reasons[up.name] = code
            lines.append(f"{up.name}: " + (_words("connected", "已連結") if not code else up.why(code)))
        return "\n".join(lines) or _words("No service is connected.", "沒有連結任何服務。")

    def call_tool(self, name: str, arguments: dict = None) -> dict:
        """A prefixed name (`github_list_issues`) or `shellbot_status`. {"error": …} for a name that is not offered."""
        arguments = arguments if isinstance(arguments, dict) else {}
        if name == STATUS_TOOL:
            before = set(self._exposed)
            text = self.status_text()
            self.tools()
            if set(self._exposed) != before and self.notify:
                self.notify({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            return self.clean(_text_result(text))
        with self._lock:
            target = self._exposed.get(name)
        if target is None:
            self.tools()
            with self._lock:
                target = self._exposed.get(name)
        if target is None:
            return {"error": f"unknown tool: {name}"}
        up, raw = target
        try:
            return self.clean(up.call(raw, arguments))
        except Exception as e:
            log(f"{up.name} call crashed: {type(e).__name__}")
            return _text_result(f"{up.name}: {type(e).__name__}", True)

    def resolve(self, service: str, tool: str) -> str:
        """The prefixed name for `sfctl call <service> <tool>`; the tool may already carry the prefix."""
        return tool if tool.startswith(f"{service}_") and f"{service}_{tool}" not in self._exposed else f"{service}_{tool}"

    def close(self) -> None:
        for up in self.upstreams:
            try:
                up.close()
            except Exception:
                pass

    # -- JSON-RPC ------------------------------------------------------------
    def handle(self, msg):
        """One JSON-RPC message in, the response dict out (None for a notification)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        method, ident, params = msg.get("method"), msg.get("id"), msg.get("params")
        params = params if isinstance(params, dict) else {}
        if not isinstance(method, str):
            return None if "id" not in msg else _rpc_error(ident, -32600, "invalid request")
        if "id" not in msg:
            return None                                              # notifications/initialized, cancelled, …: nothing to answer
        if method == "initialize":
            want = params.get("protocolVersion")
            return _rpc_result(ident, {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[1],
                                       "capabilities": {"tools": {"listChanged": True}},
                                       "serverInfo": {"name": SERVER_NAME, "title": "ShellBot", "version": _version()},
                                       "instructions": "ShellBot's connected services. Tools are named <service>_<tool> (github_…). "
                                                       "GitHub is read-only unless the person turned writing on in ShellBot."})
        if method == "ping":
            return _rpc_result(ident, {})
        if method == "tools/list":
            return _rpc_result(ident, {"tools": self.tools()})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                return _rpc_error(ident, -32602, "name is required")
            res = self.call_tool(name, params.get("arguments"))
            if "error" in res and "content" not in res:
                return _rpc_error(ident, -32602, res["error"])
            return _rpc_result(ident, res)
        return _rpc_error(ident, -32601, f"method not found: {method}")


def _rpc_result(ident, result) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _rpc_error(ident, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def build_gateway(backend=None, github_url: str = GITHUB_URL, settings: dict = None, opener=None) -> Gateway:
    """The gateway as this computer is set up: the key store's token, config.json's settings."""
    return Gateway([GithubUpstream(backend=backend, url=github_url, settings=settings, opener=opener)])


# ── for scripts: `sfctl call` ──────────────────────────────────────────────
def cli_call(service: str, tool: str, args_json: str = "", list_only: bool = False, gateway: Gateway = None) -> tuple:
    """(exit code, JSON-able dict) for `sfctl call`: this process asks the upstream itself, nothing goes through the app.
    `--list`: {"tools": [{"service", "tool", "name", "description"}], "unavailable": {service: code}, "message"}.
    A call: {"ok", "isError", "content": [...], "data": <the text parsed as JSON, when it is JSON>} or
    {"ok": false, "code", "message"} when the service cannot be used or the tool is not offered."""
    gw = gateway or build_gateway()
    try:
        tools = gw.tools()
        names = {up.name: up for up in gw.upstreams}
        bad = {n: c for n, c in gw.reasons.items() if c}
        if list_only:
            rows = [{"service": n, "tool": t["name"][len(n) + 1:], "name": t["name"], "description": str(t.get("description") or "")}
                    for t in tools if t["name"] != STATUS_TOOL for n in [t["name"].split("_", 1)[0]]]
            out = {"tools": rows, "unavailable": bad}
            if bad:
                out["message"] = " ".join(names[n].why(c) for n, c in bad.items())
            return (1 if bad and not rows else 0), out
        if service not in names:
            return 2, {"ok": False, "code": "unknown_service", "message": f"unknown service: {service} (known: {', '.join(sorted(names)) or 'none'})"}
        try:
            arguments = json.loads(args_json) if str(args_json or "").strip() else {}
        except ValueError:
            return 2, {"ok": False, "code": "bad_args", "message": "--args must be a JSON object"}
        if not isinstance(arguments, dict):
            return 2, {"ok": False, "code": "bad_args", "message": "--args must be a JSON object"}
        if bad.get(service):
            return 1, {"ok": False, "code": bad[service], "message": names[service].why(bad[service])}
        res = gw.call_tool(gw.resolve(service, tool), arguments)
        if "error" in res and "content" not in res:
            return 2, {"ok": False, "code": "unknown_tool", "message": f"{res['error']} (see `sfctl call --list`)"}
        out = {"ok": not res.get("isError"), "isError": bool(res.get("isError")), "content": res.get("content") or []}
        texts = [c.get("text") for c in out["content"] if isinstance(c, dict) and c.get("type") == "text"]
        if len(texts) == 1:
            try:
                out["data"] = json.loads(texts[0])
            except (ValueError, TypeError):
                pass
        return (1 if out["isError"] else 0), out
    finally:
        gw.close()


# ── stdio ──────────────────────────────────────────────────────────────────
def serve(gateway: Gateway, stdin, stdout, secret_values=None) -> None:
    """Read newline-delimited JSON-RPC from `stdin`, write responses to `stdout`, until stdin ends. A request is
    answered on a thread of its own (a slow GitHub call must not hold up a ping); the writer is one lock. Every
    line written has the token (and anything else in `secret_values()`) replaced first."""
    wlock = threading.Lock()
    threads = []

    def scrub(s: str) -> str:
        for v in (secret_values() if secret_values else ()):
            if v and len(v) >= 4:
                s = s.replace(v, BULLETS)
        return s

    def emit(obj) -> None:
        line = scrub(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))
        with wlock:
            try:
                stdout.write(line + "\n")
                stdout.flush()
            except Exception:
                pass

    gateway.notify = emit

    def work(msg) -> None:
        try:
            out = gateway.handle(msg)
        except Exception as e:                                        # never a stack trace on the wire
            log(f"handler crashed: {type(e).__name__}")
            out = _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32603, "internal error")
        if out is not None:
            emit(out)

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            emit(_rpc_error(None, -32700, "parse error"))
            continue
        for one in msg if isinstance(msg, list) else [msg]:
            if isinstance(one, dict) and one.get("method") in ("tools/list", "tools/call"):
                t = threading.Thread(target=work, args=(one,), daemon=True, name="shellbot-mcp-req")
                t.start()
                threads.append(t)
                threads[:] = [x for x in threads if x.is_alive()]
            else:
                work(one)
    for t in threads:                                                 # stdin closed: let the answers in flight go out
        t.join(60)


def main(argv=None, backend=None, github_url: str = GITHUB_URL, settings: dict = None) -> int:
    real_out = sys.stdout
    sys.stdout = sys.stderr                                           # a stray print can never reach the wire
    if hasattr(real_out, "reconfigure"):
        try:
            real_out.reconfigure(encoding="utf-8", newline="\n")
        except Exception:
            pass
    stdin = sys.stdin
    if hasattr(stdin, "reconfigure"):
        try:
            stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass
    gw = build_gateway(backend=backend, github_url=github_url, settings=settings)
    log(f"gateway {_version()} started")
    try:
        serve(gw, stdin, real_out, secret_values=gw.secret_values)
    finally:
        gw.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
