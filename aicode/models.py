"""Fetch available models from each provider's API.

Used by the setup wizard to let users pick from live model lists instead of
hardcoded defaults. Each fetcher returns a list of ModelInfo objects.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from .config import Profile
from .providers.openai_compat import PROVIDER_DEFAULTS


@dataclass
class ModelInfo:
    """A model offered by a provider."""

    id: str  # the model ID to use in API calls
    name: str = ""  # human-friendly name
    description: str = ""
    context_length: Optional[int] = None
    owned_by: str = ""
    # Free tier? Used to flag in the picker
    is_free: bool = False
    # Capabilities (best-effort detection)
    supports_tools: bool = True
    supports_vision: bool = False

    @property
    def display_name(self) -> str:
        """What to show in the picker."""
        if self.name and self.name != self.id:
            return f"{self.name} ({self.id})"
        return self.id

    @property
    def picker_line(self) -> str:
        """Full line for the picker, with metadata."""
        parts = [self.display_name]
        if self.context_length:
            parts.append(f"ctx={self.context_length:,}")
        if self.is_free:
            parts.append("FREE")
        if self.description:
            # truncate description
            desc = self.description[:80]
            if len(self.description) > 80:
                desc += "..."
            parts.append(desc)
        return " | ".join(parts)


# Known model metadata — used to enrich API-fetched lists with descriptions,
# vision/tool support flags, and free-tier markers. Also used as a fallback
# when the API list call fails.
KNOWN_MODELS: Dict[str, dict] = {
    # NVIDIA NIM
    "meta/llama-3.3-70b-instruct": {"name": "Llama 3.3 70B", "context": 131072, "is_free": True, "desc": "Meta's latest 70B — great all-rounder"},
    "meta/llama-3.1-405b-instruct": {"name": "Llama 3.1 405B", "context": 131072, "is_free": True, "desc": "Largest Llama — best reasoning"},
    "meta/llama-3.1-70b-instruct": {"name": "Llama 3.1 70B", "context": 131072, "is_free": True, "desc": "Solid mid-size model"},
    "meta/llama-3.1-8b-instruct": {"name": "Llama 3.1 8B", "context": 131072, "is_free": True, "desc": "Fast small model"},
    "qwen/qwen2.5-coder-32b-instruct": {"name": "Qwen 2.5 Coder 32B", "context": 131072, "is_free": True, "desc": "Excellent for coding"},
    "qwen/qwen2.5-72b-instruct": {"name": "Qwen 2.5 72B", "context": 32768, "is_free": True, "desc": "Strong general purpose"},
    "mistralai/mixtral-8x7b-instruct-v0.1": {"name": "Mixtral 8x7B", "context": 32768, "is_free": True, "desc": "MoE — fast and capable"},
    "mistralai/mistral-7b-instruct-v0.3": {"name": "Mistral 7B", "context": 32768, "is_free": True, "desc": "Compact and efficient"},
    "deepseek-ai/deepseek-r1": {"name": "DeepSeek R1", "context": 131072, "is_free": True, "desc": "Reasoning model, CoT"},
    "nvidia/llama-3.1-nemotron-70b-instruct": {"name": "Nemotron 70B", "context": 131072, "is_free": True, "desc": "NVIDIA-tuned Llama"},
    "google/gemma-7b": {"name": "Gemma 7B", "context": 8192, "is_free": True, "desc": "Google's open model"},
    "microsoft/phi-3-medium-4k-instruct": {"name": "Phi-3 Medium", "context": 4096, "is_free": True, "desc": "Microsoft's small model"},
    # OpenAI
    "gpt-4o": {"name": "GPT-4o", "context": 128000, "desc": "Flagship multimodal", "vision": True},
    "gpt-4o-mini": {"name": "GPT-4o mini", "context": 128000, "desc": "Cheap and fast", "vision": True},
    "gpt-4.1": {"name": "GPT-4.1", "context": 1047576, "desc": "Latest GPT-4 — long context", "vision": True},
    "gpt-4.1-mini": {"name": "GPT-4.1 mini", "context": 1047576, "desc": "Cheaper GPT-4.1", "vision": True},
    "gpt-4-turbo": {"name": "GPT-4 Turbo", "context": 128000, "desc": "GPT-4 with vision", "vision": True},
    "gpt-3.5-turbo": {"name": "GPT-3.5 Turbo", "context": 16385, "desc": "Cheap legacy model"},
    "o1": {"name": "o1", "context": 200000, "desc": "Reasoning model", "tools": False},
    "o1-preview": {"name": "o1 preview", "context": 128000, "desc": "Reasoning preview", "tools": False},
    "o1-mini": {"name": "o1 mini", "context": 65536, "desc": "Smaller reasoning", "tools": False},
    "o3-mini": {"name": "o3-mini", "context": 200000, "desc": "Latest reasoning model"},
    # Anthropic
    "claude-3-5-sonnet-20241022": {"name": "Claude 3.5 Sonnet", "context": 200000, "desc": "Best Claude — great for coding", "vision": True},
    "claude-3-5-haiku-20241022": {"name": "Claude 3.5 Haiku", "context": 200000, "desc": "Fast and cheap", "vision": True},
    "claude-3-opus-20240229": {"name": "Claude 3 Opus", "context": 200000, "desc": "Most capable Claude 3", "vision": True},
    "claude-3-sonnet-20240229": {"name": "Claude 3 Sonnet", "context": 200000, "desc": "Balanced", "vision": True},
    "claude-3-haiku-20240307": {"name": "Claude 3 Haiku", "context": 200000, "desc": "Fastest Claude", "vision": True},
    # Gemini
    "gemini-2.0-flash": {"name": "Gemini 2.0 Flash", "context": 1048576, "desc": "Fast multimodal", "vision": True},
    "gemini-2.0-flash-thinking-exp": {"name": "Gemini 2.0 Flash Thinking", "context": 1048576, "desc": "Reasoning variant", "vision": True},
    "gemini-2.5-pro": {"name": "Gemini 2.5 Pro", "context": 1048576, "desc": "Latest Pro", "vision": True},
    "gemini-2.5-flash": {"name": "Gemini 2.5 Flash", "context": 1048576, "desc": "Latest Flash", "vision": True},
    "gemini-1.5-pro": {"name": "Gemini 1.5 Pro", "context": 2097152, "desc": "2M context", "vision": True},
    "gemini-1.5-flash": {"name": "Gemini 1.5 Flash", "context": 1048576, "desc": "Fast 1.5", "vision": True},
    "gemini-1.5-flash-8b": {"name": "Gemini 1.5 Flash 8B", "context": 1048576, "desc": "Smallest Gemini", "vision": True},
    # Groq
    "llama-3.3-70b-versatile": {"name": "Llama 3.3 70B Versatile", "context": 131072, "desc": "Latest Llama on Groq"},
    "llama-3.1-8b-instant": {"name": "Llama 3.1 8B Instant", "context": 131072, "desc": "Very fast"},
    "llama-3.1-70b-versatile": {"name": "Llama 3.1 70B Versatile", "context": 131072, "desc": "Larger Llama"},
    "llama3-70b-8192": {"name": "Llama 3 70B", "context": 8192, "desc": "Legacy Llama 3"},
    "llama3-8b-8192": {"name": "Llama 3 8B", "context": 8192, "desc": "Legacy Llama 3 small"},
    "mixtral-8x7b-32768": {"name": "Mixtral 8x7B", "context": 32768, "desc": "MoE on Groq"},
    "gemma2-9b-it": {"name": "Gemma 2 9B", "context": 8192, "desc": "Google's Gemma 2"},
    # OpenRouter (free models — the rest are passthrough)
    "anthropic/claude-3.5-sonnet": {"name": "Claude 3.5 Sonnet (via OpenRouter)", "desc": "Anthropic via OR"},
    "openai/gpt-4o": {"name": "GPT-4o (via OpenRouter)", "desc": "OpenAI via OR"},
    "google/gemini-2.0-flash-exp:free": {"name": "Gemini 2.0 Flash (free)", "is_free": True, "desc": "Free Gemini on OR"},
    "meta-llama/llama-3.3-70b-instruct:free": {"name": "Llama 3.3 70B (free)", "is_free": True, "desc": "Free Llama on OR"},
    "deepseek/deepseek-r1:free": {"name": "DeepSeek R1 (free)", "is_free": True, "desc": "Free DeepSeek R1 on OR"},
    "qwen/qwen-2.5-coder-32b-instruct:free": {"name": "Qwen 2.5 Coder (free)", "is_free": True, "desc": "Free Qwen Coder on OR"},
    # Ollama (local)
    "llama3.2:3b": {"name": "Llama 3.2 3B", "context": 4096, "is_free": True, "desc": "Small local model"},
    "llama3.2:1b": {"name": "Llama 3.2 1B", "context": 4096, "is_free": True, "desc": "Tiny local model"},
    "llama3.1:8b": {"name": "Llama 3.1 8B", "context": 4096, "is_free": True, "desc": "Local Llama 3.1"},
    "qwen2.5-coder:7b": {"name": "Qwen 2.5 Coder 7B", "context": 4096, "is_free": True, "desc": "Local coding model"},
    "mistral:7b": {"name": "Mistral 7B", "context": 4096, "is_free": True, "desc": "Local Mistral"},
    "phi3:mini": {"name": "Phi-3 Mini", "context": 4096, "is_free": True, "desc": "Local Phi-3"},
}


def _enrich(model_id: str) -> ModelInfo:
    """Build a ModelInfo from a bare ID, enriched with known metadata."""
    known = KNOWN_MODELS.get(model_id, {})
    return ModelInfo(
        id=model_id,
        name=known.get("name", ""),
        description=known.get("desc", ""),
        context_length=known.get("context"),
        owned_by=known.get("owned", ""),
        is_free=known.get("is_free", False),
        supports_tools=known.get("tools", True),
        supports_vision=known.get("vision", False),
    )


async def fetch_openai_compat_models(
    api_key: str,
    base_url: str = "",
    provider_kind: str = "openai",
) -> List[ModelInfo]:
    """Fetch models from any OpenAI-compatible /models endpoint.

    Works for: OpenAI, NVIDIA NIM, Groq, OpenRouter, Ollama, Together, etc.
    """
    if not base_url:
        base_url = PROVIDER_DEFAULTS.get(provider_kind, {}).get("base_url", "")
    if not base_url:
        raise ValueError(f"no base_url for provider {provider_kind}")

    headers = {"Accept": "application/json"}
    auth_prefix = PROVIDER_DEFAULTS.get(provider_kind, {}).get("auth_prefix", "Bearer ")
    if api_key and api_key != "ollama":
        headers["Authorization"] = f"{auth_prefix}{api_key}"
    if provider_kind == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/aicode-termux"
        headers["X-Title"] = "aicode-termux"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=15.0) as client:
        resp = await client.get("/models")
        resp.raise_for_status()
        data = resp.json()

    models: List[ModelInfo] = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        if not model_id:
            continue
        known = KNOWN_MODELS.get(model_id, {})
        # OpenRouter marks free models with ":free" suffix
        is_free = known.get("is_free", False) or model_id.endswith(":free")
        # NIM sometimes exposes "analyze" or "classify" variants — skip non-chat
        if any(skip in model_id.lower() for skip in ["-vision", "-embed", "-rerank", "-guard", "-classify"]):
            # Allow vision models we know about
            if known.get("vision") is not True:
                continue
        models.append(ModelInfo(
            id=model_id,
            name=known.get("name", item.get("name", "")),
            description=known.get("desc", ""),
            context_length=known.get("context") or item.get("context_length") or item.get("max_model_len"),
            owned_by=item.get("owned_by", ""),
            is_free=is_free,
            supports_tools=known.get("tools", True),
            supports_vision=known.get("vision", False),
        ))

    # Sort: free first, then by name
    models.sort(key=lambda m: (not m.is_free, m.id.lower()))
    return models


async def fetch_anthropic_models(api_key: str) -> List[ModelInfo]:
    """Fetch models from Anthropic's /v1/models endpoint."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    async with httpx.AsyncClient(base_url="https://api.anthropic.com/v1", headers=headers, timeout=15.0) as client:
        resp = await client.get("/models")
        resp.raise_for_status()
        data = resp.json()

    models: List[ModelInfo] = []
    for item in data.get("data", []):
        model_id = item.get("id", "")
        if not model_id:
            continue
        known = KNOWN_MODELS.get(model_id, {})
        models.append(ModelInfo(
            id=model_id,
            name=known.get("name", item.get("display_name", "")),
            description=known.get("desc", ""),
            context_length=known.get("context"),
            owned_by=item.get("created_by", "anthropic"),
            is_free=known.get("is_free", False),
            supports_tools=known.get("tools", True),
            supports_vision=known.get("vision", False),
        ))
    models.sort(key=lambda m: m.id.lower())
    return models


async def fetch_gemini_models(api_key: str) -> List[ModelInfo]:
    """Fetch models from Gemini's v1beta/models endpoint."""
    params = {"key": api_key, "pageSize": 100}
    async with httpx.AsyncClient(base_url="https://generativelanguage.googleapis.com/v1beta", timeout=15.0) as client:
        resp = await client.get("/models", params=params)
        resp.raise_for_status()
        data = resp.json()

    models: List[ModelInfo] = []
    for item in data.get("models", []):
        # Gemini returns "models/gemini-2.0-flash" — strip the prefix
        name = item.get("name", "")
        if name.startswith("models/"):
            name = name[len("models/"):]
        if not name:
            continue
        # Skip non-generative models (embeddings, etc.)
        supported = item.get("supportedGenerationMethods", [])
        if "generateContent" not in supported:
            continue
        known = KNOWN_MODELS.get(name, {})
        models.append(ModelInfo(
            id=name,
            name=known.get("name", item.get("displayName", name)),
            description=known.get("desc", ""),
            context_length=known.get("context") or item.get("inputTokenLimit"),
            owned_by="google",
            is_free=known.get("is_free", False),
            supports_tools=known.get("tools", True),
            supports_vision=known.get("vision", False),
        ))
    models.sort(key=lambda m: m.id.lower())
    return models


async def fetch_ollama_models(base_url: str = "http://localhost:11434") -> List[ModelInfo]:
    """Fetch locally installed Ollama models."""
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
            resp = await client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        # Ollama not running — return known defaults
        return [_enrich(m) for m in ["llama3.2:3b", "llama3.1:8b", "qwen2.5-coder:7b"]]

    models: List[ModelInfo] = []
    for item in data.get("models", []):
        name = item.get("name", "")
        if not name:
            continue
        known = KNOWN_MODELS.get(name, {})
        models.append(ModelInfo(
            id=name,
            name=known.get("name", name),
            description=known.get("desc", f"{item.get('size', 0) // 1_000_000_000}GB"),
            context_length=known.get("context"),
            owned_by="ollama",
            is_free=True,
            supports_tools=known.get("tools", True),
            supports_vision=known.get("vision", False),
        ))
    return models


async def fetch_models_for_profile(profile: Profile) -> List[ModelInfo]:
    """High-level: fetch models for a Profile (provider + key + base_url)."""
    kind = profile.provider
    key = profile.resolved_api_key()
    try:
        if kind in {"openai", "nim", "groq", "openrouter", "ollama", "together", "fireworks", "deepseek", "mistral"}:
            if kind == "ollama":
                return await fetch_ollama_models(profile.base_url or "http://localhost:11434")
            return await fetch_openai_compat_models(key, profile.base_url, kind)
        elif kind == "anthropic":
            return await fetch_anthropic_models(key)
        elif kind == "gemini":
            return await fetch_gemini_models(key)
        else:
            return []
    except Exception:
        # Fallback to known defaults for this provider
        return _fallback_models(kind)


def _fallback_models(provider: str) -> List[ModelInfo]:
    """Return known default models for a provider when the API list fails."""
    fallbacks = {
        "nim": [
            "meta/llama-3.3-70b-instruct",
            "qwen/qwen2.5-coder-32b-instruct",
            "meta/llama-3.1-405b-instruct",
            "deepseek-ai/deepseek-r1",
            "mistralai/mixtral-8x7b-instruct-v0.1",
        ],
        "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini"],
        "anthropic": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
        "gemini": ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-pro", "gemini-1.5-flash"],
        "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
        "openrouter": [
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4o",
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
        "ollama": ["llama3.2:3b", "llama3.1:8b", "qwen2.5-coder:7b"],
    }
    return [_enrich(m) for m in fallbacks.get(provider, [])]
