"""OpenAI-compatible provider — used for OpenAI, NVIDIA NIM, Groq, OpenRouter, Ollama.

All of these expose `/chat/completions` with the same request/response schema,
so we just need to vary `base_url` and the auth header.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import httpx

from .base import Message, Provider, Response, ToolCall, ToolSpec

# Per-provider defaults — overridden by Profile.base_url
PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "auth_prefix": "Bearer "},
    "nim": {"base_url": "https://integrate.api.nvidia.com/v1", "auth_prefix": "Bearer "},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "auth_prefix": "Bearer "},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "auth_prefix": "Bearer "},
    "ollama": {"base_url": "http://localhost:11434/v1", "auth_prefix": ""},
    "together": {"base_url": "https://api.together.xyz/v1", "auth_prefix": "Bearer "},
    "fireworks": {"base_url": "https://api.fireworks.ai/inference/v1", "auth_prefix": "Bearer "},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "auth_prefix": "Bearer "},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "auth_prefix": "Bearer "},
}


class OpenAICompatProvider(Provider):
    """Streams chat completions from any OpenAI-compatible endpoint via SSE."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "",
        provider_kind: str = "openai",
        **kwargs: Any,
    ) -> None:
        super().__init__(model, api_key, base_url, **kwargs)
        self.provider_kind = provider_kind
        if not self.base_url:
            self.base_url = PROVIDER_DEFAULTS.get(provider_kind, {}).get("base_url", "")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: Dict[str, str] = {"Accept": "application/json"}
            auth_prefix = PROVIDER_DEFAULTS.get(self.provider_kind, {}).get("auth_prefix", "Bearer ")
            if self.api_key and self.api_key != "ollama":
                headers["Authorization"] = f"{auth_prefix}{self.api_key}"
            # OpenRouter likes these for ranking/identification
            if self.provider_kind == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/aicode-termux"
                headers["X-Title"] = "aicode-termux"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
        return self._client

    def _serialize_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id or "",
                        "content": m.content,
                        "name": m.name or "",
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSpec]] = None,
        on_delta: Optional[Callable[[str], None]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> Response:
        client = await self._get_client()
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages),
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]

        content_parts: List[str] = []
        tool_calls: Dict[int, Dict[str, Any]] = {}
        finish_reason = "stop"
        usage: Dict[str, int] = {}

        async with client.stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(
                    f"{self.provider_kind} API error {resp.status_code}: {body.decode('utf-8', 'ignore')[:500]}"
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        text = delta["content"]
                        content_parts.append(text)
                        if on_delta:
                            on_delta(text)
                    if delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            slot = tool_calls.setdefault(
                                idx, {"id": "", "name": "", "arguments": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

        final_tool_calls: List[ToolCall] = []
        for idx in sorted(tool_calls):
            slot = tool_calls[idx]
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            final_tool_calls.append(ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=args))

        return Response(
            content="".join(content_parts),
            tool_calls=final_tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
