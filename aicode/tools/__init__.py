"""Tools exposed to the agent.

Every tool subclasses `Tool` and implements `run(args) -> str`. The `spec()` method
returns a JSON-schema description that providers translate into their native tool format.
"""

from .base import Tool, ToolResult
from .file_ops import ReadFileTool, WriteFileTool, EditFileTool, ListFilesTool, GrepTool
from .shell import ShellTool
from .git import GitTool
from .registry import ToolRegistry, default_registry

__all__ = [
    "Tool",
    "ToolResult",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListFilesTool",
    "GrepTool",
    "ShellTool",
    "GitTool",
    "ToolRegistry",
    "default_registry",
]
