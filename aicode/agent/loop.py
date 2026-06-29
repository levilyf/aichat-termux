"""The agentic loop — drives a Provider through a tool-calling loop.

Events are emitted via the `on_event` callback as the turn progresses.
The UI layer (TUI or exec mode) subscribes by passing `on_event` and
`on_permission_request` callbacks. There is no subclassing — composition
over inheritance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, List, Optional

from ..autocommit import auto_commit_if_needed, is_clean_tree, is_git_repo
from ..config import Config, Profile
from ..cost import SessionCost, TurnCost, parse_usage
from ..mcp import MCPManager
from ..memory import load_memory
from ..permissions import Permission, PermissionManager
from ..providers.base import Message, Provider, Response, ToolCall
from ..providers.registry import Router, build_provider
from ..tools.base import ToolResult
from ..tools.registry import default_registry
from ..tools.shell import ShellTool
from .prompts import build_system_prompt


class AgentEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPLETE = "complete"
    ERROR = "error"
    PROFILE_SWITCHED = "profile_switched"
    PLAN = "plan"
    COST_UPDATE = "cost_update"


@dataclass
class AgentEvent:
    type: AgentEventType
    text: str = ""
    tool_call: Optional[ToolCall] = None
    tool_result: Optional[ToolResult] = None
    profile: Optional[str] = None
    error: Optional[str] = None
    cost: Optional[TurnCost] = None
    plan: Optional[str] = None


# Type of the permission-request callback the UI must supply.
# Returns True to allow, False to deny.
PermissionCallback = Callable[[str, dict], Awaitable[bool]]

MAX_ITERATIONS = 12


class Agent:
    """Stateful agent that drives a Provider through a tool-calling loop."""

    def __init__(
        self,
        config: Config,
        cwd: str = ".",
        profile: Optional[Profile] = None,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
        on_permission_request: Optional[PermissionCallback] = None,
        plan_mode: bool = False,
        session_id: Optional[str] = None,
        mcp_manager: Optional[MCPManager] = None,
    ) -> None:
        self.config = config
        self.cwd = cwd
        self.router = Router(config)
        self.pinned_profile: Optional[Profile] = profile
        self.current_profile: Optional[Profile] = profile
        self.history: List[Message] = []
        self.tools = default_registry(cwd=cwd, config=config)
        self.on_event: Callable[[AgentEvent], None] = on_event or (lambda e: None)
        self.on_permission_request: Optional[PermissionCallback] = on_permission_request
        self._provider: Optional[Provider] = None

        self.plan_mode = plan_mode
        self.permissions = PermissionManager(config)
        self.session_cost = SessionCost(started_at=time.time())
        self.session_id = session_id
        self.mcp_manager = mcp_manager

        if mcp_manager and mcp_manager.is_running():
            from ..tools.mcp_tool import register_mcp_tools
            register_mcp_tools(self.tools, mcp_manager)

        self._files_modified_this_turn = False

        self.history.append(Message(role="system", content=self._build_system_prompt()))

    def _build_system_prompt(self) -> str:
        """Construct the system prompt with memory + plan mode + custom commands."""
        prompt = build_system_prompt(self.cwd, self.tools.names())
        memory = load_memory(self.cwd)
        if memory:
            prompt += f"\n\n## Project Memory\n\n{memory}"
        if self.config.commands:
            cmd_list = ", ".join(f"`/{name}`" for name in self.config.commands)
            prompt += f"\n\n## Custom commands available\nThe user has these custom slash commands: {cmd_list}. Mention them if relevant."
        if self.plan_mode:
            prompt += (
                "\n\n## PLAN MODE ACTIVE\n"
                "You are in plan mode. Do NOT execute any tools. Instead:\n"
                "1. Read files to understand the codebase\n"
                "2. Produce a clear, numbered plan of what you would do\n"
                "3. Wait for the user to approve before any changes are made\n"
                "Output your plan in a section titled '## Plan'."
            )
        return prompt

    def _emit(self, event: AgentEvent) -> None:
        self.on_event(event)

    async def _get_provider(self, user_text: str) -> Provider:
        if self.pinned_profile:
            profile = self.pinned_profile
        else:
            profile = self.router.for_text(user_text)
            if self.router.last_warning:
                self._emit(AgentEvent(type=AgentEventType.ERROR, error=self.router.last_warning))
        if profile is not self.current_profile:
            self.current_profile = profile
            self._emit(AgentEvent(type=AgentEventType.PROFILE_SWITCHED, profile=profile.name))
            if self._provider:
                await self._provider.close()
                self._provider = None
        if self._provider is None:
            self._provider = build_provider(profile)
        return self._provider

    async def pin_profile(self, name: str) -> None:
        profile = self.config.get_profile(name)
        self.pinned_profile = profile
        self.current_profile = profile
        if self._provider:
            await self._provider.close()
            self._provider = build_provider(profile)
        self._emit(AgentEvent(type=AgentEventType.PROFILE_SWITCHED, profile=name))

    async def unpin(self) -> None:
        self.pinned_profile = None

    def toggle_plan_mode(self, enabled: Optional[bool] = None) -> bool:
        self.plan_mode = enabled if enabled is not None else not self.plan_mode
        if self.history and self.history[0].role == "system":
            self.history[0] = Message(role="system", content=self._build_system_prompt())
        return self.plan_mode

    def reset_history(self) -> None:
        self.history = [Message(role="system", content=self._build_system_prompt())]

    async def _ask_permission(self, tool_name: str, args: dict) -> bool:
        """Ask the UI layer whether a tool call should proceed.

        Defaults to DENY when no callback is registered (safer than auto-allowing).
        """
        if self.on_permission_request is None:
            return False
        try:
            return await self.on_permission_request(tool_name, args)
        except Exception as e:
            self._emit(AgentEvent(type=AgentEventType.ERROR, error=f"permission callback failed: {e}"))
            return False

    async def chat(self, user_text: str) -> None:
        """Run one full agentic turn — may execute multiple tool calls.

        Emits AgentEvent objects via `on_event` as it progresses. Returns when
        the turn is complete (model finished without tool calls, hit the
        iteration cap, or hit an unrecoverable error).
        """
        self.history.append(Message(role="user", content=user_text))
        self._files_modified_this_turn = False

        was_clean = is_clean_tree(self.cwd) if is_git_repo(self.cwd) else False

        for _ in range(MAX_ITERATIONS):
            provider = await self._get_provider(user_text)
            text_buf: List[str] = []

            def on_delta(chunk: str) -> None:
                text_buf.append(chunk)
                self._emit(AgentEvent(type=AgentEventType.TEXT_DELTA, text=chunk))

            try:
                response: Response = await provider.chat(
                    messages=self.history,
                    tools=self.tools.specs() if not self.plan_mode else [],
                    on_delta=on_delta,
                    temperature=0.2,
                    max_tokens=4096,
                )
            except Exception as e:
                self._emit(AgentEvent(type=AgentEventType.ERROR, error=str(e)))
                return

            if response.usage:
                turn_cost = parse_usage(response.usage, self.current_profile.model if self.current_profile else "")
                self.session_cost.add(turn_cost)
                self._emit(AgentEvent(type=AgentEventType.COST_UPDATE, cost=turn_cost))

            assistant_content = "".join(text_buf) or response.content
            self.history.append(Message(
                role="assistant",
                content=assistant_content,
                tool_calls=response.tool_calls,
            ))

            if self.plan_mode and "## Plan" in assistant_content:
                plan_section = assistant_content.split("## Plan", 1)[-1].strip()
                self._emit(AgentEvent(type=AgentEventType.PLAN, plan=plan_section))

            if not response.tool_calls:
                self._emit(AgentEvent(type=AgentEventType.COMPLETE, text=assistant_content))
                self._maybe_auto_commit(was_clean)
                return

            # Execute each tool call
            for tc in response.tool_calls:
                self._emit(AgentEvent(type=AgentEventType.TOOL_CALL, tool_call=tc))

                perm = self.permissions.get_permission(tc.name, tc.arguments)
                if perm == Permission.DENY:
                    self._record_tool_result(tc, "blocked by permissions", success=False)
                    continue
                if perm == Permission.ASK:
                    approved = await self._ask_permission(tc.name, tc.arguments)
                    if not approved:
                        self._record_tool_result(tc, "user denied permission", success=False)
                        continue

                result = await self._execute_tool(tc)
                self._emit(AgentEvent(type=AgentEventType.TOOL_RESULT, tool_result=result, tool_call=tc))

                if tc.name in {"write_file", "edit_file"} and result.success:
                    self._files_modified_this_turn = True

                self.history.append(Message(
                    role="tool",
                    content=result.output[:8000],
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

                # Shell tool may signal it needs approval — ask via callback
                if result.requires_confirmation:
                    approved = await self._ask_permission("shell", tc.arguments)
                    if not approved:
                        self._emit(AgentEvent(type=AgentEventType.COMPLETE, text="User denied the command."))
                        return
                    shell = self.tools.get("shell")
                    if isinstance(shell, ShellTool):
                        result = shell.run(tc.arguments.get("command", ""), force=True)
                        self._emit(AgentEvent(type=AgentEventType.TOOL_RESULT, tool_result=result, tool_call=tc))

        self._emit(AgentEvent(
            type=AgentEventType.COMPLETE,
            text=f"Stopped after {MAX_ITERATIONS} tool iterations. Use /clear and ask more specifically.",
        ))
        self._maybe_auto_commit(was_clean)

    def _record_tool_result(self, tc: ToolCall, message: str, success: bool) -> None:
        """Emit a synthetic tool result and append it to history."""
        result = ToolResult(success=success, output=message)
        self._emit(AgentEvent(type=AgentEventType.TOOL_RESULT, tool_result=result, tool_call=tc))
        self.history.append(Message(
            role="tool",
            content=message,
            tool_call_id=tc.id,
            name=tc.name,
        ))

    def _maybe_auto_commit(self, was_clean_before: bool) -> None:
        try:
            msg = auto_commit_if_needed(
                self.cwd,
                self.config.auto_commit,
                self._files_modified_this_turn,
                was_clean_before,
            )
            if msg:
                self._emit(AgentEvent(
                    type=AgentEventType.COMPLETE,
                    text=f"Auto-committed: {msg}",
                ))
        except Exception as e:
            self._emit(AgentEvent(type=AgentEventType.ERROR, error=f"auto-commit failed: {e}"))

    async def _execute_tool(self, tc: ToolCall) -> ToolResult:
        if tc.name.startswith("mcp_") and self.mcp_manager:
            return await self.mcp_manager.call_tool(tc.name, tc.arguments)
        try:
            return self.tools.execute(tc.name, tc.arguments)
        except KeyError:
            return ToolResult(success=False, output=f"unknown tool: {tc.name}")
        except Exception as e:
            return ToolResult(success=False, output=f"tool error: {e}")

    async def compact(self) -> str:
        """Summarize the conversation to save context. Returns the summary."""
        if not self._provider:
            profile = self.current_profile or self.config.profiles.get(self.config.routing.default)
            if profile is None:
                return "compact failed: no provider available"
            self._provider = build_provider(profile)
        history_text = "\n\n".join(
            f"[{m.role}] {m.content[:500]}" for m in self.history[1:] if m.content
        )
        summary_prompt = (
            "Summarize this conversation concisely. Preserve:\n"
            "- Key decisions made\n"
            "- Files created/modified (with paths)\n"
            "- Open tasks or TODOs\n"
            "- Any important context\n\n"
            f"Conversation:\n{history_text}\n\n"
            "Summary:"
        )
        try:
            resp = await self._provider.chat(
                messages=[Message(role="user", content=summary_prompt)],
                tools=None,
                temperature=0.0,
                max_tokens=1024,
            )
            summary = resp.content
            self.reset_history()
            self.history.append(Message(role="user", content=f"Previous conversation summary:\n{summary}"))
            self.history.append(Message(role="assistant", content="Understood. I have the context from the previous conversation."))
            return summary
        except Exception as e:
            return f"compact failed: {e}"

    async def close(self) -> None:
        if self._provider:
            await self._provider.close()
