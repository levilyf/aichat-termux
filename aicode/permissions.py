"""Permissions system — fine-grained allow/deny/ask rules per tool.

Like Claude Code's permissions: each tool can be set to 'allow', 'ask', or 'deny'.
Rules can be global, per-tool, or pattern-based (e.g. allow `shell` only for `git *`).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Pattern

from .config import Config


class Permission(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# Sensible defaults — these can be overridden in config.toml under [permissions]
DEFAULT_PERMISSIONS: Dict[str, Permission] = {
    "read_file": Permission.ALLOW,
    "write_file": Permission.ASK,
    "edit_file": Permission.ASK,
    "list_files": Permission.ALLOW,
    "grep": Permission.ALLOW,
    "shell": Permission.ASK,
    "git": Permission.ALLOW,  # git read ops are safe; git write ops are gated inside the tool
}

# Patterns that auto-allow shell commands even when shell=ASK
SAFE_SHELL_PATTERNS = [
    r"^ls\b", r"^pwd\b", r"^cat\b", r"^head\b", r"^tail\b", r"^echo\b",
    r"^wc\b", r"^find\b", r"^grep\b", r"^rg\b", r"^tree\b", r"^du\b",
    r"^df\b", r"^stat\b", r"^file\b", r"^which\b", r"^env\b",
    r"^git (status|diff|log|branch|show|blame)\b",
    r"^python(3)? -m pytest\b", r"^pytest\b",
    r"^ruff check\b", r"^black --check\b", r"^isort --check\b",
    r"^npm (run|test|lint)\b", r"^yarn (test|lint)\b",
    r"^go (test|build|vet)\b", r"^cargo (check|test|build)\b",
    r"^make\b", r"^cmake\b",
]

# Patterns that ALWAYS require approval (override ALLOW)
DANGEROUS_SHELL_PATTERNS = [
    r"\brm\s+-rf?\s+/", r"\bsudo\b", r"\bdd\b", r"\bmkfs\b",
    r"\bshutdown\b", r"\breboot\b", r"git push\b.*--force",
    r"curl\s+.+\|\s*sh", r"wget\s+.+\|\s*sh",
]


@dataclass
class PermissionRule:
    tool: str
    permission: Permission
    pattern: Optional[str] = None  # for shell, a regex pattern that triggers this rule


class PermissionManager:
    """Decides whether a tool call should run, be asked about, or be blocked."""

    def __init__(self, config: Config) -> None:
        self.config = config
        # Load overrides from config.extra if present
        self.overrides: Dict[str, Permission] = self._load_overrides()
        self._safe_patterns: List[Pattern] = [re.compile(p) for p in SAFE_SHELL_PATTERNS]
        self._dangerous_patterns: List[Pattern] = [re.compile(p) for p in DANGEROUS_SHELL_PATTERNS]

    def _load_overrides(self) -> Dict[str, Permission]:
        # Config can have a [permissions] section; if not, use defaults
        # We piggyback on Config.extra — but since Config doesn't have a generic
        # extra dict, we read from the raw TOML if available
        overrides: Dict[str, Permission] = {}
        # For now, use defaults — config extension is a v2 enhancement
        return overrides

    def get_permission(self, tool_name: str, args: dict) -> Permission:
        """Determine the permission for a tool call.

        For `shell`, the command string is inspected against safe/dangerous patterns.
        """
        base = self.overrides.get(tool_name, DEFAULT_PERMISSIONS.get(tool_name, Permission.ASK))

        if tool_name == "shell":
            command = args.get("command", "")
            # Dangerous patterns always escalate to ASK
            for pat in self._dangerous_patterns:
                if pat.search(command):
                    return Permission.ASK
            # Safe patterns downgrade to ALLOW
            if base == Permission.ASK:
                for pat in self._safe_patterns:
                    if pat.search(command):
                        return Permission.ALLOW

        return base

    def should_ask(self, tool_name: str, args: dict) -> bool:
        return self.get_permission(tool_name, args) == Permission.ASK

    def should_deny(self, tool_name: str, args: dict) -> bool:
        return self.get_permission(tool_name, args) == Permission.DENY

    def summary(self) -> str:
        lines = ["**Permission rules:**", ""]
        for tool, perm in sorted(DEFAULT_PERMISSIONS.items()):
            lines.append(f"- `{tool}` → {perm.value}")
        lines.append("")
        lines.append("**Shell auto-allow patterns:**")
        for p in SAFE_SHELL_PATTERNS[:5]:
            lines.append(f"- `{p}`")
        lines.append(f"- ... and {len(SAFE_SHELL_PATTERNS) - 5} more")
        lines.append("")
        lines.append("**Always-ask patterns (override allow):**")
        for p in DANGEROUS_SHELL_PATTERNS:
            lines.append(f"- `{p}`")
        return "\n".join(lines)
