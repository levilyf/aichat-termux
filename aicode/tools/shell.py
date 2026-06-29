"""Shell execution tool — runs bash commands with an approval gate."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from .base import Tool, ToolResult

# Commands that are safe enough to auto-run without asking
SAFE_AUTO = {
    "ls", "pwd", "cat", "head", "tail", "echo", "wc", "find", "grep", "rg",
    "git", "python", "python3", "pip", "node", "npm", "yarn", "tsc", "ruff",
    "black", "isort", "pytest", "go", "cargo", "rustc", "make", "cmake",
    "tree", "du", "df", "stat", "file", "which", "env", "printenv",
}

# Commands that we should always ask about
DANGEROUS = {"rm", "rmdir", "mv", "chmod", "chown", "dd", "mkfs", "fdisk", "shutdown", "reboot", "kill", "killall", "pkill"}


class ShellTool(Tool):
    name = "shell"
    description = (
        "Execute a shell command and return stdout+stderr. "
        "Commands are run with `bash -c`. Avoid interactive commands. "
        "Use this for running tests, installing packages, git ops, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Bash command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 120},
        },
        "required": ["command"],
    }

    def __init__(self, cwd: str = ".", require_approval: bool = True) -> None:
        self.cwd = cwd
        self.require_approval = require_approval

    def _needs_approval(self, command: str) -> bool:
        if not self.require_approval:
            return False
        try:
            argv = shlex.split(command)
        except ValueError:
            return True
        if not argv:
            return True
        # Walk through pipeline/sequence operators
        for token in shlex.split(command):
            if token in {"rm", "rmdir", "mv", "chmod", "chown", "sudo", "su", "kill", "killall", "pkill", "dd", "mkfs"}:
                return True
        first = argv[0]
        if first in SAFE_AUTO:
            return False
        if first in DANGEROUS:
            return True
        # Unknown command — ask
        return True

    def run(self, command: str, timeout: int = 120, force: bool = False, **_: Any) -> ToolResult:
        needs = self._needs_approval(command) and not force
        if needs and self.require_approval:
            return ToolResult(
                success=True,
                output=f"[requires approval] {command}",
                requires_confirmation=True,
            )
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = result.stdout
            if result.stderr:
                out += ("\n--- stderr ---\n" + result.stderr) if out else result.stderr
            return ToolResult(success=result.returncode == 0, output=out.strip() or "(no output)")
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(False, f"shell error: {e}")
