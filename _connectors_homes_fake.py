"""A stand-in for every AI program, for the GitHub tests: keeps the servers each account folder has and runs nothing."""
from connectors.clis import Result


class HomeRunner:
    """Stands in for every AI program; keeps the servers each account folder has. `calls`: (cli, op, home, argv, env)."""

    def __init__(self, versions=None):
        self.calls, self.entries, self.versions = [], {}, versions or {}

    @staticmethod
    def home_of(cli, env):
        return (env or {}).get({"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}.get(cli, "-"), "")

    def __call__(self, argv, timeout=40, env=None):
        cli = str(argv[0]).rsplit("/", 1)[-1]
        if argv[1:] == ["--version"]:
            return Result(0, self.versions.get(cli, f"{cli} 1.0.0"))
        op = argv[2]
        name = next((a for a in argv[3:] if str(a).startswith("shellbot-")), "")
        home = self.home_of(cli, env)
        self.calls.append((cli, op, home, list(argv), dict(env or {})))
        have = self.entries.setdefault((cli, home), set())
        if op == "add":
            have.add(name)
            return Result(0, "added")
        if op == "remove":
            if name in have:
                have.discard(name)
                return Result(0, "removed")
            return Result(1, "not found")
        if op == "get":
            return Result(0 if name in have else 1, "")
        if op == "list":
            return Result(0, "\n".join(sorted(have)))
        return Result(1, "")

    def of(self, op, cli=None):
        return [c for c in self.calls if c[1] == op and (cli is None or c[0] == cli)]
