"""MCP (Model Context Protocol) client — connects to external tool servers.

Spawns each configured MCP server as a subprocess, speaks JSON-RPC 2.0 over
stdio, and exposes its tools through the standard ToolRegistry so the agent
can call them transparently alongside the built-in tools.

Protocol reference: https://modelcontextprotocol.io/specification
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from .tools.base import Tool, ToolResult, ToolSpec


class MCPClient:
    """Manages a single MCP server subprocess and communicates via JSON-RPC."""

    def __init__(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._pending: Dict[int, "asyncio.Future[Any]"] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._tools: List[Dict[str, Any]] = []

    async def start(self) -> None:
        """Spawn the MCP server subprocess and do the initial handshake."""
        full_env = dict(os.environ)
        full_env.update(self.env)
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
        )
        # Start the reader loop
        self._reader_task = asyncio.create_task(self._read_loop())
        # Initialize handshake
        await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "aicode", "version": "0.1.0"},
        })
        # Send initialized notification
        await self._notify("notifications/initialized", {})
        # Discover tools
        result = await self._call("tools/list", {})
        self._tools = result.get("tools", []) if isinstance(result, dict) else []

    async def _read_loop(self) -> None:
        """Read JSON-RPC messages from the server's stdout."""
        assert self._proc and self._proc.stdout
        while True:
            try:
                line = await self._proc.stdout.readline()
            except (asyncio.CancelledError, ConnectionResetError):
                break
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            # Is this a response to a pending request?
            if "id" in msg and msg["id"] in self._pending:
                fut = self._pending.pop(msg["id"])
                if "error" in msg:
                    fut.set_exception(RuntimeError(f"MCP error: {msg['error']}"))
                else:
                    fut.set_result(msg.get("result"))
            # Notifications from server are ignored for now
        # Process exited
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("MCP server closed"))

    async def _call(self, method: str, params: Dict[str, Any]) -> Any:
        """Send a JSON-RPC request and await the response."""
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("MCP server not started")
        self._request_id += 1
        req_id = self._request_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        data = (json.dumps(msg) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()
        return await asyncio.wait_for(fut, timeout=30.0)

    async def _notify(self, method: str, params: Dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._proc or not self._proc.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        data = (json.dumps(msg) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Call a tool exposed by this MCP server."""
        try:
            result = await self._call("tools/call", {"name": name, "arguments": arguments})
            if not isinstance(result, dict):
                return ToolResult(False, f"unexpected MCP response: {result}")
            # MCP returns content as a list of {type: "text", text: "..."}
            content = result.get("content", [])
            if isinstance(content, list):
                text = "\n".join(
                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                    for c in content
                )
            else:
                text = str(content)
            is_error = result.get("isError", False)
            return ToolResult(success=not is_error, output=text)
        except Exception as e:
            return ToolResult(False, f"MCP call failed: {e}")

    def get_tool_specs(self) -> List[ToolSpec]:
        """Convert MCP tool definitions to our ToolSpec format."""
        specs: List[ToolSpec] = []
        for t in self._tools:
            name = t.get("name", "")
            if not name:
                continue
            # Prefix with server name to avoid collisions
            full_name = f"mcp_{self.name}_{name}"
            schema = t.get("inputSchema", {"type": "object", "properties": {}})
            specs.append(ToolSpec(
                name=full_name,
                description=t.get("description", f"MCP tool: {name}"),
                parameters=schema,
            ))
        return specs

    def get_tool_names(self) -> List[str]:
        """Return the prefixed tool names this server exposes."""
        return [s.name for s in self.get_tool_specs()]

    async def stop(self) -> None:
        """Shut down the MCP server subprocess."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
            self._proc = None


class MCPManager:
    """Manages all configured MCP servers + exposes their tools to the agent."""

    def __init__(self, mcp_config: Dict[str, Dict[str, Any]]) -> None:
        self.config = mcp_config
        self._clients: Dict[str, MCPClient] = {}

    async def start_all(self) -> Dict[str, List[str]]:
        """Start all configured MCP servers. Returns {server_name: [tool_names]}."""
        results: Dict[str, List[str]] = {}
        for name, cfg in self.config.items():
            client = MCPClient(
                name=name,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
            )
            try:
                await client.start()
                self._clients[name] = client
                results[name] = client.get_tool_names()
            except Exception as e:
                # Don't fail hard — just skip this server
                results[name] = [f"(failed to start: {e})"]
        return results

    def get_all_tool_specs(self) -> List[ToolSpec]:
        """Return ToolSpecs for all tools across all running MCP servers."""
        specs: List[ToolSpec] = []
        for client in self._clients.values():
            specs.extend(client.get_tool_specs())
        return specs

    def get_tool_names(self) -> List[str]:
        return [s.name for s in self.get_all_tool_specs()]

    async def call_tool(self, full_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Route a tool call to the right MCP server based on the prefixed name."""
        # full_name = mcp_<server>_<tool>
        if not full_name.startswith("mcp_"):
            return ToolResult(False, f"not an MCP tool: {full_name}")
        rest = full_name[4:]
        # Find the matching server by trying prefixes
        for server_name, client in self._clients.items():
            prefix = f"{server_name}_"
            if rest.startswith(prefix):
                tool_name = rest[len(prefix):]
                return await client.call_tool(tool_name, arguments)
        return ToolResult(False, f"MCP server not found for tool: {full_name}")

    def get_client(self, name: str) -> Optional[MCPClient]:
        return self._clients.get(name)

    async def stop_all(self) -> None:
        for client in list(self._clients.values()):
            await client.stop()
        self._clients.clear()

    def is_running(self) -> bool:
        return bool(self._clients)
