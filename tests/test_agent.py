"""End-to-end agent tests with a mock provider.

These tests would have caught the critical bug where Agent.chat() was declared
as an async generator but never yielded. They exercise the full event flow:
TEXT_DELTA → TOOL_CALL → TOOL_RESULT → COMPLETE.
"""

from __future__ import annotations

import asyncio
from typing import Callable, List, Optional
from unittest.mock import MagicMock

import pytest

from aicode.agent.loop import Agent, AgentEvent, AgentEventType
from aicode.config import Config, Profile, default_config
from aicode.providers.base import Message, Provider, Response, ToolCall


class MockProvider(Provider):
    """A provider that returns canned responses in sequence."""

    def __init__(self, responses: List[Response]) -> None:
        super().__init__(model="mock", api_key="mock")
        self._responses = list(responses)
        self._index = 0
        self.calls: List[List[Message]] = []

    async def chat(
        self,
        messages: List[Message],
        tools=None,
        on_delta: Optional[Callable[[str], None]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> Response:
        self.calls.append(messages)
        if self._index >= len(self._responses):
            return Response(content="(no more responses)", tool_calls=[])
        resp = self._responses[self._index]
        self._index += 1
        # Stream the content via on_delta
        if on_delta and resp.content:
            on_delta(resp.content)
        return resp

    async def close(self) -> None:
        pass


def _make_response(content: str = "", tool_calls: Optional[List[ToolCall]] = None) -> Response:
    return Response(content=content, tool_calls=tool_calls or [], finish_reason="stop")


def _clear_env(monkeypatch) -> None:
    """Clear all provider env vars so tests don't pick up real keys."""
    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)


def _make_agent(tmp_path, monkeypatch, responses: List[Response]) -> Agent:
    """Build an Agent wired with a MockProvider, bypassing real config."""
    _clear_env(monkeypatch)
    cfg = default_config()
    # Pin the nim profile so the router doesn't replace our mock provider
    agent = Agent(config=cfg, cwd=str(tmp_path), profile=cfg.profiles["nim"])
    agent._provider = MockProvider(responses)
    return agent


def test_agent_chat_simple_text_response(tmp_path, monkeypatch):
    """Agent.chat() with a plain text response emits TEXT_DELTA + COMPLETE."""
    events: List[AgentEvent] = []
    agent = _make_agent(tmp_path, monkeypatch, [
        _make_response(content="Hello, world!"),
    ])
    agent.on_event = events.append

    asyncio.run(agent.chat("hi"))

    types = [e.type for e in events]
    assert AgentEventType.TEXT_DELTA in types, f"expected TEXT_DELTA, got {types}"
    assert AgentEventType.COMPLETE in types, f"expected COMPLETE, got {types}"
    # The text should be in the deltas
    deltas = "".join(e.text for e in events if e.type == AgentEventType.TEXT_DELTA)
    assert "Hello, world!" in deltas


def test_agent_chat_executes_tool_call(tmp_path, monkeypatch):
    """Agent.chat() with a tool_call emits TOOL_CALL + TOOL_RESULT, then loops."""
    # First response: model wants to read a file
    # Second response: model summarizes the file contents
    events: List[AgentEvent] = []
    agent = _make_agent(tmp_path, monkeypatch, [
        _make_response(
            content="Let me read that file.",
            tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"path": "/etc/hostname"})],
        ),
        _make_response(content="I read the file. Done!"),
    ])
    agent.on_event = events.append

    asyncio.run(agent.chat("read /etc/hostname"))

    types = [e.type for e in events]
    assert AgentEventType.TOOL_CALL in types, f"expected TOOL_CALL, got {types}"
    assert AgentEventType.TOOL_RESULT in types, f"expected TOOL_RESULT, got {types}"
    assert AgentEventType.COMPLETE in types, f"expected COMPLETE, got {types}"

    # Verify the tool call was for read_file
    tool_call_events = [e for e in events if e.type == AgentEventType.TOOL_CALL]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call.name == "read_file"

    # Verify the tool result was emitted
    tool_result_events = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
    assert len(tool_result_events) >= 1
    assert tool_result_events[0].tool_call.name == "read_file"


def test_agent_chat_handles_provider_error(tmp_path, monkeypatch):
    """Agent.chat() emits ERROR when the provider raises."""
    events: List[AgentEvent] = []

    class FailingProvider(MockProvider):
        async def chat(self, *args, **kwargs):
            raise RuntimeError("API key invalid")

    _clear_env(monkeypatch)
    cfg = default_config()
    agent = Agent(config=cfg, cwd=str(tmp_path), profile=cfg.profiles["nim"])
    agent._provider = FailingProvider([])
    agent.on_event = events.append

    asyncio.run(agent.chat("hello"))

    error_events = [e for e in events if e.type == AgentEventType.ERROR]
    assert len(error_events) == 1
    assert "API key invalid" in error_events[0].error


def test_agent_chat_tracks_file_modifications(tmp_path, monkeypatch):
    """Agent sets _files_modified_this_turn when write_file succeeds."""
    events: List[AgentEvent] = []

    async def allow_all(tool_name: str, args: dict) -> bool:
        return True

    _clear_env(monkeypatch)
    cfg = default_config()
    agent = Agent(
        config=cfg,
        cwd=str(tmp_path),
        profile=cfg.profiles["nim"],
        on_permission_request=allow_all,
    )
    agent._provider = MockProvider([
        _make_response(
            content="Writing the file.",
            tool_calls=[ToolCall(id="call_1", name="write_file",
                                  arguments={"path": "test.txt", "content": "hello"})],
        ),
        _make_response(content="Done!"),
    ])
    agent.on_event = events.append

    asyncio.run(agent.chat("write test.txt"))

    assert agent._files_modified_this_turn is True


def test_agent_chat_does_not_track_failed_writes(tmp_path, monkeypatch):
    """Agent does NOT set _files_modified_this_turn when write_file fails."""
    events: List[AgentEvent] = []
    # Try to write to a path that doesn't exist (no parent dir)
    agent = _make_agent(tmp_path, monkeypatch, [
        _make_response(
            content="Writing.",
            tool_calls=[ToolCall(id="call_1", name="write_file",
                                  arguments={"path": "/nonexistent/path/file.txt", "content": "x"})],
        ),
        _make_response(content="Done"),
    ])
    agent.on_event = events.append

    asyncio.run(agent.chat("write to bad path"))

    assert agent._files_modified_this_turn is False


def test_agent_chat_permission_denied(tmp_path, monkeypatch):
    """When permission callback returns False, tool is not executed."""
    events: List[AgentEvent] = []

    async def deny_all(tool_name: str, args: dict) -> bool:
        return False

    _clear_env(monkeypatch)
    cfg = default_config()
    agent = Agent(
        config=cfg,
        cwd=str(tmp_path),
        profile=cfg.profiles["nim"],
        on_permission_request=deny_all,
    )
    agent._provider = MockProvider([
        _make_response(
            content="Writing.",
            tool_calls=[ToolCall(id="call_1", name="write_file",
                                  arguments={"path": "test.txt", "content": "x"})],
        ),
        _make_response(content="Ok, I won't write."),
    ])
    agent.on_event = events.append

    asyncio.run(agent.chat("write test.txt"))

    # Should have a tool result with "denied"
    tool_results = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
    assert len(tool_results) >= 1
    assert "denied" in tool_results[0].tool_result.output.lower()
    # File should not have been modified
    assert agent._files_modified_this_turn is False


def test_agent_chat_permission_allowed(tmp_path, monkeypatch):
    """When permission callback returns True, tool executes."""
    events: List[AgentEvent] = []

    async def allow_all(tool_name: str, args: dict) -> bool:
        return True

    _clear_env(monkeypatch)
    cfg = default_config()
    agent = Agent(
        config=cfg,
        cwd=str(tmp_path),
        profile=cfg.profiles["nim"],
        on_permission_request=allow_all,
    )
    agent._provider = MockProvider([
        _make_response(
            content="Writing.",
            tool_calls=[ToolCall(id="call_1", name="write_file",
                                  arguments={"path": "test.txt", "content": "hello"})],
        ),
        _make_response(content="Done!"),
    ])
    agent.on_event = events.append

    asyncio.run(agent.chat("write test.txt"))

    assert agent._files_modified_this_turn is True
    # File should exist
    assert (tmp_path / "test.txt").read_text() == "hello"


def test_agent_chat_no_callback_denies_by_default(tmp_path, monkeypatch):
    """Without a permission callback, ASK tools are denied (safe default)."""
    events: List[AgentEvent] = []
    agent = _make_agent(tmp_path, monkeypatch, [
        _make_response(
            content="Writing.",
            tool_calls=[ToolCall(id="call_1", name="write_file",
                                  arguments={"path": "test.txt", "content": "x"})],
        ),
        _make_response(content="Done"),
    ])
    agent.on_event = events.append
    # No on_permission_request set — defaults to deny

    asyncio.run(agent.chat("write test.txt"))

    tool_results = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
    assert "denied" in tool_results[0].tool_result.output.lower()
    assert not (tmp_path / "test.txt").exists()


def test_agent_chat_history_grows(tmp_path, monkeypatch):
    """History accumulates user + assistant + tool messages across the turn."""
    agent = _make_agent(tmp_path, monkeypatch, [
        _make_response(
            content="Reading.",
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "/etc/hostname"})],
        ),
        _make_response(content="Done."),
    ])

    initial_len = len(agent.history)
    asyncio.run(agent.chat("read /etc/hostname"))
    # Should have grown by: user msg + assistant(msg1) + tool result + assistant(msg2) = 4
    assert len(agent.history) == initial_len + 4


def test_agent_chat_iteration_cap(tmp_path, monkeypatch):
    """Agent stops after MAX_ITERATIONS tool-call rounds."""
    # Every response has a tool call — never terminates naturally
    responses = [
        _make_response(
            content=f"Loop {i}",
            tool_calls=[ToolCall(id=f"c{i}", name="read_file", arguments={"path": "/etc/hostname"})],
        )
        for i in range(20)
    ]
    events: List[AgentEvent] = []
    agent = _make_agent(tmp_path, monkeypatch, responses)
    agent.on_event = events.append

    asyncio.run(agent.chat("loop forever"))

    complete_events = [e for e in events if e.type == AgentEventType.COMPLETE]
    assert len(complete_events) >= 1
    assert "12" in complete_events[-1].text or "iterations" in complete_events[-1].text.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
