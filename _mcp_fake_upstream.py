"""A made-up GitHub-style remote MCP server for the gateway's tests: Streamable HTTP, answers as JSON or as an event
stream, hands out an `Mcp-Session-Id`, records every request's headers. Nothing real is contacted; the token is made up."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_TOKEN = "github_pat_FAKE0000TOKEN1111FOR2222TESTS3333ONLY4444"

TOOLS = [
    {"name": "list_issues", "description": "List issues", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "get_me", "description": "Who am I", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "create_issue", "description": "Create an issue", "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
     "annotations": {"readOnlyHint": False}},
]


EXTRA = [{"name": n, "description": n, "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}
         for n in ("boom", "toolfail", "leak", "crash500")]


class Upstream:
    """`mode`: json | sse. `honor_readonly`: drop write tools when X-MCP-Readonly is sent (GitHub does)."""

    def __init__(self, mode="json", token=FAKE_TOKEN, honor_readonly=True, page_size=0, extra=False, hang=False):
        self.mode, self.token, self.honor_readonly, self.page_size = mode, token, honor_readonly, page_size
        self.tools = TOOLS + (EXTRA if extra else [])
        self.hang = hang                   # accept the request and never answer (until stop())
        self.release = threading.Event()
        self.requests = []                 # {"method", "headers", "body"}
        self.sessions = 0
        self.expire_next = False           # the next request with a session id gets 404 once
        self.server = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                outer.requests.append({"method": "DELETE", "headers": {k.lower(): v for k, v in self.headers.items()}, "body": None})
                self._send(200)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                msg = json.loads(raw or b"{}")
                outer.requests.append({"method": "POST", "headers": {k.lower(): v for k, v in self.headers.items()}, "body": msg})
                if outer.hang:
                    outer.release.wait(30)
                    return
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    return self._send(401, b'{"message":"Bad credentials"}')
                m = msg.get("method")
                if m == "initialize":
                    outer.sessions += 1
                    res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-github", "version": "1"}}
                    return self._reply(msg, res, {"Mcp-Session-Id": f"sess-{outer.sessions}"})
                if not self.headers.get("Mcp-Session-Id"):
                    return self._send(400, b'{"message":"no session"}')
                if outer.expire_next and m != "notifications/initialized":
                    outer.expire_next = False
                    return self._send(404, b'{"message":"session gone"}')
                if "id" not in msg:
                    return self._send(202)
                if m == "tools/list":
                    tools = outer.tools
                    if outer.honor_readonly and self.headers.get("X-MCP-Readonly") == "true":
                        tools = [t for t in outer.tools if (t.get("annotations") or {}).get("readOnlyHint")]
                    cursor = (msg.get("params") or {}).get("cursor")
                    if outer.page_size:
                        start = int(cursor or 0)
                        page = tools[start:start + outer.page_size]
                        res = {"tools": page}
                        if start + outer.page_size < len(tools):
                            res["nextCursor"] = str(start + outer.page_size)
                        return self._reply(msg, res)
                    return self._reply(msg, {"tools": tools})
                if m == "tools/call":
                    p = msg.get("params") or {}
                    name, args = p.get("name"), p.get("arguments")
                    if name == "boom":
                        return self._reply(msg, None, error={"code": -32000, "message": "upstream exploded"})
                    if name == "toolfail":
                        return self._reply(msg, {"content": [{"type": "text", "text": "not found"}], "isError": True})
                    if name == "leak":                   # a hostile answer that repeats the token
                        return self._reply(msg, {"content": [{"type": "text", "text": f"Bearer {outer.token} is yours"}]})
                    if name == "crash500":
                        return self._send(500, f"internal error for {outer.token}".encode(), "text/plain")
                    return self._reply(msg, {"content": [{"type": "text", "text": json.dumps({"tool": name, "args": args})}], "isError": False})
                if m == "ping":
                    return self._reply(msg, {})
                return self._reply(msg, None, error={"code": -32601, "message": "method not found"})

            def _reply(self, msg, result, extra=None, error=None):
                body = {"jsonrpc": "2.0", "id": msg.get("id")}
                body.update({"error": error} if error else {"result": result})
                if outer.mode == "sse":
                    note = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "hi"}}
                    sse = f"event: message\ndata: {json.dumps(note)}\n\nevent: message\ndata: {json.dumps(body)}\n\n".encode()
                    return self._send(200, sse, "text/event-stream", extra)
                return self._send(200, json.dumps(body).encode(), "application/json", extra)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
