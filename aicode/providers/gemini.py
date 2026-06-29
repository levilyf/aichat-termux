"""Google Gemini provider — uses v1beta generateContent stream API.

Converts OpenAI-style tool specs to Gemini's `functionDeclarations` schema
and translates Gemini's `functionCall` parts back to ToolCall.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import httpx

from .base import Message, Provider, Response, ToolCall, ToolSpec

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(Provider):
    def __init__(self, model: str, api_key: str, base_url: str = "", **kwargs: Any) -> None:
        super().__init__(model, api_key, base_url or GEMINI_BASE, **kwargs)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"content-type": "application/json"},
                params={"key": self.api_key},
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
        return self._client

    def _to_gemini_contents(self, messages: List[Message]) -> tuple[str, List[Dict[str, Any]]]:
        """Returns (system_instruction, contents)."""
        sys_parts = [m.content for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        contents: List[Dict[str, Any]] = []
        for m in rest:
            if m.role == "user":
                contents.append({"role": "user", "parts": [{"text": m.content}]})
            elif m.role == "assistant":
                parts: List[Dict[str, Any]] = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
                contents.append({"role": "model", "parts": parts})
            elif m.role == "tool":
                contents.append(
                    {
                        "role": "function",
                        "parts": [{"functionResponse": {"name": m.name or "tool", "response": {"result": m.content}}}],
                    }
                )
        return "\n\n".join(sys_parts), contents

    def _tools_to_gemini(self, tools: Optional[List[ToolSpec]]) -> Optional[Dict[str, Any]]:
        if not tools:
            return None
        decls = []
        for t in tools:
            schema = dict(t.parameters)
            # Gemini doesn't accept these top-level JSON-schema keys; strip them
            for k in ("$schema", "additionalProperties", "title"):
                schema.pop(k, None)
            decls.append({"name": t.name, "description": t.description, "parameters": schema})
        return {"functionDeclarations": decls}

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSpec]] = None,
        on_delta: Optional[Callable[[str], None]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> Response:
        client = await self._get_client()
        system, contents = self._to_gemini_contents(messages)

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        gemini_tools = self._tools_to_gemini(tools)
        if gemini_tools:
            payload["tools"] = [gemini_tools]

        url = f"/models/{self.model}:streamGenerateContent"
        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        usage: Dict[str, int] = {}
        finish_reason = "stop"

        async with client.stream("POST", url, json=payload) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise RuntimeError(
                    f"Gemini API error {resp.status_code}: {body.decode('utf-8', 'ignore')[:500]}"
                )
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("[") and not line.startswith("{"):
                    continue
                # Gemini streams a JSON array; lines may be partial — try to parse array chunks
                try:
                    chunks = json.loads(line) if line.startswith("[") else [json.loads(line)]
                except json.JSONDecodeError:
                    continue
                for chunk in chunks:
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("usageMetadata"):
                        um = chunk["usageMetadata"]
                        usage = {
                            "prompt_tokens": um.get("promptTokenCount", 0),
                            "completion_tokens": um.get("candidatesTokenCount", 0),
                            "total_tokens": um.get("totalTokenCount", 0),
                        }
                    for cand in chunk.get("candidates", []):
                        if cand.get("finishReason"):
                            finish_reason = cand["finishReason"]
                        for part in cand.get("content", {}).get("parts", []):
                            if "text" in part:
                                content_parts.append(part["text"])
                                if on_delta:
                                    on_delta(part["text"])
                            elif "functionCall" in part:
                                fc = part["functionCall"]
                                tool_calls.append(
                                    ToolCall(
                                        id=f"call_{len(tool_calls)}",
                                        name=fc.get("name", ""),
                                        arguments=fc.get("args", {}) if isinstance(fc.get("args"), dict) else {},
                                    )
                                )

        return Response(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
