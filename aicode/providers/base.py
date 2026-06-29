"""Base types for all providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional


@dataclass
class ToolCall:
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class Message:
    """Unified message format used internally.

    role: "system" | "user" | "assistant" | "tool"
    content: str (may be empty when only tool_calls present)
    tool_calls: list[ToolCall] (assistant messages only)
    tool_call_id: str (tool messages only, matches the request that produced them)
    name: str (tool messages only — which tool produced this)
    """

    role: str
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class Response:
    """The non-streaming tail of a chat completion."""

    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Dict[str, int] = field(default_factory=dict)


@dataclass
class ToolSpec:
    """JSON-schema-style tool definition (OpenAI tools format)."""

    name: str
    description: str
    parameters: Dict[str, Any]

    def to_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Provider:
    """Base class — providers must implement `chat`."""

    def __init__(self, model: str, api_key: str, base_url: str = "", **kwargs: Any) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.kwargs = kwargs

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSpec]] = None,
        on_delta: Optional[Callable[[str], None]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> Response:
        """Stream a chat completion. `on_delta` is called with text chunks as they arrive."""
        raise NotImplementedError

    async def close(self) -> None:
        """Clean up any resources (http client, etc.)."""
        pass
