"""Cost tracking — tokens used + estimated $ per turn and per session.

Pricing is approximate and updated periodically. See PRICING table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# Per-million-token prices in USD. (input, output)
# Source: provider pricing pages, June 2025. Approximate.
PRICING: Dict[str, tuple[float, float]] = {
    # NVIDIA NIM — Llama 3.3 70B pricing (build.nvidia.com credits are free, but list price ~$0.77/$0.77)
    "meta/llama-3.3-70b-instruct": (0.77, 0.77),
    "meta/llama-3.1-405b-instruct": (3.00, 3.00),
    "qwen/qwen2.5-coder-32b-instruct": (0.40, 0.40),
    "mistralai/mixtral-8x7b-instruct-v0.1": (0.27, 0.27),
    "deepseek-ai/deepseek-r1": (1.26, 1.26),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    # Anthropic
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    # Gemini
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-1.5-pro": (1.25, 5.00),
    # Groq
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    # OpenRouter (varies — use Claude 3.5 Sonnet pricing as a reasonable default)
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "openai/gpt-4o": (2.50, 10.00),
    # Ollama — local, free
    "llama3.2:3b": (0.0, 0.0),
    "llama3.1:8b": (0.0, 0.0),
    "qwen2.5-coder:7b": (0.0, 0.0),
}

DEFAULT_PRICE = (1.00, 1.00)  # fallback if model not in table


@dataclass
class TurnCost:
    """Cost for a single turn (one model call)."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def input_cost(self) -> float:
        price_in, _ = PRICING.get(self.model, DEFAULT_PRICE)
        return (self.input_tokens / 1_000_000) * price_in

    @property
    def output_cost(self) -> float:
        _, price_out = PRICING.get(self.model, DEFAULT_PRICE)
        return (self.output_tokens / 1_000_000) * price_out

    @property
    def total_cost(self) -> float:
        return self.input_cost + self.output_cost

    def format(self) -> str:
        return (
            f"{self.model}: "
            f"{self.input_tokens:,} in / {self.output_tokens:,} out = "
            f"${self.total_cost:.4f}"
        )


@dataclass
class SessionCost:
    """Accumulated cost across a whole session."""

    turns: list = field(default_factory=list)
    started_at: float = 0.0  # epoch seconds

    def add(self, turn: TurnCost) -> None:
        self.turns.append(turn)

    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def total_cost(self) -> float:
        return sum(t.total_cost for t in self.turns)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def format_summary(self) -> str:
        return (
            f"**Session totals** — {self.turn_count} turn(s)\n"
            f"- Input tokens:  {self.total_input_tokens:,}\n"
            f"- Output tokens: {self.total_output_tokens:,}\n"
            f"- Total cost:    ${self.total_cost:.4f}"
        )

    def format_breakdown(self) -> str:
        if not self.turns:
            return "(no turns yet)"
        lines = [self.format_summary(), "", "**Per-turn breakdown:**"]
        for i, t in enumerate(self.turns, 1):
            lines.append(f"{i}. {t.format()}")
        return "\n".join(lines)


def parse_usage(usage: dict, model: str) -> TurnCost:
    """Parse the `usage` dict from a Provider response into a TurnCost.

    Handles both OpenAI-style and Anthropic-style keys.
    """
    if not usage:
        return TurnCost(model=model)
    return TurnCost(
        model=model,
        input_tokens=usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
        output_tokens=usage.get("completion_tokens") or usage.get("output_tokens") or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
        cache_write_tokens=usage.get("cache_creation_input_tokens") or 0,
    )
