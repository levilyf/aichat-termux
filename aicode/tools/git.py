"""Git tool — wraps common git operations, all marked safe to auto-run."""

from __future__ import annotations

import subprocess
from typing import Any

from .base import Tool, ToolResult


class GitTool(Tool):
    name = "git"
    description = (
        "Run a git subcommand. Use this for `status`, `diff`, `log`, `branch`, `add`, "
        "`commit -m`, `checkout`, `pull`, `push` etc. Avoid interactive commands like `git rebase -i`."
    )
    parameters = {
        "type": "object",
        "properties": {
            "args": {"type": "string", "description": "Arguments to pass to git (e.g. 'status --short')"},
        },
        "required": ["args"],
    }

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def run(self, args: str, **_: Any) -> ToolResult:
        # Disallow clearly interactive commands
        blocked = ["rebase -i", "commit --amend", "add -p"]
        for b in blocked:
            if b in args:
                return ToolResult(False, f"blocked interactive git op: {args}")
        try:
            result = subprocess.run(
                ["git", *args.split()],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            out = result.stdout
            if result.stderr:
                out += ("\n" + result.stderr) if out else result.stderr
            return ToolResult(success=result.returncode == 0, output=out.strip() or "(no output)")
        except subprocess.TimeoutExpired:
            return ToolResult(False, "git timed out")
        except Exception as e:
            return ToolResult(False, f"git error: {e}")
