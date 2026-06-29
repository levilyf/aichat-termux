"""Memory system — AICODE.md project context (like Claude Code's CLAUDE.md).

The agent automatically loads AICODE.md files from:
  1. The current working directory (project-specific)
  2. The user's home directory (global preferences)

These get injected into the system prompt so the model has project context.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

MEMORY_FILENAME = "AICODE.md"
GLOBAL_MEMORY_FILENAME = ".aicode.md"  # in home dir, dot-prefixed


def find_memory_files(cwd: str) -> List[Path]:
    """Find AICODE.md files: project-local + global home."""
    found: List[Path] = []
    # Project-local
    local = Path(cwd) / MEMORY_FILENAME
    if local.is_file():
        found.append(local)
    # Walk up the tree looking for parent AICODE.md files (monorepo support)
    for parent in Path(cwd).parents:
        candidate = parent / MEMORY_FILENAME
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
        if parent == parent.parent:
            break
    # Global
    global_path = Path.home() / GLOBAL_MEMORY_FILENAME
    if global_path.is_file():
        found.append(global_path)
    return found


def load_memory(cwd: str) -> str:
    """Load and concatenate all AICODE.md files into a single string."""
    files = find_memory_files(cwd)
    if not files:
        return ""
    parts: List[str] = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"--- Memory: {f} ---\n{content}")
        except OSError:
            continue
    return "\n\n".join(parts)


def write_project_memory(cwd: str, content: str) -> Path:
    """Write/overwrite the project-local AICODE.md."""
    path = Path(cwd) / MEMORY_FILENAME
    path.write_text(content, encoding="utf-8")
    return path


def init_project_memory(cwd: str, project_summary: str) -> Path:
    """Create a starter AICODE.md with project context."""
    template = f"""# Project Memory

This file is automatically loaded by aicode on every turn.
Use it to give the agent persistent context about your project.

## Overview
{project_summary}

## Build & Test Commands
- `# fill in: e.g. pytest tests/`
- `# fill in: e.g. npm run build`

## Code Style
- `# fill in: e.g. use black for formatting, isort for imports`

## Architecture
- `# fill in: key modules and how they fit together`

## Gotchas
- `# fill in: anything tricky future-you should know`
"""
    return write_project_memory(cwd, template)


def has_project_memory(cwd: str) -> bool:
    return (Path(cwd) / MEMORY_FILENAME).is_file()
