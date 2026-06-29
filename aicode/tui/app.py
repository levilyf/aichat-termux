"""Main Textual app — Claude Code-style TUI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

from rich.markdown import Markdown
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Footer,
    Header,
    Input,
    Static,
    TextArea,
    Tree,
)

from ..agent.loop import Agent, AgentEvent, AgentEventType
from ..config import Config, Profile
from ..cost import SessionCost
from ..memory import has_project_memory, init_project_memory, load_memory, write_project_memory
from ..permissions import PermissionManager
from ..session import list_sessions, load_session, save_session
from ..tools.base import ToolResult
from .widgets import ChatMessage, FileTreeWidget, StatusBar

SLASH_HELP = """\
## Slash commands

### Session
- `/help`                — show this help
- `/clear`               — clear conversation history
- `/compact`             — summarize conversation to save context
- `/status`              — show session info (model, tokens, cost, duration)
- `/cost`                — detailed cost breakdown per turn
- `/save`                — save current session to disk
- `/resume <id>`         — resume a saved session
- `/sessions`            — list saved sessions
- `/quit`                — exit

### Models & routing
- `/model <name>`        — pin a profile (e.g. `/model nim`)
- `/auto`                — re-enable auto-routing
- `/profiles`            — list configured profiles + usability

### Project & memory
- `/init`                — analyze project and create AICODE.md
- `/memory`              — show current AICODE.md content
- `/memory edit`         — write a new AICODE.md (opens editor)
- `/files`               — refresh the file tree
- `/tools`               — list available tools

### Workflow
- `/plan`                — toggle plan mode (read-only, plans before executing)
- `/review`              — review uncommitted changes (git diff)
- `/permissions`         — show permission rules

### Tips
- **Enter** to send, **Shift+Enter** for newline
- **Ctrl+C** to interrupt a running turn
- Pipe commands: `aicode exec "fix the bug"` (non-interactive)
"""


class AICodeApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "aicode"
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_screen", "Clear screen", show=False),
    ]

    def __init__(
        self,
        config: Config,
        cwd: str,
        profile: Optional[Profile] = None,
        plan_mode: bool = False,
        session_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.cwd = cwd
        self.initial_profile = profile
        self.initial_plan_mode = plan_mode
        self.session_id = session_id
        self.agent: Optional[Agent] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static(" Files", id="sidebar-title")
                yield FileTreeWidget(id="file-tree")
                yield StatusBar(id="status-bar")
            with Vertical(id="main"):
                yield Static(
                    f" aicode · {self.cwd} · type /help for commands",
                    id="header",
                )
                yield Container(id="chat")
                with Horizontal(id="input-area"):
                    yield TextArea(
                        "Ask anything — /help for commands",
                        id="input",
                        soft_wrap=True,
                    )
        yield Footer()

    def on_mount(self) -> None:
        try:
            tree = self.query_one("#file-tree", FileTreeWidget)
            tree.load(Path(self.cwd))
        except Exception:
            pass

        self.agent = Agent(
            config=self.config,
            cwd=self.cwd,
            profile=self.initial_profile,
            on_event=self._on_agent_event,
            plan_mode=self.initial_plan_mode,
            session_id=self.session_id,
        )

        # Resume session if specified
        if self.session_id:
            loaded = load_session(self.session_id)
            if loaded:
                messages, pinned, sess_cwd, cost_summary = loaded
                self.agent.history = messages
                if pinned:
                    asyncio.ensure_future(self.agent.pin_profile(pinned))
                self._add_chat_message("system", f"Resumed session **{self.session_id}** ({len(messages)} messages)")

        bar = self.query_one("#status-bar", StatusBar)
        if self.initial_profile:
            bar.set_profile(self.initial_profile.name, self.initial_profile.model)
        else:
            bar.set_profile("auto", "")

        self._add_chat_message(
            "system",
            "Welcome to **aicode** — Claude Code-style AI agent for Termux. "
            "Type `/help` to see commands, or just ask me to do something.",
        )
        self.query_one("#input", TextArea).focus()

    def _add_chat_message(self, role: str, content: str) -> ChatMessage:
        chat = self.query_one("#chat", Container)
        msg = ChatMessage(role=role, content=content)
        chat.mount(msg)
        chat.scroll_end(animate=False)
        return msg

    def _on_agent_event(self, event: AgentEvent) -> None:
        if event.type == AgentEventType.TEXT_DELTA:
            self.call_from_thread(self._append_text_delta, event.text)
        elif event.type == AgentEventType.TOOL_CALL:
            tc = event.tool_call
            args_str = ", ".join(f"{k}={v!r}" for k, v in tc.arguments.items())
            text = f"**→ {tc.name}**({args_str})"
            self.call_from_thread(self._add_chat_message, "tool", text)
        elif event.type == AgentEventType.TOOL_RESULT:
            res = event.tool_result
            preview = res.output[:1200] + ("..." if len(res.output) > 1200 else "")
            status = "✓" if res.success else "✗"
            text = f"**{status} {event.tool_call.name}**\n```\n{preview}\n```"
            self.call_from_thread(self._add_chat_message, "tool", text)
        elif event.type == AgentEventType.PROFILE_SWITCHED:
            profile_name = event.profile or ""
            profile = self.config.profiles.get(profile_name)
            self.call_from_thread(
                self._update_status, profile_name, profile.model if profile else ""
            )
        elif event.type == AgentEventType.ERROR:
            self.call_from_thread(self._add_chat_message, "error", f"⚠ {event.error}")
        elif event.type == AgentEventType.COST_UPDATE:
            self.call_from_thread(self._update_cost)
        elif event.type == AgentEventType.PLAN:
            self.call_from_thread(self._add_chat_message, "system", f"## Plan\n\n{event.plan}")
        elif event.type == AgentEventType.COMPLETE:
            pass

    def _append_text_delta(self, text: str) -> None:
        chat = self.query_one("#chat", Container)
        children = list(chat.children)
        if not children or not isinstance(children[-1], ChatMessage) or children[-1].role != "assistant":
            msg = ChatMessage(role="assistant", content=text)
            chat.mount(msg)
        else:
            msg = children[-1]
            msg.append_text(text)
        chat.scroll_end(animate=False)

    def _update_status(self, profile: str, model: str) -> None:
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.set_profile(profile, model)
        except Exception:
            pass

    def _update_cost(self) -> None:
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.set_cost(self.agent.session_cost.total_cost, self.agent.session_cost.turn_count)
        except Exception:
            pass

    def on_textarea_changed(self, event: TextArea.Changed) -> None:
        # No-op; we use Ctrl+Enter / Enter to submit
        pass

    def on_key(self, event) -> None:
        # Submit on Enter (without shift), insert newline on Shift+Enter
        if event.key == "enter" and self.focused and self.focused.id == "input":
            ta = self.query_one("#input", TextArea)
            text = ta.text.strip()
            if not text:
                return
            ta.text = ""
            event.prevent_default()
            if text.startswith("/"):
                self._handle_slash(text)
            else:
                self._add_chat_message("user", text)
                self._run_turn(text)

    def _handle_slash(self, text: str) -> None:
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in {"help", "h", "?"}:
            self._add_chat_message("system", SLASH_HELP)
        elif cmd == "clear":
            self.agent.reset_history()
            self._clear_chat()
            self._add_chat_message("system", "Conversation cleared.")
        elif cmd == "compact":
            self._add_chat_message("system", "Compacting conversation...")
            asyncio.ensure_future(self._do_compact())
        elif cmd == "status":
            self._show_status()
        elif cmd == "cost":
            self._add_chat_message("system", self.agent.session_cost.format_breakdown())
        elif cmd == "save":
            sid = save_session(
                self.agent.session_id,
                self.agent.cwd,
                self.agent.pinned_profile.name if self.agent.pinned_profile else None,
                self.agent.history,
                {"total_cost": self.agent.session_cost.total_cost},
            )
            self.agent.session_id = sid
            self._add_chat_message("system", f"Session saved as **{sid}**. Resume with: `aicode --resume {sid}`")
        elif cmd == "sessions":
            sessions = list_sessions()
            if not sessions:
                self._add_chat_message("system", "No saved sessions.")
            else:
                lines = ["**Saved sessions:**", ""]
                for s in sessions[:15]:
                    preview = s["preview"][:60] or "(empty)"
                    lines.append(f"- `{s['id']}` — {s['message_count']} msgs — {preview}")
                self._add_chat_message("system", "\n".join(lines))
        elif cmd == "resume":
            if not arg:
                self._add_chat_message("system", "Usage: `/resume <session-id>`")
                return
            loaded = load_session(arg)
            if not loaded:
                self._add_chat_message("error", f"Session {arg} not found.")
                return
            messages, pinned, sess_cwd, _ = loaded
            self.agent.history = messages
            if pinned:
                asyncio.ensure_future(self.agent.pin_profile(pinned))
            self._clear_chat()
            self._add_chat_message("system", f"Resumed session **{arg}** ({len(messages)} messages)")
        elif cmd == "tools":
            tools = ", ".join(self.agent.tools.names())
            self._add_chat_message("system", f"**Available tools:** {tools}")
        elif cmd == "profiles":
            self._show_profiles()
        elif cmd == "model":
            self._handle_model(arg)
        elif cmd == "auto":
            asyncio.ensure_future(self.agent.unpin())
            self._update_status("auto", "")
            self._add_chat_message("system", "Auto-routing re-enabled.")
        elif cmd == "files":
            tree = self.query_one("#file-tree", FileTreeWidget)
            tree.load(Path(self.cwd))
            self._add_chat_message("system", "File tree refreshed.")
        elif cmd == "init":
            self._do_init()
        elif cmd == "memory":
            self._handle_memory(arg)
        elif cmd == "plan":
            new_state = self.agent.toggle_plan_mode()
            self._add_chat_message(
                "system",
                f"Plan mode **{'ON' if new_state else 'OFF'}**. "
                + ("The agent will plan before executing — no tools will run until you approve." if new_state else "Agent will execute tools normally."),
            )
        elif cmd == "review":
            asyncio.ensure_future(self._do_review())
        elif cmd == "permissions":
            self._add_chat_message("system", self.agent.permissions.summary())
        elif cmd in {"quit", "exit"}:
            self.exit()
        else:
            self._add_chat_message("error", f"Unknown command: /{cmd}. Try /help.")

    def _clear_chat(self) -> None:
        chat = self.query_one("#chat", Container)
        for child in list(chat.children):
            child.remove()

    def _show_status(self) -> None:
        import time
        elapsed = time.time() - self.agent.session_cost.started_at
        hours, rem = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(rem, 60)
        profile = self.agent.current_profile
        lines = [
            "**Session status**",
            "",
            f"- Profile: `{profile.name if profile else 'auto'}` ({profile.provider}/{profile.model if profile else '?'})",
            f"- Pinned: {'yes' if self.agent.pinned_profile else 'no (auto-routing)'}",
            f"- Plan mode: {'on' if self.agent.plan_mode else 'off'}",
            f"- Working dir: `{self.cwd}`",
            f"- Messages: {len(self.agent.history)}",
            f"- Turns: {self.agent.session_cost.turn_count}",
            f"- Input tokens: {self.agent.session_cost.total_input_tokens:,}",
            f"- Output tokens: {self.agent.session_cost.total_output_tokens:,}",
            f"- Total cost: ${self.agent.session_cost.total_cost:.4f}",
            f"- Duration: {hours:02d}:{minutes:02d}:{seconds:02d}",
            f"- Session ID: `{self.agent.session_id or '(unsaved)'}`",
        ]
        self._add_chat_message("system", "\n".join(lines))

    def _show_profiles(self) -> None:
        lines = ["**Profiles:**"]
        for name, p in self.config.profiles.items():
            marker = " (pinned)" if self.agent.pinned_profile and self.agent.pinned_profile.name == name else ""
            usable = "✓" if self.config.is_profile_usable(name) else "✗"
            lines.append(f"- {usable} `{name}` — {p.provider}/{p.model}{marker}")
        lines.append("\n**Routing:**")
        lines.append(f"- coding → `{self.config.routing.coding}`")
        lines.append(f"- reasoning → `{self.config.routing.reasoning}`")
        lines.append(f"- simple → `{self.config.routing.simple}`")
        lines.append(f"- default → `{self.config.routing.default}`")
        lines.append("\n✓ = usable (key set)  ✗ = missing key")
        self._add_chat_message("system", "\n".join(lines))

    def _handle_model(self, arg: str) -> None:
        if not arg:
            self._add_chat_message("system", "Usage: `/model <profile-name>`")
            return
        if arg not in self.config.profiles:
            self._add_chat_message("error", f"Unknown profile: {arg}")
            return
        if not self.config.is_profile_usable(arg):
            p = self.config.profiles[arg]
            self._add_chat_message(
                "error",
                f"Profile `{arg}` ({p.provider}/{p.model}) has no API key set. "
                f"Run `aicode setup` to configure it.",
            )
            return
        asyncio.ensure_future(self.agent.pin_profile(arg))
        p = self.config.profiles[arg]
        self._update_status(arg, p.model)
        self._add_chat_message("system", f"Pinned to profile **{arg}** ({p.provider}/{p.model})")

    def _do_init(self) -> None:
        if has_project_memory(self.cwd):
            self._add_chat_message("system", f"AICODE.md already exists at `{self.cwd}/AICODE.md`.")
            return
        # Generate a quick project summary by listing files
        from ..tools.file_ops import ListFilesTool
        ls = ListFilesTool(cwd=self.cwd)
        result = ls.run(path=".", max_depth=2)
        files = result.output if result.success else "(could not list files)"
        summary = f"Project at {self.cwd}\n\nFiles:\n{files[:2000]}"
        path = init_project_memory(self.cwd, summary)
        self._add_chat_message("system", f"Created `{path}`. Edit it to add project context.")

    def _handle_memory(self, arg: str) -> None:
        if arg == "edit":
            # Write a basic template if none exists
            content = load_memory(self.cwd)
            if not content:
                content = "# Project Memory\n\nDescribe your project here for the agent to remember.\n"
            path = write_project_memory(self.cwd, content)
            self._add_chat_message("system", f"Memory written to `{path}`. Edit the file directly to update.")
        else:
            content = load_memory(self.cwd)
            if content:
                self._add_chat_message("system", content)
            else:
                self._add_chat_message("system", "No AICODE.md found. Run `/init` to create one.")

    async def _do_compact(self) -> None:
        summary = await self.agent.compact()
        self._clear_chat()
        self._add_chat_message("system", f"**Conversation compacted.** Summary:\n\n{summary}")

    async def _do_review(self) -> None:
        """Review uncommitted changes via git diff."""
        self._add_chat_message("system", "Reviewing uncommitted changes...")
        result = self.agent.tools.execute("git", {"args": "diff"})
        diff = result.output if result.success else "(no changes or not a git repo)"
        if not diff.strip():
            self._add_chat_message("system", "No uncommitted changes to review.")
            return
        # Ask the agent to review
        self._add_chat_message("user", f"Review these uncommitted changes and suggest improvements:\n\n```diff\n{diff[:8000]}\n```")
        await self._run_turn_async(f"Review these uncommitted changes and suggest improvements:\n\n```diff\n{diff[:8000]}\n```")

    @work(exclusive=True, name="agent-turn")
    async def _run_turn(self, user_text: str) -> None:
        if self.agent is None:
            return
        try:
            async for _ in self.agent.chat(user_text):
                pass
        except Exception as e:
            self._add_chat_message("error", f"Agent error: {e}")

    async def _run_turn_async(self, user_text: str) -> None:
        """Internal: run a turn without spawning a separate worker (for /review etc.)."""
        if self.agent is None:
            return
        try:
            async for _ in self.agent.chat(user_text):
                pass
        except Exception as e:
            self._add_chat_message("error", f"Agent error: {e}")

    async def request_approval(self, command: str) -> bool:
        """For v1, deny by default. v2: pop a modal."""
        return False

    async def action_clear_screen(self) -> None:
        self._clear_chat()

    async def on_unmount(self) -> None:
        if self.agent:
            await self.agent.close()
