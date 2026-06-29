"""Modal screens for permission prompts and confirmations."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class PermissionModal(ModalScreen[bool]):
    """A modal that asks the user to approve/deny a tool call."""

    CSS = """
    PermissionModal {
        align: center middle;
    }
    #perm-dialog {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 70;
        height: auto;
        max-height: 20;
    }
    #perm-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    #perm-body {
        margin-bottom: 1;
        max-height: 10;
        overflow-y: auto;
    }
    #perm-buttons {
        layout: horizontal;
        height: 3;
        align: center middle;
    }
    #perm-yes {
        margin-right: 2;
        background: $success;
    }
    #perm-no {
        background: $error;
    }
    """

    def __init__(self, tool_name: str, args: dict, reason: str = "") -> None:
        super().__init__()
        self.tool_name = tool_name
        self.args = args
        self.reason = reason

    def compose(self) -> ComposeResult:
        with Vertical(id="perm-dialog"):
            yield Static(f"⚠ Permission required: {self.tool_name}", id="perm-title")
            args_str = "\n".join(f"  {k}: {v!r}" for k, v in self.args.items())
            body = f"Arguments:\n{args_str}"
            if self.reason:
                body = f"{self.reason}\n\n{body}"
            yield Static(body, id="perm-body")
            with Vertical(id="perm-buttons"):
                yield Button("Allow (y)", id="perm-yes", variant="success")
                yield Button("Deny (n)", id="perm-no", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "perm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "y":
            self.dismiss(True)
        elif event.key in {"n", "escape"}:
            self.dismiss(False)


class ConfirmModal(ModalScreen[bool]):
    """A simple yes/no confirmation modal."""

    CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm-dialog {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 60;
        height: auto;
        max-height: 15;
    }
    #confirm-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #confirm-buttons {
        layout: horizontal;
        height: 3;
        align: center middle;
    }
    #confirm-yes {
        margin-right: 2;
        background: $success;
    }
    #confirm-no {
        background: $error;
    }
    """

    def __init__(self, title: str, body: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.body_text = body

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self.title_text, id="confirm-title")
            if self.body_text:
                yield Static(self.body_text, id="confirm-body")
            with Vertical(id="confirm-buttons"):
                yield Button("Yes (y)", id="confirm-yes", variant="success")
                yield Button("No (n)", id="confirm-no", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def on_key(self, event) -> None:
        if event.key == "y":
            self.dismiss(True)
        elif event.key in {"n", "escape"}:
            self.dismiss(False)
