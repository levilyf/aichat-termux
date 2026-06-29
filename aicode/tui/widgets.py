"""Custom widgets for the aicode TUI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Static, Tree


class ChatMessage(Static):
    """A single chat message bubble."""

    def __init__(self, role: str, content: str, *, classes: Optional[str] = None) -> None:
        self.role = role
        self.content = content
        role_label = {
            "user": "You",
            "assistant": "AI",
            "tool": "Tool",
            "error": "Error",
            "system": "System",
        }.get(role, role)
        if role in {"tool"}:
            renderable = Syntax(content, "text", theme="monokai", word_wrap=True)
        elif role in {"assistant", "system"}:
            renderable = Markdown(content)
        else:
            renderable = content
        super().__init__(
            renderable,
            markup=False,
            classes=f"chat-msg {role} {classes or ''}".strip(),
        )
        self.border_title = role_label

    def append_text(self, text: str) -> None:
        """Append streaming text and re-render."""
        self.content += text
        if self.role in {"assistant", "system"}:
            self.update(Markdown(self.content))
        else:
            self.update(self.content)


class ToolCallWidget(Static):
    """A collapsible tool call display — header always visible, output toggleable."""

    def __init__(self, tool_name: str, args_str: str, output: str = "", success: bool = True, expanded: bool = False) -> None:
        self.tool_name = tool_name
        self.args_str = args_str
        self.output = output
        self.success = success
        self.expanded = expanded
        self._render()
        super().__init__(self._build_renderable(), markup=True, classes="chat-msg tool")
        self.border_title = f"Tool: {tool_name}"

    def _build_renderable(self):
        from rich.markdown import Markdown
        status_icon = "✓" if self.success else "✗"
        color = "green" if self.success else "red"
        indicator = "▼" if self.expanded else "▶"
        header = f"[{color}]{status_icon}[/{color}] {indicator} [bold]{self.tool_name}[/bold]({self.args_str})"
        if self.output and self.expanded:
            full = f"{header}\n```\n{self.output[:1500]}\n```"
            return Markdown(full)
        return header

    def update_result(self, output: str, success: bool) -> None:
        """Update with the tool's result and re-render (stays collapsed)."""
        self.output = output
        self.success = success
        self.expanded = False  # collapse by default after result
        self.update(self._build_renderable())

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self.update(self._build_renderable())

    def _render(self) -> None:
        pass  # placeholder for any pre-init setup


class FileTreeWidget(Tree):
    """A simple file tree rooted at the working directory."""

    def load(self, root: Path, max_depth: int = 3) -> None:
        self.clear()
        self.root.set_label(root.name or str(root))
        self.root.data = root
        self._populate(self.root, root, depth=0, max_depth=max_depth)
        self.root.expand()

    def _populate(self, node, path: Path, depth: int, max_depth: int) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.name in {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache"}:
                continue
            child = node.add(entry.name, data=entry)
            if entry.is_dir():
                child.allow_expand = True
                self._populate(child, entry, depth + 1, max_depth)


class StatusBar(Static):
    """Status line showing current profile + model + cost."""

    def __init__(self, id: Optional[str] = None) -> None:
        super().__init__("", id=id)
        self.set_profile("auto", "")
        self.set_cost(0.0, 0)

    def set_profile(self, profile: str, model: str) -> None:
        self._profile = profile
        self._model = model
        self._refresh()

    def set_cost(self, total_cost: float, turn_count: int) -> None:
        self._cost = total_cost
        self._turns = turn_count
        self._refresh()

    def _refresh(self) -> None:
        text = (
            f" profile: {self._profile} | model: {self._model or '(auto)'} "
            f"| cost: ${self._cost:.4f} | turns: {self._turns} "
        )
        self.update(text)
