"""File operation tools — read, write, edit, list, grep."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from .base import Tool, ToolResult

# Safety: how many lines/chars we'll return at once
MAX_READ_LINES = 500
MAX_GREP_RESULTS = 100


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read the contents of a file. Returns up to "
        f"{MAX_READ_LINES} lines. Use a path relative to the working directory or an absolute path."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "offset": {"type": "integer", "description": "Line number to start from (1-indexed)", "default": 1},
            "limit": {"type": "integer", "description": "Max number of lines to read", "default": MAX_READ_LINES},
        },
        "required": ["path"],
    }

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = Path(cwd).resolve()

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        if not path.is_absolute():
            path = self.cwd / path
        return path.resolve()

    def run(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES, **_: Any) -> ToolResult:
        try:
            resolved = self._resolve(path)
            if not resolved.exists():
                return ToolResult(False, f"file not found: {path}")
            if not resolved.is_file():
                return ToolResult(False, f"not a file: {path}")
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            start = max(0, offset - 1)
            end = start + limit
            chunk = lines[start:end]
            text = "".join(chunk)
            header = f"--- {path} (lines {start+1}-{start+len(chunk)} of {len(lines)}) ---\n"
            return ToolResult(True, header + text)
        except Exception as e:
            return ToolResult(False, f"read error: {e}")


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write text to a file, creating it if it does not exist. "
        "Overwrites the entire file. Use `edit_file` for in-place edits."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Full file contents to write"},
            "create_dirs": {"type": "boolean", "description": "Create parent directories if missing", "default": True},
        },
        "required": ["path", "content"],
    }

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = Path(cwd).resolve()

    def run(self, path: str, content: str, create_dirs: bool = True, **_: Any) -> ToolResult:
        try:
            resolved = (self.cwd / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
            if create_dirs:
                resolved.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(True, f"wrote {len(content)} bytes to {path}")
        except Exception as e:
            return ToolResult(False, f"write error: {e}")


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Apply a find-and-replace edit to an existing file. "
        "`old_text` must match exactly once in the file (whitespace-sensitive). "
        "Use `apply_edits` to do multiple replacements in one call."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string", "description": "Exact text to find (must be unique in the file)"},
            "new_text": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = Path(cwd).resolve()

    def run(self, path: str, old_text: str, new_text: str, **_: Any) -> ToolResult:
        try:
            resolved = (self.cwd / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
            if not resolved.exists():
                return ToolResult(False, f"file not found: {path}")
            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read()
            count = content.count(old_text)
            if count == 0:
                return ToolResult(False, f"old_text not found in {path}")
            if count > 1:
                # Provide context so the user/model can disambiguate
                return ToolResult(False, f"old_text matches {count} times in {path} — make it more unique")
            new_content = content.replace(old_text, new_text, 1)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(new_content)
            return ToolResult(True, f"edited {path}")
        except Exception as e:
            return ToolResult(False, f"edit error: {e}")


class ListFilesTool(Tool):
    name = "list_files"
    description = (
        "List files in a directory (recursive up to a depth). "
        "Returns relative paths, one per line."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list", "default": "."},
            "max_depth": {"type": "integer", "description": "Max recursion depth", "default": 2},
            "ignore_globs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns to exclude (e.g. ['.git', 'node_modules'])",
                "default": [".git", "node_modules", "__pycache__", ".venv"],
            },
        },
    }

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = Path(cwd).resolve()

    def run(
        self,
        path: str = ".",
        max_depth: int = 2,
        ignore_globs: List[str] = None,
        **_: Any,
    ) -> ToolResult:
        ignore_globs = ignore_globs or [".git", "node_modules", "__pycache__", ".venv"]
        try:
            root = (self.cwd / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
            if not root.exists():
                return ToolResult(False, f"dir not found: {path}")
            results: List[str] = []
            for dirpath, dirnames, filenames in os.walk(root):
                rel = Path(dirpath).relative_to(root)
                depth = 0 if str(rel) == "." else len(rel.parts)
                if depth >= max_depth:
                    dirnames[:] = []
                    continue
                # Filter ignored dirs
                dirnames[:] = [d for d in dirnames if d not in ignore_globs and not any(Path(d).match(g) for g in ignore_globs)]
                for fn in filenames:
                    if any(Path(fn).match(g) for g in ignore_globs):
                        continue
                    rel_path = (rel / fn) if str(rel) != "." else Path(fn)
                    results.append(str(rel_path))
            results.sort()
            return ToolResult(True, "\n".join(results[:500]) or "(empty)")
        except Exception as e:
            return ToolResult(False, f"list error: {e}")


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search file contents with a regex. Returns matching lines with file:line prefixes. "
        f"Cap at {MAX_GREP_RESULTS} matches."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regex"},
            "path": {"type": "string", "description": "Directory or file to search", "default": "."},
            "glob": {"type": "string", "description": "Filename glob filter (e.g. '*.py')", "default": "*"},
            "ignore_case": {"type": "boolean", "default": False},
        },
        "required": ["pattern"],
    }

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = Path(cwd).resolve()

    def run(self, pattern: str, path: str = ".", glob: str = "*", ignore_case: bool = False, **_: Any) -> ToolResult:
        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(False, f"invalid regex: {e}")
        root = (self.cwd / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not root.exists():
            return ToolResult(False, f"path not found: {path}")
        out: List[str] = []
        try:
            for p in root.rglob(glob):
                if not p.is_file():
                    continue
                if any(part in {".git", "node_modules", "__pycache__", ".venv"} for part in p.parts):
                    continue
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, start=1):
                            if regex.search(line):
                                rel = p.relative_to(self.cwd) if p.is_relative_to(self.cwd) else p
                                out.append(f"{rel}:{i}: {line.rstrip()}")
                                if len(out) >= MAX_GREP_RESULTS:
                                    out.append("... (truncated)")
                                    return ToolResult(True, "\n".join(out))
                except (UnicodeDecodeError, OSError):
                    continue
        except Exception as e:
            return ToolResult(False, f"grep error: {e}")
        return ToolResult(True, "\n".join(out) if out else "(no matches)")
