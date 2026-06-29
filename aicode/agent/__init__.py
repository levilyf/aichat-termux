"""Agent package — agentic loop that drives the model + tools."""

from .loop import Agent, AgentEvent, AgentEventType
from .prompts import build_system_prompt

__all__ = ["Agent", "AgentEvent", "AgentEventType", "build_system_prompt"]
