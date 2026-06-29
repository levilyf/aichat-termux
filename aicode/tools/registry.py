"""Tool registry — default set of tools wired up for the agent."""

from __future__ import annotations

from ..config import Config
from .base import ToolRegistry
from .file_ops import EditFileTool, GrepTool, ListFilesTool, ReadFileTool, WriteFileTool
from .git import GitTool
from .shell import ShellTool


def default_registry(cwd: str, config: Config) -> ToolRegistry:
    """Build the standard tool registry for a working directory + config."""
    reg = ToolRegistry()
    reg.register(ReadFileTool(cwd=cwd))
    reg.register(WriteFileTool(cwd=cwd))
    reg.register(EditFileTool(cwd=cwd))
    reg.register(ListFilesTool(cwd=cwd))
    reg.register(GrepTool(cwd=cwd))
    reg.register(ShellTool(cwd=cwd, require_approval=config.shell.require_approval))
    reg.register(GitTool(cwd=cwd))
    return reg
