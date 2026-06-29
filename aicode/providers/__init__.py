"""Provider abstraction for multiple LLM APIs.

All providers expose a unified `chat(messages, tools, on_delta) -> Response` async method
that returns content + tool_calls. Streaming deltas are emitted via `on_delta(text)`.
"""

from .base import Provider, Message, ToolCall, Response, ToolSpec
from .openai_compat import OpenAICompatProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .registry import build_provider, PROVIDER_CLASSES

__all__ = [
    "Provider",
    "Message",
    "ToolCall",
    "Response",
    "ToolSpec",
    "OpenAICompatProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "build_provider",
    "PROVIDER_CLASSES",
]
