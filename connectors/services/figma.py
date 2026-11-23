"""Figma: its remote MCP server added to the AI programs Figma lets register (Claude Code, Codex, Cursor), one account
folder at a time (connectors/remote_mcp.py does the work; the steps, the words and the FAQ are in figma.json).

  add         put `figma` into every folder of those programs (never replaces an entry: same address counts as added)
  figma_added / figma_login   the two checks: where it is, and where the person is signed in (read-only)
  test        a temporary tab of the primary bot's AI (Claude Code or Codex) calls Figma's `whoami` once

The sign-in is each program's own; ShellBot never holds, reads or keeps it.
"""

import sf_lang
from connectors import clis as clis_mod, remote_mcp
from connectors.services._tab_test import TabTestAdapter

ID = "figma"
ROUTE = "remote"
MARK = "FIGMA-TEST"
SERVER = "figma"
PREFIXES = (f"mcp__{SERVER}__", f"mcp__plugin_{SERVER}_{SERVER}__")      # Claude Code's own server, and the one its plugin brings
AUTH_TOOLS = tuple(p + n for p in PREFIXES for n in ("authenticate", "complete_authentication"))
TEST_CLIS = ("claude", "codex")
FAILED_STEP = {"no_tools": "f1", "not_ready": "f1", "needs_auth": "f2", "failed": "f2"}
BINARY = {"claude": "claude", "codex": "codex", "cursor": "cursor-agent"}


def remote_of(ctx):
    """The route's server as the description says it: (name, url)."""
    from connectors import registry
    r = registry.default().route(ID, ROUTE)["remote"]
    return r["name"], r["url"]


def make_remote(ctx) -> "remote_mcp.Remote":
    """The Remote for this call: the programs are run and found the way the core does it (ctx.run, ctx.find)."""
    return remote_mcp.Remote(ctx.run, lambda cli: ctx.find(BINARY.get(cli, cli)), env=dict(ctx.env or {}), cfg=ctx.cfg)


def action_add(ctx, args: dict) -> dict:
    name, url = remote_of(ctx)
    rows = make_remote(ctx).add_all(name, url)
    here = [r for r in rows if r["state"] != "missing"]
    placed = [r for r in here if r["state"] in ("added", "present")]
    if not here:
        state = "no_cli"
    elif not placed:
        state = "conflict" if all(r["state"] == "conflict" for r in here) else "failed"
    elif len(placed) < len(here):
        state = "partial"
    else:
        state = "added" if any(r["state"] == "added" for r in placed) else "present"
    return {"state": state, "done": len(placed), "total": len(here), "rows": remote_mcp.rows_text(rows)}


def check_added(ctx, args: dict) -> dict:
    """Is Figma in at least one place, and is every folder that is there at the address? Read-only."""
    name, url = remote_of(ctx)
    rows = make_remote(ctx).check_all(name, url)
    here = [r for r in rows if r["state"] != "missing"]
    have = [r for r in here if r["state"] in ("signed_in", "needs_login", "unverified", "failed")]
    if not here:
        state = "no_cli"
    elif len(have) == len(here):
        state = "added"
    elif have:
        state = "partial"
    elif any(r["state"] == "conflict" for r in here):
        state = "conflict"
    else:
        state = "not_added"
    return {"state": state, "have": len(have), "total": len(here), "rows": remote_mcp.rows_text(rows)}


def check_login(ctx, args: dict) -> dict:
    """Where the person is signed in: the sign-in state each program reports (Cursor's cannot be read)."""
    name, url = remote_of(ctx)
    rows = make_remote(ctx).check_all(name, url)
    n = lambda s: sum(1 for r in rows if r["state"] == s)
    present = n("signed_in") + n("needs_login") + n("unverified")
    if not present:
        state = "not_added"
    elif n("needs_login"):
        state = "needs_login"
    elif n("signed_in"):
        state = "ok"
    else:
        state = "unverified"
    return {"state": state, "signed": n("signed_in"), "need": n("needs_login"),
            "total": len([r for r in rows if r["state"] != "missing"]), "rows": remote_mcp.rows_text(rows)}


class FigmaAdapter(TabTestAdapter):
    """The test asks a temporary tab of the primary bot's CLI to call whoami once, and never to repeat the answer."""

    def __init__(self):
        super().__init__(ID, PREFIXES[0], MARK, ("Figma", "Figma"), ROUTE, FAILED_STEP, auth_tools=AUTH_TOOLS, prefixes=PREFIXES, clis=TEST_CLIS)
        self.checks = {"figma_added": check_added, "figma_login": check_login}
        self.actions = {"add": action_add}

    def pick_cli(self, ctx) -> str:
        cli = clis_mod.preferred_cli(getattr(ctx, "cfg", None))
        return cli if cli in TEST_CLIS else "claude"

    def prompt(self, cli: str = "claude") -> str:
        """One line, in the OS language. Figma's tools can change files, so it names the one tool to call and forbids the rest,
        and asks that the answer (a name, an email) is not repeated."""
        if cli == "codex":
            en_load, zh_load = f"Use the Figma tools of the {SERVER} MCP server to", f"用 {SERVER} 這個 MCP 伺服器的 Figma 工具，"
        else:
            en_load, zh_load = "First load the Figma tools with ToolSearch, then", "先用 ToolSearch 載入 Figma 的工具，"
        en = (f"This is a Figma connection test. {en_load} call whoami exactly once. Do not call any other Figma tool, do not open, "
              "create, change or delete any file, and do not repeat the name, email or any other detail it returns. Report only "
              f"whether the call worked, in one line. Then write a last line on its own: {MARK}: OK, or {MARK}: FAIL followed by the reason.")
        zh = (f"這是 Figma 連結測試。{zh_load}只呼叫一次 whoami。不要呼叫其他 Figma 工具，不要打開、新增、修改或刪除任何檔案，"
              "回覆裡也不要重複它回傳的名字、email 或任何細節。用一行只回報呼叫有沒有成功。"
              f"最後單獨一行寫 {MARK}: OK，失敗就寫 {MARK}: FAIL 加原因。")
        return sf_lang.pick(en, zh)


ADAPTER = FigmaAdapter()
