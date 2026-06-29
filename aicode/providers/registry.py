"""Provider registry + auto-routing logic.

`build_provider(profile)` returns a ready-to-use Provider instance for a Profile.
`Router` picks a profile based on a task type using the config's [routing] section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..config import Config, Profile
from .anthropic import AnthropicProvider
from .base import Provider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider

PROVIDER_CLASSES: Dict[str, type] = {
    "openai": OpenAICompatProvider,
    "nim": OpenAICompatProvider,
    "groq": OpenAICompatProvider,
    "openrouter": OpenAICompatProvider,
    "ollama": OpenAICompatProvider,
    "together": OpenAICompatProvider,
    "fireworks": OpenAICompatProvider,
    "deepseek": OpenAICompatProvider,
    "mistral": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def build_provider(profile: Profile) -> Provider:
    """Instantiate the correct Provider subclass for a Profile."""
    kind = profile.provider.lower()
    cls = PROVIDER_CLASSES.get(kind)
    if cls is None:
        raise ValueError(
            f"unknown provider '{kind}'. Supported: {sorted(PROVIDER_CLASSES)}"
        )
    if cls is OpenAICompatProvider:
        return cls(
            model=profile.model,
            api_key=profile.resolved_api_key(),
            base_url=profile.base_url,
            provider_kind=kind,
            **profile.extra,
        )
    return cls(
        model=profile.model,
        api_key=profile.resolved_api_key(),
        base_url=profile.base_url,
        **profile.extra,
    )


# Lightweight task classifier for auto-routing
_TASK_HINTS = {
    "reasoning": [
        r"\b(debug|why|explain|understand|reason|analy[sz]e|architect)\b",
        r"\b(design|refactor|review|compare|tradeoff)\b",
        r"\b(algorithm|complexity|optimi[sz]e)\b",
    ],
    "coding": [
        r"\b(implement|write|create|build|add|generate)\b.*\b(function|class|method|component|file|api|endpoint|feature)\b",
        r"\b(fix|patch|update|modify|edit|change)\b",
        r"\b(test|spec|lint|format)\b",
        r"```",
    ],
    "simple": [
        r"^(hi|hello|hey|thanks|ok|yes|no)\b",
        r"\b(what is|who is|where is|when is)\b",
        r"\b(define|meaning of)\b",
    ],
}


@dataclass
class TaskClass:
    kind: str  # "reasoning" | "coding" | "simple"
    confidence: float


def classify_task(user_text: str) -> TaskClass:
    """Classify the user's latest message into reasoning/coding/simple."""
    text = user_text.lower()
    scores = {kind: 0 for kind in _TASK_HINTS}
    for kind, patterns in _TASK_HINTS.items():
        for p in patterns:
            if re.search(p, text):
                scores[kind] += 1
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return TaskClass(kind="simple", confidence=0.0)
    total = sum(scores.values())
    return TaskClass(kind=best, confidence=scores[best] / total)


# Preference chains — used as fallback when the configured target isn't usable.
# Ordered by "best fit" for each task type, across all built-in providers.
# The router walks this list and picks the FIRST one that's actually available
# (has a working API key or is ollama).
PREFERENCE_CHAINS: Dict[str, List[str]] = {
    "coding": [
        "nim-coder", "nim",           # Qwen Coder / Llama on NIM (cheap, great at code)
        "claude", "gpt",              # premium reasoning about code
        "openrouter", "groq",         # openrouter has free coders; groq is fast
        "gemini",                     # gemini flash is decent at code
        "ollama",                     # local fallback
    ],
    "reasoning": [
        "claude", "gpt",              # best reasoning models
        "nim", "nim-coder",           # llama-3.3-70b is solid at reasoning
        "openrouter", "gemini",       # openrouter can route to many; gemini pro
        "groq", "ollama",
    ],
    "simple": [
        "groq", "gemini",             # fast + cheap
        "nim", "ollama",              # free / local
        "openrouter", "claude", "gpt",  # last resort
    ],
    "default": [
        "nim", "groq", "claude", "gpt", "gemini", "openrouter", "ollama", "nim-coder",
    ],
}


class Router:
    """Picks a profile for a task using the [routing] section, with smart fallback.

    Auto-routing ONLY ever returns a profile that is currently USABLE — i.e. it has
    a resolved API key (or is ollama). If the configured target is unavailable, the
    router walks PREFERENCE_CHAINS[task_type] and picks the first usable one.

    This means: if you only configured NVIDIA NIM, every task will use NIM, regardless
    of what [routing] says. The router won't send requests to providers you didn't
    set up.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._last_warning: Optional[str] = None

    @property
    def last_warning(self) -> Optional[str]:
        """After a `for_text` / `for_task_type` call, holds a warning string if a
        fallback was used. None if the configured target was used directly."""
        return self._last_warning

    def _pick(self, task_type: str) -> Profile:
        """Walk: configured target → preference chain → any available. Returns a usable profile."""
        self._last_warning = None
        available = self.config.available_profiles()
        if not available:
            raise RuntimeError(
                "no usable profiles — set at least one API key (run `aicode setup`)"
            )

        configured = self.config.routing.for_task(task_type)
        chain = list(PREFERENCE_CHAINS.get(task_type, PREFERENCE_CHAINS["default"]))

        # If the user configured a target that exists in the chain, prioritize it
        if configured and configured not in chain:
            chain.insert(0, configured)

        # Step 1: try configured target first (if usable)
        if configured and self.config.is_profile_usable(configured):
            return self.config.get_profile(configured)

        # Step 2: walk preference chain, pick first usable
        for name in chain:
            if self.config.is_profile_usable(name):
                if configured and configured != name:
                    self._last_warning = (
                        f"configured {task_type} profile '{configured}' is not usable "
                        f"(missing API key) — using '{name}' instead. "
                        f"Run `aicode setup` to fix."
                    )
                return self.config.get_profile(name)

        # Step 3: any available profile
        name = next(iter(available))
        self._last_warning = (
            f"no preference-chain profile was usable for {task_type!r} — "
            f"falling back to '{name}'"
        )
        return available[name]

    def for_text(self, user_text: str) -> Profile:
        cls = classify_task(user_text)
        return self._pick(cls.kind)

    def for_task_type(self, task_type: str) -> Profile:
        return self._pick(task_type)
