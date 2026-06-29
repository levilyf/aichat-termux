"""Session save/resume — persist conversation history to disk.

Sessions are stored as JSON in ~/.local/share/aicode/sessions/<id>.json
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from .providers.base import Message, ToolCall

# Where to store sessions — computed lazily so tests can override XDG_DATA_HOME
def _sessions_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "aicode" / "sessions"


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.json"


@dataclass
class SessionRecord:
    """A saved conversation session."""

    id: str
    created_at: float
    updated_at: float
    cwd: str
    pinned_profile: Optional[str]
    messages: List[dict]  # serialized Message objects
    cost_summary: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def _serialize_message(m: Message) -> dict:
    return {
        "role": m.role,
        "content": m.content,
        "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls],
        "tool_call_id": m.tool_call_id,
        "name": m.name,
    }


def _deserialize_message(d: dict) -> Message:
    return Message(
        role=d["role"],
        content=d.get("content", ""),
        tool_calls=[ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"]) for tc in d.get("tool_calls", [])],
        tool_call_id=d.get("tool_call_id"),
        name=d.get("name"),
    )


def save_session(
    session_id: Optional[str],
    cwd: str,
    pinned_profile: Optional[str],
    messages: List[Message],
    cost_summary: dict,
) -> str:
    """Save a session to disk. Returns the session ID."""
    if session_id is None:
        session_id = uuid.uuid4().hex[:12]

    _sessions_dir().mkdir(parents=True, exist_ok=True)
    now = time.time()
    record = SessionRecord(
        id=session_id,
        created_at=now,
        updated_at=now,
        cwd=cwd,
        pinned_profile=pinned_profile,
        messages=[_serialize_message(m) for m in messages],
        cost_summary=cost_summary,
    )
    path = _session_path(session_id)
    path.write_text(record.to_json(), encoding="utf-8")
    return session_id


def load_session(session_id: str) -> Optional[tuple]:
    """Load a session from disk. Returns (messages, pinned_profile, cwd, cost_summary) or None."""
    path = _session_path(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    messages = [_deserialize_message(m) for m in data.get("messages", [])]
    return (
        messages,
        data.get("pinned_profile"),
        data.get("cwd"),
        data.get("cost_summary", {}),
    )


def list_sessions(limit: int = 20) -> List[dict]:
    """List recent sessions (newest first)."""
    if not _sessions_dir().exists():
        return []
    sessions: List[dict] = []
    for p in _sessions_dir().glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Find first user message for preview
            preview = ""
            for m in data.get("messages", []):
                if m.get("role") == "user":
                    preview = m.get("content", "")[:80]
                    break
            sessions.append({
                "id": data["id"],
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
                "cwd": data.get("cwd", ""),
                "pinned_profile": data.get("pinned_profile"),
                "preview": preview,
                "message_count": len(data.get("messages", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return sessions[:limit]
