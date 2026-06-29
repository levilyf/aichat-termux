"""MCP tool wrapper — bridges MCP server tools into our ToolRegistry."""

from __future__ import annotations

from typing import Any

from ..mcp import MCPManager
from .base import Tool, ToolResult


class MCPTool(Tool):
    """A single tool exposed by an MCP server, wrapped as a native Tool."""

    def __init__(self, name: str, description: str, parameters: dict, manager: MCPManager) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._manager = manager

    def run(self, **args: Any) -> ToolResult:
        # MCP calls are async, but Tool.run is sync — we use the sync wrapper
        # that runs the event loop. The agent's _execute_tool is async-aware
        # via the registry, so this path is only hit when called directly.
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context — create a task and block on it
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        lambda: asyncio.run(self._manager.call_tool(self.name, args))
                    ).result()
            return loop.run_until_complete(self._manager.call_tool(self.name, args))
        except RuntimeError:
            return asyncio.run(self._manager.call_tool(self.name, args))

    async def arun(self, **args: Any) -> ToolResult:
        """Async entry point — used by the agent when available."""
        return await self._manager.call_tool(self.name, args)


def register_mcp_tools(registry, manager: MCPManager) -> None:
    """Register all MCP-exposed tools into an existing ToolRegistry."""
    for spec in manager.get_all_tool_specs():
        tool = MCPTool(
            name=spec.name,
            description=spec.description,
            parameters=spec.parameters,
            manager=manager,
        )
        registry.register(tool)
