"""Stand-ins for the remote-MCP tests: Claude Code, Codex and Cursor as they answered on 2026-11-23 (Claude Code 2.1.329
`mcp get`, Codex 0.142.1 `mcp list` and `mcp add --url`), with a state per account folder, and a made-up home and app folder.
Nothing real is run or read."""
import tempfile
from pathlib import Path

from connectors.clis import Result

URL = "https://mcp.figma.com/mcp"


def tmp():
    return Path(tempfile.mkdtemp(prefix="sf-remote-mcp-"))


# What the real programs printed (lowercased, as the runner gives it).
CLAUDE_GET = ("figma:\n  scope: user config (available in all your projects)\n  status: {status}\n  type: http\n  url: {url}\n\n"
              "to remove this server, run: claude mcp remove figma -s user\n")
CLAUDE_MISSING = 'no mcp server named "figma". run `claude mcp add` to add one.\n'
CODEX_HEAD = "name   url                        bearer token env var  status   auth \n"
CODEX_ROW = "figma  {url}  -                     enabled  {auth}\n"


class FakeProgram:
    """Claude Code, Codex and Cursor's `mcp` commands, with a state per account folder (the env's CLAUDE_CONFIG_DIR / CODEX_HOME)."""

    def __init__(self):
        self.calls = []                  # (cli, folder, argv[1:])
        self.state = {}                  # (cli, folder) -> {"url", "status"/"auth"}
        self.silent = set()              # (cli, folder) that does not answer
        self.refuse_add = set()

    @staticmethod
    def where(argv, env):
        cli = str(argv[0]).rsplit("/", 1)[-1]
        return cli, (env or {}).get({"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}.get(cli, ""), "") or "default"

    def __call__(self, argv, timeout=40, env=None):
        cli, folder = self.where(argv, env)
        self.calls.append((cli, folder, list(argv[1:])))
        if (cli, folder) in self.silent:
            return Result(-2, "")
        have = self.state.get((cli, folder))
        if cli == "claude" and argv[2] == "get":
            if not have:
                return Result(1, CLAUDE_MISSING)
            return Result(0, CLAUDE_GET.format(status=have.get("status", "! needs authentication"), url=have["url"].lower()))
        if cli == "claude" and argv[2] == "add":
            if (cli, folder) in self.refuse_add:
                return Result(1, "error")
            self.state[(cli, folder)] = {"url": argv[-1], "status": "! needs authentication"}
            return Result(0, "added http mcp server")
        if cli == "codex" and argv[2] == "list":
            if not have:
                return Result(0, "no mcp servers configured yet. try `codex mcp add my-tool -- my-command`.\n")
            return Result(0, CODEX_HEAD + CODEX_ROW.format(url=have["url"].lower(), auth=have.get("auth", "oauth")))
        return Result(1, "unknown")

    def launcher(self, argv, env, marker, timeout):
        """`codex mcp add figma --url <url>`: writes the entry, prints 'added', then would wait for a sign-in."""
        cli, folder = self.where(argv, env)
        self.calls.append((cli, folder, list(argv[1:]) + ["<until " + marker + ">"]))
        if (cli, folder) not in self.refuse_add:
            self.state[(cli, folder)] = {"url": argv[-1], "auth": "not logged in"}
        return (cli, folder) not in self.refuse_add


def find(installed=("claude", "codex", "cursor")):
    return lambda cli: f"/fake/bin/{cli}" if cli in installed else None


def world(profiles=(("claude", "claude-aaaa1111"), ("claude", "claude-bbbb2222"), ("codex", "codex-cccc3333")), default=("claude", "codex"), cursor=True):
    """A made-up home and app folder: the default folders, the profiles ShellBot made, ~/.cursor."""
    home, cfg = tmp(), tmp()
    for cli in default:
        (home / {"claude": ".claude", "codex": ".codex"}[cli]).mkdir()
    if cursor:
        (home / ".cursor").mkdir()
    for cli, ref in profiles:
        (cfg / "account-profiles" / cli / ref).mkdir(parents=True)
    return home, cfg
