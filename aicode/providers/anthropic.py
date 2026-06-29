"""Anthropic provider — uses the Messages API with SSE streaming.

Converts our internal Message list to Anthropic's format (system prompt is a top-level
field, not a message). Translates OpenAI-style tool specs to Anthropic's tool schema.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, List, Optional

import httpx

from .base import Message, Provider, Response, ToolCall, ToolSpec

ANTHROPIC_BASE = "https://api.anthropic.com/v1"


class AnthropicProvider(Provider):
    def __init__(self, model: str, api_key: str, base_url: str = "", **kwargs: Any) -> None:
        super().__init__(model, api_key, base_url or ANTHROPIC_BASE, **kwargs)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                    "accept": "text/event-stream",
                },
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
        return self._client

    def _split_system(self, messages: List[Message]) -> tuple[str, List[Message]]:
        sys_parts = [m.content for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        return "\n\n".join(sys_parts), rest

    def _to_anthropic_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": [{"type": "text", "text": m.content}]})
            elif m.role == "assistant":
                if m.tool_calls:
                    blocks: List[Dict[str, Any]] = []
                    if m.content:
                        blocks.append({"type": "text", "text": m.content})
                    for tc in m.tool_calls:
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                    out.append({"role": "assistant", "content": blocks})
                else:
                    out.append(
                        {"role": "assistant", "content": [{"type": "text", "text": m.content}]}
                    )
            elif m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
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
        system, rest = self._split_system(messages)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": self._to_anthropic_messages(rest),
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]

        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        current_tool: Optional[Dict[str, Any]] = None
        current_tool_input: str = ""
        finish_reason = "stop"
        usage: Dict[str, int] = {}

        async with client.stream("POST", "/messages", json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(
                    f"Anthropic API error {resp.status_code}: {body.decode('utf-8', 'ignore')[:500]}"
                )
            event_type: Optional[str] = None
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "message_start" and data.get("message", {}).get("usage"):
                        usage.update(data["message"]["usage"])
                    elif data.get("type") == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool = {"id": block.get("id", uuid.uuid4().hex), "name": block.get("name", "")}
                            current_tool_input = ""
                    elif data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            content_parts.append(text)
                            if on_delta:
                                on_delta(text)
                        elif delta.get("type") == "input_json_delta" and current_tool:
                            current_tool_input += delta.get("partial_json", "")
                    elif data.get("type") == "content_block_stop" and current_tool:
                        try:
                            args = json.loads(current_tool_input) if current_tool_input else {}
                        except json.JSONDecodeError:
                            args = {"_raw": current_tool_input}
                        tool_calls.append(
                            ToolCall(id=current_tool["id"], name=current_tool["name"], arguments=args)
                        )
                        current_tool = None
                        current_tool_input = ""
                    elif data.get("type") == "message_delta":
                        d = data.get("delta", {})
                        if d.get("stop_reason"):
                            finish_reason = d["stop_reason"]
                        if data.get("usage"):
                            usage.update(data["usage"])

        return Response(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
