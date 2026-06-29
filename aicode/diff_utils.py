"""Diff utilities — generate and display file diffs before/after edits."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import List, Tuple


def generate_diff(old: str, new: str, filename: str = "file") -> str:
    """Generate a unified diff between old and new content."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


def preview_edit(filepath: str, old_text: str, new_text: str) -> str:
    """Show a diff preview for an edit_file operation."""
    return generate_diff(old_text, new_text, filepath)


def preview_write(filepath: str, new_content: str, existed: bool, old_content: str = "") -> str:
    """Show a diff preview for a write_file operation."""
    if not existed:
        # Show all lines as additions
        lines = new_content.splitlines(keepends=True)
        diff_lines = [f"--- /dev/null\n", f"+++ b/{filepath}\n"]
        for line in lines:
            diff_lines.append(f"+{line}" if not line.endswith("\n") else f"+{line}")
        return "".join(diff_lines)
    return generate_diff(old_content, new_content, filepath)


def apply_with_backup(filepath: Path, new_content: str) -> Path:
    """Write new content, keeping a .bak of the old file."""
    backup = filepath.with_suffix(filepath.suffix + ".bak")
    if filepath.exists():
        backup.write_text(filepath.read_text(encoding="utf-8"), encoding="utf-8")
    filepath.write_text(new_content, encoding="utf-8")
    return backup
