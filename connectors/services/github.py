"""GitHub on the connector core: the adapter. The steps, the words and the FAQ are in github.json.

One route, `token`: a fine-grained personal access token the person makes, kept in the system key store
(`secret_set`), and ShellBot's own MCP gateway (`shellbot_mcp.py`, server `shellbot-gateway`) added by the core's
`cli_add` to every AI program that is installed, in every account folder (`"targets": "all"`). The gateway reads the
token from the key store itself and talks to GitHub's remote MCP server; nothing secret is in any AI's settings.

What the description cannot say for itself lives here: the connection test, a temporary tab of the primary bot's CLI
asked for one small read-only GitHub call (`_tab_test.py`). The test counts only the calls of `github_…` tools: a call
of `shellbot_status` means the gateway could not reach GitHub (no token, refused), which is the verdict `needs_auth`.

  * Never reads, stores, logs or shows the token: it is only asked about (is it in the key store).
  * The test asks for a count and forbids opening anything or changing anything.
"""

import sf_lang
from connectors import secrets
from connectors.services._tab_test import TabTestAdapter

ID = "github"
SERVER = "shellbot-gateway"
MARK = "GITHUB-TEST"
PREFIX = f"mcp__{SERVER}__github_"
PREFIXES = (PREFIX, PREFIX.replace("-", "_"))                  # Codex may write the server's `-` as `_`
STATUS = tuple(p.replace("github_", "shellbot_status") for p in PREFIXES)
TEST_CLIS = ("claude", "codex")
FAILED_STEP = {"no_tools": "g2", "needs_auth": "g1", "failed": "g1"}      # the step a verdict sends the person back to


class GithubAdapter(TabTestAdapter):
    def __init__(self):
        super().__init__(ID, PREFIX, MARK, ("GitHub", "GitHub"), "token", FAILED_STEP, auth_tools=STATUS, prefixes=PREFIXES, clis=TEST_CLIS)

    def pick_cli(self, ctx) -> str:
        """The CLI the primary bot runs on, when its transcript can be read; else Claude."""
        from connectors import clis as clis_mod
        cli = clis_mod.preferred_cli(getattr(ctx, "cfg", None))
        return cli if cli in TEST_CLIS else "claude"

    def prompt(self, cli: str = "claude") -> str:
        """One line, in the OS language. Claude Code loads MCP tools on demand (ToolSearch); Codex has them from the
        start, so its sentence names the server."""
        if cli == "codex":
            en_load, zh_load = f"Use the GitHub tools (github_…) of the {SERVER} MCP server to", f"用 {SERVER} 這個 MCP 伺服器的 GitHub 工具（github_…），"
        else:
            en_load, zh_load = f"First load the GitHub tools of the {SERVER} MCP server with ToolSearch, then", f"先用 ToolSearch 載入 {SERVER} 這個 MCP 伺服器的 GitHub 工具，"
        en = (f"This is a GitHub connection test. {en_load} make one read-only call that lists something with at most 1 result "
              "(for example github_search_repositories with perPage 1) and count what comes back. Do not open any file, issue, "
              "pull request or other content, do not call shellbot_status, do not create or change anything, and do not repeat any "
              f"name or content in your reply. Report only the number, in one line. Then write a last line on its own: {MARK}: OK, "
              f"or {MARK}: FAIL followed by the reason.")
        zh = (f"這是 GitHub 連結測試。{zh_load}只做一次唯讀呼叫，列出最多 1 筆東西（例如 github_search_repositories，perPage 設 1），"
              "數一下回來幾筆。不要打開任何檔案、issue、pull request 或其他內容，不要呼叫 shellbot_status，不要新增或修改任何東西，"
              f"回覆裡也不要重複任何名稱或內容。用一行只回報數字。最後單獨一行寫 {MARK}: OK，失敗就寫 {MARK}: FAIL 加原因。")
        return sf_lang.pick(en, zh)

    def health(self, ctx, route) -> dict:
        """Is the token still in the key store (it is not opened here; GitHub is not asked: a token that has expired is found
        by the test and by the gateway's status tool)."""
        try:
            return {"state": "ok" if ctx.secrets.has("token") else "revoked"}
        except secrets.Unsupported:
            return {"state": "unknown"}


ADAPTER = GithubAdapter()
