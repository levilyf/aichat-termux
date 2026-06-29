"""The agentic loop — Claude Code-style with plan mode, cost tracking, memory, permissions."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Callable, List, Optional

from ..config import Config, Profile
from ..cost import SessionCost, TurnCost, parse_usage
from ..memory import load_memory
from ..permissions import Permission, PermissionManager
from ..providers.base import Message, Provider, Response, ToolCall
from ..providers.registry import Router, build_provider
from ..tools.base import ToolResult
from ..tools.registry import default_registry
from .prompts import build_system_prompt


class AgentEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_CONFIRM = "tool_confirm"
    COMPLETE = "complete"
    ERROR = "error"
    PROFILE_SWITCHED = "profile_switched"
    PLAN = "plan"               # plan mode: model produced a plan
    COST_UPDATE = "cost_update"  # cost updated after each turn
    PERMISSION_ASK = "permission_ask"  # permission needed for a tool


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


class Agent:
    """Stateful agent with plan mode, cost tracking, memory, and permissions."""

    def __init__(
        self,
        config: Config,
        cwd: str = ".",
        profile: Optional[Profile] = None,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
        plan_mode: bool = False,
        session_id: Optional[str] = None,
    ) -> None:
        self.config = config
        self.cwd = cwd
        self.router = Router(config)
        self.pinned_profile: Optional[Profile] = profile
        self.current_profile: Optional[Profile] = profile
        self.history: List[Message] = []
        self.tools = default_registry(cwd=cwd, config=config)
        self.on_event = on_event or (lambda e: None)
        self._provider: Optional[Provider] = None

        # Claude Code-style features
        self.plan_mode = plan_mode
        self.permissions = PermissionManager(config)
        self.session_cost = SessionCost(started_at=time.time())
        self.session_id = session_id

        # Build system prompt with memory
        memory = load_memory(cwd)
        sys_prompt = build_system_prompt(cwd, self.tools.names())
        if memory:
            sys_prompt += f"\n\n## Project Memory\n\n{memory}"
        if plan_mode:
            sys_prompt += (
                "\n\n## PLAN MODE ACTIVE\n"
                "You are in plan mode. Do NOT execute any tools. Instead:\n"
                "1. Read files to understand the codebase\n"
                "2. Produce a clear, numbered plan of what you would do\n"
                "3. Wait for the user to approve before any changes are made\n"
                "Output your plan in a section titled '## Plan'."
            )
        self.history.append(Message(role="system", content=sys_prompt))

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
        """Toggle plan mode on/off. Returns the new state."""
        if enabled is not None:
            self.plan_mode = enabled
        else:
            self.plan_mode = not self.plan_mode
        # Update system prompt
        if self.history and self.history[0].role == "system":
            base_prompt = build_system_prompt(self.cwd, self.tools.names())
            memory = load_memory(self.cwd)
            if memory:
                base_prompt += f"\n\n## Project Memory\n\n{memory}"
            if self.plan_mode:
                base_prompt += (
                    "\n\n## PLAN MODE ACTIVE\n"
                    "You are in plan mode. Do NOT execute any tools. Instead:\n"
                    "1. Read files to understand the codebase\n"
                    "2. Produce a clear, numbered plan of what you would do\n"
                    "3. Wait for the user to approve before any changes are made\n"
                    "Output your plan in a section titled '## Plan'."
                )
            self.history[0] = Message(role="system", content=base_prompt)
        return self.plan_mode

    def reset_history(self) -> None:
        memory = load_memory(self.cwd)
        sys_prompt = build_system_prompt(self.cwd, self.tools.names())
        if memory:
            sys_prompt += f"\n\n## Project Memory\n\n{memory}"
        if self.plan_mode:
            sys_prompt += "\n\n## PLAN MODE ACTIVE\n"
        self.history = [Message(role="system", content=sys_prompt)]

    async def request_approval(self, command: str) -> bool:
        """Override in subclasses to actually ask the user. Default: deny."""
        return False

    async def request_permission(self, tool_name: str, args: dict) -> bool:
        """Override to implement actual permission prompting. Default: follow config."""
        perm = self.permissions.get_permission(tool_name, args)
        if perm == Permission.ALLOW:
            return True
        if perm == Permission.DENY:
            return False
        # ASK — delegate to request_approval (subclasses can override)
        return await self.request_approval(f"{tool_name}({args})")

    async def chat(self, user_text: str) -> AsyncIterator[AgentEvent]:
        """Run one full agentic turn — may execute multiple tool calls."""
        self.history.append(Message(role="user", content=user_text))

        max_iterations = 12
        for _ in range(max_iterations):
            provider = await self._get_provider(user_text)
            text_buf: List[str] = []
            tool_calls: List[ToolCall] = []

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

            # Track cost
            if response.usage:
                turn_cost = parse_usage(response.usage, self.current_profile.model if self.current_profile else "")
                self.session_cost.add(turn_cost)
                self._emit(AgentEvent(type=AgentEventType.COST_UPDATE, cost=turn_cost))

            assistant_content = "".join(text_buf) or response.content
            assistant_msg = Message(
                role="assistant",
                content=assistant_content,
                tool_calls=response.tool_calls,
            )
            self.history.append(assistant_msg)

            # In plan mode, detect "## Plan" section and emit it
            if self.plan_mode and "## Plan" in assistant_content:
                plan_section = assistant_content.split("## Plan", 1)[-1].strip()
                self._emit(AgentEvent(type=AgentEventType.PLAN, plan=plan_section))

            if not response.tool_calls:
                self._emit(AgentEvent(type=AgentEventType.COMPLETE, text=assistant_content))
                return

            # Execute each tool call (with permission check)
            for tc in response.tool_calls:
                self._emit(AgentEvent(type=AgentEventType.TOOL_CALL, tool_call=tc))

                # Permission check
                if self.permissions.should_ask(tc.name, tc.arguments):
                    approved = await self.request_permission(tc.name, tc.arguments)
                    if not approved:
                        result = ToolResult(False, "user denied permission")
                        self._emit(AgentEvent(type=AgentEventType.TOOL_RESULT, tool_result=result, tool_call=tc))
                        self.history.append(
                            Message(role="tool", content="user denied permission", tool_call_id=tc.id, name=tc.name)
                        )
                        continue
                elif self.permissions.should_deny(tc.name, tc.arguments):
                    result = ToolResult(False, "blocked by permissions")
                    self._emit(AgentEvent(type=AgentEventType.TOOL_RESULT, tool_result=result, tool_call=tc))
                    self.history.append(
                        Message(role="tool", content="blocked by permissions", tool_call_id=tc.id, name=tc.name)
                    )
                    continue

                result = await self._execute_tool(tc)
                self._emit(AgentEvent(type=AgentEventType.TOOL_RESULT, tool_result=result, tool_call=tc))
                self.history.append(
                    Message(
                        role="tool",
                        content=result.output[:8000],
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )
                if not result.success and "requires approval" in result.output.lower():
                    self._emit(AgentEvent(type=AgentEventType.COMPLETE, text="Waiting for approval."))
                    return

        self._emit(
            AgentEvent(
                type=AgentEventType.COMPLETE,
                text="(stopped after 12 tool iterations — use /clear and ask more specifically)",
            )
        )

    async def _execute_tool(self, tc: ToolCall) -> ToolResult:
        try:
            result = self.tools.execute(tc.name, tc.arguments)
        except KeyError:
            return ToolResult(False, f"unknown tool: {tc.name}")
        except Exception as e:
            return ToolResult(False, f"tool error: {e}")

        if result.requires_confirmation:
            approved = await self.request_approval(result.output)
            if approved:
                cmd = tc.arguments.get("command", "")
                from ..tools.shell import ShellTool

                shell = self.tools.get("shell")
                if isinstance(shell, ShellTool):
                    original_require = shell.require_approval
                    shell.require_approval = False
                    try:
                        result = shell.run(cmd, timeout=tc.arguments.get("timeout", 120))
                    finally:
                        shell.require_approval = original_require
            else:
                return ToolResult(False, "user denied the command")
        return result

    async def compact(self) -> str:
        """Summarize the conversation to save context. Returns the summary."""
        if not self._provider:
            self._provider = build_provider(self.current_profile or self.config.profiles[self.config.routing.default])
        # Build a summarization prompt
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
            # Replace history with system + summary
            self.reset_history()
            self.history.append(Message(role="user", content=f"Previous conversation summary:\n{summary}"))
            self.history.append(Message(role="assistant", content="Understood. I have the context from the previous conversation."))
            return summary
        except Exception as e:
            return f"compact failed: {e}"

    async def close(self) -> None:
        if self._provider:
            await self._provider.close()
