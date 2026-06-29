"""Interactive setup wizard — `aicode setup`.

Guides the user through:
  1. Picking which providers to configure (multi-select)
  2. Entering API keys for each (with masking + validation)
  3. Testing each key against the live API
  4. Choosing the default profile + per-task routing
  5. Writing the config to disk + running doctor
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.spinner import Spinner
from rich.text import Text

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
from .providers.base import Message, Provider
from .providers.registry import build_provider

console = Console()

# Friendly metadata for each built-in profile
PROVIDER_META: Dict[str, Dict[str, str]] = {
    "nim": {
        "label": "NVIDIA NIM (free tier, Llama/Qwen/Mistral)",
        "env_var": "NVIDIA_API_KEY",
        "signup_url": "https://build.nvidia.com",
        "key_prefix": "nvapi-",
        "default_model": "meta/llama-3.3-70b-instruct",
        "alt_models": [
            "meta/llama-3.3-70b-instruct",
            "qwen/qwen2.5-coder-32b-instruct",
            "mistralai/mixtral-8x7b-instruct-v0.1",
            "deepseek-ai/deepseek-r1",
            "meta/llama-3.1-405b-instruct",
        ],
    },
    "gpt": {
        "label": "OpenAI (GPT-4o, GPT-4.1, o3)",
        "env_var": "OPENAI_API_KEY",
        "signup_url": "https://platform.openai.com/api-keys",
        "key_prefix": "sk-",
        "default_model": "gpt-4o",
        "alt_models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini"],
    },
    "claude": {
        "label": "Anthropic Claude (Sonnet, Opus, Haiku)",
        "env_var": "ANTHROPIC_API_KEY",
        "signup_url": "https://console.anthropic.com/settings/keys",
        "key_prefix": "sk-ant-",
        "default_model": "claude-3-5-sonnet-20241022",
        "alt_models": [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
    },
    "gemini": {
        "label": "Google Gemini (Pro, Flash)",
        "env_var": "GEMINI_API_KEY",
        "signup_url": "https://aistudio.google.com/app/apikey",
        "key_prefix": "AIza",
        "default_model": "gemini-2.0-flash",
        "alt_models": ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-1.5-pro"],
    },
    "groq": {
        "label": "Groq (very fast, free tier)",
        "env_var": "GROQ_API_KEY",
        "signup_url": "https://console.groq.com/keys",
        "key_prefix": "gsk_",
        "default_model": "llama-3.3-70b-versatile",
        "alt_models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    },
    "openrouter": {
        "label": "OpenRouter (one key → 100+ models)",
        "env_var": "OPENROUTER_API_KEY",
        "signup_url": "https://openrouter.ai/keys",
        "key_prefix": "sk-or-",
        "default_model": "anthropic/claude-3.5-sonnet",
        "alt_models": [
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4o",
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
    },
    "ollama": {
        "label": "Ollama (local, no key needed)",
        "env_var": "",
        "signup_url": "",
        "key_prefix": "",
        "default_model": "llama3.2:3b",
        "alt_models": ["llama3.2:3b", "llama3.1:8b", "qwen2.5-coder:7b"],
    },
}


# ---------- UI helpers ----------

BANNER = r"""
[cyan]
   _____   ___  _____ _____
  /  _  \ / _ \/  ___|  ___|
 /  /_\  / /_\ \ `--.| |__
 |  _  ||  _  |`--. \  __|
 |  | | || | | /\__/ / |___
 \_| |_/\_| |_/\____/\____/
[/cyan]

[dim]AI coding agent for Termux — multi-provider setup wizard[/dim]
"""


def _clear() -> None:
    os.system("clear" if os.name == "posix" else "cls")


def _pause(msg: str = "Press Enter to continue...") -> None:
    Prompt.ask(f"[dim]{msg}[/dim]", default="")


def _print_step(n: int, total: int, title: str) -> None:
    console.print()
    console.rule(f"[bold cyan]Step {n}/{total} — {title}[/bold cyan]")


# ---------- Step 1: Welcome ----------

def step_welcome() -> None:
    _clear()
    console.print(Panel(Text.from_markup(BANNER.strip()), border_style="cyan", padding=(1, 2)))
    console.print(
        "This wizard will:\n"
        "  [cyan]1[/cyan] Ask which providers you want to set up\n"
        "  [cyan]2[/cyan] Prompt for each API key (with validation)\n"
        "  [cyan]3[/cyan] Test each key live against the provider\n"
        "  [cyan]4[/cyan] Pick your default model + auto-routing rules\n"
        "  [cyan]5[/cyan] Write the config to [bold]~/.config/aicode/config.toml[/bold]\n"
    )
    console.print("[dim]You can re-run this any time with: aicode setup[/dim]")
    _pause()


# ---------- Step 2: Provider picker ----------

def step_pick_providers() -> List[str]:
    _clear()
    _print_step(1, 4, "Pick your providers")
    console.print("Choose which providers to configure. Type the numbers, comma-separated.")
    console.print("[dim](You can configure more later by re-running this wizard.)[/dim]\n")
    names = list(PROVIDER_META.keys())
    for i, name in enumerate(names, 1):
        meta = PROVIDER_META[name]
        env_note = f" [dim](env: ${meta['env_var']})[/dim]" if meta["env_var"] else " [dim](no key needed)[/dim]"
        console.print(f"  [cyan]{i}[/cyan]. {meta['label']}{env_note}")
    console.print()
    default = "1"
    raw = Prompt.ask("Pick (e.g. 1,3,5)", default=default)
    indices: List[int] = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            idx = int(tok)
            if 1 <= idx <= len(names):
                indices.append(idx - 1)
        except ValueError:
            pass
    if not indices:
        indices = [0]
    chosen = [names[i] for i in dict.fromkeys(indices)]
    console.print(f"\n[green]✓[/green] Selected: {', '.join(chosen)}")
    _pause()
    return chosen


# ---------- Step 3: API keys ----------

def _mask(s: str) -> str:
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


def _read_key(provider_name: str) -> str:
    meta = PROVIDER_META[provider_name]
    env_var = meta["env_var"]
    # Check env first
    existing = os.environ.get(env_var, "") if env_var else ""
    if existing:
        if Confirm.ask(
            f"Found [green]${env_var}[/green] in your env ([dim]{_mask(existing)}[/dim]). Use it?",
            default=True,
        ):
            return f"${{{env_var}}}"
    # Manual input
    while True:
        console.print(f"\n[dim]Get your key at: {meta['signup_url']}[/dim]")
        key = Prompt.ask(f"Paste your {meta['label'].split(' ')[0]} API key", password=True)
        if not key:
            if Confirm.ask("No key entered — skip this provider?", default=False):
                return ""
            continue
        prefix = meta["key_prefix"]
        if prefix and not key.startswith(prefix) and not Confirm.ask(
            f"[yellow]Key doesn't start with '{prefix}'. Use anyway?[/yellow]", default=False
        ):
            continue
        # Ask whether to save as env var (recommended) or inline in config
        if env_var and Confirm.ask(
            f"Store this in your shell env as [bold]${env_var}[/bold]? (recommended — keeps it out of the config file)",
            default=True,
        ):
            # We won't modify .bashrc automatically (too invasive). Just write the ref.
            console.print(
                f"[yellow]Add this to your ~/.bashrc:[/yellow]\n    export {env_var}=\"{_mask(key)}\""
            )
            return f"${{{env_var}}}"
        return key


def step_collect_keys(chosen: List[str]) -> Dict[str, str]:
    """Returns dict of provider_name -> resolved api_key (literal or ${VAR} ref)."""
    _clear()
    _print_step(2, 4, "Enter API keys")
    keys: Dict[str, str] = {}
    for name in chosen:
        meta = PROVIDER_META[name]
        console.rule(f"[bold]{meta['label']}[/bold]")
        if name == "ollama":
            base_url = Prompt.ask("Ollama base URL", default="http://localhost:11434/v1")
            keys[name] = "ollama"
            # stash base_url on env-like dict using a sentinel attribute
            os.environ["AICODE_OLLAMA_BASE_URL"] = base_url
            continue
        key = _read_key(name)
        if key:
            keys[name] = key
            console.print(f"[green]✓[/green] Stored key for {name}")
        else:
            console.print(f"[yellow]! Skipped {name}[/yellow]")
    _pause()
    return keys


# ---------- Step 4: Test keys ----------

async def _test_provider(profile: Profile) -> Tuple[bool, str]:
    """Run a tiny chat completion to verify the key works. Returns (ok, message)."""
    try:
        provider = build_provider(profile)
    except Exception as e:
        return False, f"build error: {e}"
    try:
        resp = await provider.chat(
            messages=[
                Message(role="system", content="Reply with exactly: ok"),
                Message(role="user", content="ping"),
            ],
            tools=None,
            temperature=0.0,
            max_tokens=10,
        )
        await provider.close()
        snippet = (resp.content or "").strip()[:60]
        return True, f"replied: {snippet!r}"
    except Exception as e:
        return False, f"error: {str(e)[:120]}"


def step_test_keys(chosen: List[str], keys: Dict[str, str], models: Dict[str, str]) -> Dict[str, bool]:
    _clear()
    _print_step(3, 4, "Testing keys live")
    console.print("Sending a tiny test request to each provider...\n")

    results: Dict[str, bool] = {}

    for name in chosen:
        if name not in keys:
            results[name] = False
            continue
        profile = Profile(
            name=name,
            provider=name,
            model=models.get(name, PROVIDER_META[name]["default_model"]),
            api_key=keys[name],
            base_url=(
                os.environ.get("AICODE_OLLAMA_BASE_URL", "http://localhost:11434/v1")
                if name == "ollama"
                else ""
            ),
        )
        meta = PROVIDER_META[name]
        with Live(Spinner("dots", text=f"  Testing [bold]{name}[/bold] — {meta['label'].split(' (')[0]}..."), console=console, refresh_per_second=10) as live:
            ok, msg = asyncio.run(_test_provider(profile))
        if ok:
            console.print(f"  [green]✓[/green] {name}: {msg}")
            results[name] = True
        else:
            console.print(f"  [red]✗[/red] {name}: {msg}")
            results[name] = False

    passed = sum(1 for v in results.values() if v)
    console.print(f"\n[bold]{passed}/{len(results)} providers passed.[/bold]")
    if passed == 0:
        console.print("[red]No providers worked. Check your keys and re-run: aicode setup[/red]")
        sys.exit(1)
    _pause()
    return results


# ---------- Step 5: Pick models ----------

def step_pick_models(chosen: List[str], keys: Dict[str, str]) -> Dict[str, str]:
    _clear()
    _print_step(4, 5, "Pick models")
    models: Dict[str, str] = {}
    for name in chosen:
        if name not in keys:
            continue
        meta = PROVIDER_META[name]
        console.rule(f"[bold]{meta['label']}[/bold]")
        console.print("Available models:")
        for i, m in enumerate(meta["alt_models"], 1):
            default_marker = " (default)" if m == meta["default_model"] else ""
            console.print(f"  [cyan]{i}[/cyan]. {m}{default_marker}")
        idx = IntPrompt.ask(
            "Pick a model number",
            default=1,
            show_default=True,
        )
        if 1 <= idx <= len(meta["alt_models"]):
            models[name] = meta["alt_models"][idx - 1]
        else:
            models[name] = meta["default_model"]
    _pause()
    return models


# ---------- Step 6: Routing ----------

def step_pick_routing(working_profiles: List[str]) -> RoutingConfig:
    _clear()
    _print_step(5, 5, "Configure auto-routing")
    console.print(
        "aicode auto-picks the best model for each task. "
        "Map each task type to a profile (or pick the same one for all).\n"
    )
    for i, p in enumerate(working_profiles, 1):
        console.print(f"  [cyan]{i}[/cyan]. {p}")

    def _pick(prompt: str, default_idx: int) -> str:
        idx = IntPrompt.ask(prompt, default=default_idx, show_default=True)
        if 1 <= idx <= len(working_profiles):
            return working_profiles[idx - 1]
        return working_profiles[default_idx - 1]

    # Heuristic defaults: first working profile for everything, but prefer nim/groq/claude if present
    def _default_for(preferred: List[str]) -> int:
        for pref in preferred:
            if pref in working_profiles:
                return working_profiles.index(pref) + 1
        return 1

    coding = _pick("Profile for [bold]coding[/bold] tasks", _default_for(["nim", "openrouter", "claude", "gpt"]))
    reasoning = _pick("Profile for [bold]reasoning[/bold] tasks", _default_for(["claude", "gpt", "nim"]))
    simple = _pick("Profile for [bold]simple[/bold] tasks", _default_for(["groq", "gemini", "ollama"]))
    default = _pick("[bold]Default[/bold] profile (fallback)", _default_for(["nim", "gpt", "claude", working_profiles[0]]))

    console.print(f"\n[green]✓[/green] coding → {coding}, reasoning → {reasoning}, simple → {simple}, default → {default}")
    _pause()
    return RoutingConfig(coding=coding, reasoning=reasoning, simple=simple, default=default)


# ---------- Step 7: Write config ----------

def step_write_config(
    chosen: List[str],
    keys: Dict[str, str],
    models: Dict[str, str],
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
            "model": models.get(name, meta["default_model"]),
            "api_key": keys[name],
        }
        if name == "ollama":
            entry["base_url"] = os.environ.get("AICODE_OLLAMA_BASE_URL", "http://localhost:11434/v1")
        elif name in {"nim", "groq", "openrouter", "openai"}:
            # Pre-fill known base_url for clarity in the config file
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
    step_welcome()
    chosen = step_pick_providers()
    keys = step_collect_keys(chosen)
    if not keys:
        console.print("[red]No API keys provided. Aborting.[/red]")
        return 1
    models = step_pick_models(chosen, keys)
    test_results = step_test_keys(chosen, keys, models)
    # Filter to working profiles for routing
    working = [n for n in chosen if test_results.get(n)]
    if not working:
        console.print("[red]No providers passed testing. Fix your keys and re-run: aicode setup[/red]")
        return 1
    routing = step_pick_routing(working)
    path = step_write_config(chosen, keys, models, routing, config_path)

    _clear()
    console.print(Panel(
        f"[green bold]Setup complete![/green bold]\n\n"
        f"Config written to: [bold]{path}[/bold]\n\n"
        f"Working providers: {', '.join(working)}\n"
        f"Default profile: [bold]{routing.default}[/bold]\n\n"
        f"Next: run [bold cyan]aicode[/bold cyan] to launch the TUI.",
        title="aicode setup",
        border_style="green",
    ))

    # Offer to run doctor
    if Confirm.ask("\nRun doctor now?", default=True):
        from .__main__ import cmd_doctor
        import argparse
        cmd_doctor(argparse.Namespace(config=str(path) if path else None))
    return 0
