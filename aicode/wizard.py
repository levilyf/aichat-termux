"""Interactive setup wizard v4 — beautiful modern CLI.

Uses questionary for interactive prompts, rich panels for headers, and
the new ui.py module for splash screens, spinners, and summary tables.

Flow:
  1. Splash screen
  2. Multi-select providers (questionary checkbox)
  3. Enter API key for each (masked, env detection, signup URL)
  4. Test each key + fetch live model list (with spinner)
  5. Pick a model per provider (searchable select)
  6. Configure routing (questionary select showing picked models)
  7. Review summary → write config → run doctor
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from . import __version__
from .config import (
    Config,
    Profile,
    RoutingConfig,
    ShellConfig,
    UIConfig,
    DEFAULT_CONFIG_PATH,
    default_config,
    write_default_config,
)
from .models import ModelInfo, fetch_models_for_profile, _fallback_models
from .providers.base import Message, Provider
from .providers.registry import build_provider
from . import ui

console = ui.console

# Provider metadata — same as before but exposed for the UI layer
PROVIDER_META: Dict[str, Dict[str, str]] = {
    "nim": {
        "label": "NVIDIA NIM",
        "tagline": "Free tier — Llama, Qwen, Mistral, DeepSeek",
        "env_var": "NVIDIA_API_KEY",
        "signup_url": "https://build.nvidia.com",
        "key_prefix": "nvapi-",
        "default_model": "meta/llama-3.3-70b-instruct",
        "free": True,
    },
    "openai": {
        "label": "OpenAI",
        "tagline": "GPT-4o, GPT-4.1, o3 — paid",
        "env_var": "OPENAI_API_KEY",
        "signup_url": "https://platform.openai.com/api-keys",
        "key_prefix": "sk-",
        "default_model": "gpt-4o",
        "free": False,
    },
    "claude": {
        "label": "Anthropic Claude",
        "tagline": "Claude 3.5 Sonnet — best for coding, paid",
        "env_var": "ANTHROPIC_API_KEY",
        "signup_url": "https://console.anthropic.com/settings/keys",
        "key_prefix": "sk-ant-",
        "default_model": "claude-3-5-sonnet-20241022",
        "free": False,
    },
    "gemini": {
        "label": "Google Gemini",
        "tagline": "Gemini 2.0/2.5 — free tier available",
        "env_var": "GEMINI_API_KEY",
        "signup_url": "https://aistudio.google.com/app/apikey",
        "key_prefix": "AIza",
        "default_model": "gemini-2.0-flash",
        "free": True,
    },
    "groq": {
        "label": "Groq",
        "tagline": "Very fast inference — free tier",
        "env_var": "GROQ_API_KEY",
        "signup_url": "https://console.groq.com/keys",
        "key_prefix": "gsk_",
        "default_model": "llama-3.3-70b-versatile",
        "free": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "tagline": "One key → 100+ models (incl. free ones)",
        "env_var": "OPENROUTER_API_KEY",
        "signup_url": "https://openrouter.ai/keys",
        "key_prefix": "sk-or-",
        "default_model": "anthropic/claude-3.5-sonnet",
        "free": True,
    },
    "ollama": {
        "label": "Ollama (local)",
        "tagline": "Run models on-device — no API key",
        "env_var": "",
        "signup_url": "",
        "key_prefix": "",
        "default_model": "llama3.2:3b",
        "free": True,
    },
}


# ---------- Step 1: Splash ----------

def step_splash() -> None:
    ui.show_splash(
        version=__version__,
        subtitle="Setup wizard — let's get you coding",
    )


# ---------- Step 2: Pick providers ----------

def step_pick_providers() -> List[str]:
    providers_list = [
        {"name": name, **meta}
        for name, meta in PROVIDER_META.items()
    ]
    return ui.pick_providers_interactive(providers_list)


# ---------- Step 3: API keys ----------

def step_collect_keys(chosen: List[str]) -> Dict[str, str]:
    ui._clear()
    ui.show_info_panel(
        "Step 2 of 5 — API Keys",
        "Paste your API keys below. They will be validated against each provider before continuing.",
        color="cyan",
    )

    keys: Dict[str, str] = {}
    for name in chosen:
        meta = PROVIDER_META[name]
        console.rule(f"[bold]{meta['label']}[/bold] — {meta['tagline']}")

        if name == "ollama":
            base_url = Prompt.ask("Ollama base URL", default="http://localhost:11434/v1")
            keys[name] = "ollama"
            os.environ["AICODE_OLLAMA_BASE_URL"] = base_url
            ui.show_success(f"Ollama configured at {base_url}")
            continue

        key = ui.ask_api_key(
            provider_label=meta["label"],
            signup_url=meta["signup_url"],
            env_var=meta["env_var"],
            key_prefix=meta["key_prefix"],
        )
        if key:
            keys[name] = key
            ui.show_success(f"Key stored for {name}")
        else:
            console.print(f"[yellow]! Skipped {name}[/yellow]")

    return keys


# ---------- Step 4: Test + fetch models ----------

async def _test_and_fetch(profile: Profile) -> Tuple[bool, str, List[ModelInfo]]:
    """Test the key by fetching models + doing a tiny chat completion."""
    try:
        models = await fetch_models_for_profile(profile)
        if not models:
            return False, "no models returned", []
        provider = build_provider(profile)
        try:
            resp = await provider.chat(
                messages=[
                    Message(role="system", content="Reply: ok"),
                    Message(role="user", content="ping"),
                ],
                tools=None,
                temperature=0.0,
                max_tokens=5,
            )
            await provider.close()
            snippet = (resp.content or "").strip()[:40]
            return True, f"replied: {snippet!r}", models
        except Exception:
            return True, f"key works ({len(models)} models available)", models
    except Exception as e:
        fallback = _fallback_models(profile.provider)
        return False, f"API error: {str(e)[:100]}", fallback


def step_test_and_fetch(
    chosen: List[str],
    keys: Dict[str, str],
) -> Dict[str, Tuple[bool, str, List[ModelInfo]]]:
    ui._clear()
    ui.show_info_panel(
        "Step 3 of 5 — Validating keys & fetching models",
        "Each key will be tested and the live model list fetched.",
        color="cyan",
    )

    results: Dict[str, Tuple[bool, str, List[ModelInfo]]] = {}
    for name in chosen:
        if name not in keys:
            results[name] = (False, "skipped", [])
            continue
        profile = Profile(
            name=name,
            provider=name,
            model=PROVIDER_META[name]["default_model"],
            api_key=keys[name],
            base_url=(
                os.environ.get("AICODE_OLLAMA_BASE_URL", "http://localhost:11434/v1")
                if name == "ollama"
                else ""
            ),
        )
        meta = PROVIDER_META[name]
        # Use the spinner helper
        result = ui.with_spinner(
            f"Testing {meta['label']}...",
            lambda: asyncio.run(_test_and_fetch(profile)),
        )
        ok, msg, models = result
        if ok:
            ui.show_success(f"{name}", f"{msg}\n{len(models)} models available")
        else:
            ui.show_error(f"{name}: {msg}")
            if models:
                console.print(f"  [dim]Using {len(models)} fallback models[/dim]")
        results[name] = (ok, msg, models)

    passed = sum(1 for ok, _, _ in results.values() if ok)
    console.print(f"\n[bold]{passed}/{len(results)} providers passed.[/bold]")
    if passed == 0:
        ui.show_error(
            "No providers worked",
            "Check your keys and re-run: aicode setup",
        )
        sys.exit(1)
    return results


# ---------- Step 5: Pick models ----------

def step_pick_models(
    chosen: List[str],
    keys: Dict[str, str],
    fetch_results: Dict[str, Tuple[bool, str, List[ModelInfo]]],
) -> Dict[str, str]:
    ui._clear()
    ui.show_info_panel(
        "Step 4 of 5 — Pick your models",
        "For each provider, browse the live model list and pick one. Type to search.",
        color="cyan",
    )

    models_picked: Dict[str, str] = {}
    for name in chosen:
        if name not in keys:
            continue
        ok, msg, models = fetch_results.get(name, (False, "", []))
        if not ok and not models:
            console.print(f"[yellow]Skipping {name} — no models[/yellow]")
            continue
        meta = PROVIDER_META[name]
        console.print(f"\n[bold]{meta['label']}[/bold] — {len(models)} model(s)")
        picked = ui.pick_model_interactive(
            provider_label=meta["label"],
            models=models,
            default_model_id=meta["default_model"],
        )
        if picked:
            models_picked[name] = picked
            ui.show_success(f"Picked: {picked}")
    return models_picked


# ---------- Step 6: Routing ----------

def step_pick_routing(
    working_profiles: List[str],
    models_picked: Dict[str, str],
) -> RoutingConfig:
    ui._clear()
    ui.show_info_panel(
        "Step 5 of 5 — Auto-routing",
        "aicode auto-picks the best model for each task type. Map each task type to a profile.",
        color="cyan",
    )

    # Build a label→model lookup for display
    profile_models = {
        p: models_picked.get(p, PROVIDER_META[p]["default_model"])
        for p in working_profiles
    }

    def _default_for(preferred: List[str]) -> str:
        for pref in preferred:
            if pref in working_profiles:
                return pref
        return working_profiles[0]

    def _pick(task_type: str, preferred: List[str]) -> str:
        # Build choices with model names
        import questionary
        choices = []
        for p in working_profiles:
            label = f"{p}  [dim]— {profile_models[p]}[/dim]"
            is_default = p == _default_for(preferred)
            choices.append(questionary.Choice(title=label, value=p, checked=is_default))
        result = questionary.select(
            f"Profile for [bold]{task_type}[/bold] tasks:",
            choices=choices,
            style=ui.QSTYLE,
        ).ask()
        return result or _default_for(preferred)

    coding = _pick("coding", ["nim", "openrouter", "claude", "openai"])
    reasoning = _pick("reasoning", ["claude", "openai", "nim"])
    simple = _pick("simple", ["groq", "gemini", "ollama"])
    default = _pick("default (fallback)", ["nim", "openai", "claude", working_profiles[0]])

    ui.show_success(
        "Routing configured",
        f"coding → {coding} | reasoning → {reasoning} | simple → {simple} | default → {default}",
    )
    return RoutingConfig(coding=coding, reasoning=reasoning, simple=simple, default=default)


# ---------- Step 7: Summary + write ----------

def step_summary(
    chosen: List[str],
    keys: Dict[str, str],
    models_picked: Dict[str, str],
    routing: RoutingConfig,
) -> None:
    profiles_data = []
    for name in chosen:
        if name not in keys:
            continue
        meta = PROVIDER_META[name]
        model = models_picked.get(name, meta["default_model"])
        key_disp = ui._mask(keys[name]) if keys[name] and not keys[name].startswith("${") else keys[name]
        profiles_data.append({
            "name": name,
            "label": meta["label"],
            "model": model,
            "key": key_disp,
        })

    routing_data = {
        "coding": routing.coding,
        "reasoning": routing.reasoning,
        "simple": routing.simple,
        "default": routing.default,
    }
    ui.show_config_summary(profiles_data, routing_data)


def step_write_config(
    chosen: List[str],
    keys: Dict[str, str],
    models_picked: Dict[str, str],
    routing: RoutingConfig,
    path: Optional[Path] = None,
) -> Path:
    import tomli_w

    target = path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    profiles_data: Dict[str, Dict] = {}
    for name in chosen:
        if name not in keys:
            continue
        meta = PROVIDER_META[name]
        entry = {
            "provider": name,
            "model": models_picked.get(name, meta["default_model"]),
            "api_key": keys[name],
        }
        if name == "ollama":
            entry["base_url"] = os.environ.get("AICODE_OLLAMA_BASE_URL", "http://localhost:11434/v1")
        elif name in {"nim", "groq", "openrouter", "openai"}:
            from .providers.openai_compat import PROVIDER_DEFAULTS
            entry["base_url"] = PROVIDER_DEFAULTS.get(name, {}).get("base_url", "")
        profiles_data[name] = entry

    data = {
        "profiles": profiles_data,
        "routing": routing.__dict__,
        "shell": ShellConfig().__dict__,
        "ui": UIConfig().__dict__,
    }
    with open(target, "wb") as f:
        tomli_w.dump(data, f)
    return target


# ---------- Orchestrator ----------

def run_wizard(config_path: Optional[Path] = None) -> int:
    """Top-level entry point for `aicode setup`."""
    step_splash()
    chosen = step_pick_providers()
    keys = step_collect_keys(chosen)
    if not keys:
        ui.show_error("No API keys provided")
        return 1

    fetch_results = step_test_and_fetch(chosen, keys)
    models_picked = step_pick_models(chosen, keys, fetch_results)

    working = [n for n in chosen if fetch_results.get(n, (False,))[0]]
    if not working:
        ui.show_error(
            "No providers passed testing",
            "Fix your keys and re-run: aicode setup",
        )
        return 1

    routing = step_pick_routing(working, models_picked)
    step_summary(chosen, keys, models_picked, routing)

    if not ui.confirm("Write config and finish?", default=True):
        console.print("[yellow]Aborted — no config written.[/yellow]")
        return 1

    path = step_write_config(chosen, keys, models_picked, routing, config_path)

    ui.show_success(
        "Setup complete!",
        f"Config: [bold]{path}[/bold]\n\n"
        f"Working providers: {', '.join(working)}\n"
        f"Default profile: [bold]{routing.default}[/bold]\n\n"
        f"Next: run [bold cyan]aicode[/bold cyan] to launch the TUI.",
    )

    if ui.confirm("\nRun doctor now?", default=True):
        from .__main__ import cmd_doctor
        import argparse
        cmd_doctor(argparse.Namespace(config=str(path) if path else None))
    return 0
