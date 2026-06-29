"""Base Tool class."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..providers.base import ToolSpec


@dataclass
class ToolResult:
    success: bool
    output: str
    requires_confirmation: bool = False  # if true, the UI should ask before running

    def __str__(self) -> str:
        prefix = "[ok]" if self.success else "[err]"
        return f"{prefix} {self.output}"


class Tool:
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)

    def run(self, **args: Any) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    """Holds a set of tools and dispatches by name."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must define a non-empty `name`")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool '{name}'")
        return self._tools[name]

    def names(self) -> List[str]:
        return sorted(self._tools)

    def specs(self) -> List[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        return tool.run(**args)
