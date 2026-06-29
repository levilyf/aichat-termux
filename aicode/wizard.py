"""Interactive setup wizard v2 — `aicode setup`.

Streamlined flow with live model fetching:
  1. Pick providers (numbered menu, free-tier marked)
  2. Enter API key for each (with env detection + signup URL)
  3. Test key live (with spinner)
  4. Fetch available models from the provider's API
  5. Pick a model from the live list (paginated, searchable)
  6. Configure routing (pick profile per task type)
  7. Review summary → write config → run doctor
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
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.spinner import Spinner
from rich.table import Table
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
from .models import ModelInfo, fetch_models_for_profile, _fallback_models
from .providers.base import Message, Provider
from .providers.registry import build_provider

console = Console()

# Provider metadata
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


# ---------- UI helpers ----------

BANNER = """
[cyan]
     _   ___ ___ ___ _  _      _   ___ ___ ___
    /_\\ | _ \\ __| _ \\ \\| |____| | | __/ __| _ \\
   / _ \\|  _/ _||   / .` |____| |_| _|| (__|   /
  /_/ \\_\\_| |___|_|_\\_|\\_|     \\___|___|___|_\\_\\
[/cyan]

[dim]AI coding agent for Termux — multi-provider setup wizard[/dim]
"""


def _clear() -> None:
    os.system("clear" if os.name == "posix" else "cls")


def _pause(msg: str = "Press Enter to continue...") -> None:
    Prompt.ask(f"[dim]{msg}[/dim]", default="")


def _mask(s: str) -> str:
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


# ---------- Step 1: Welcome ----------

def step_welcome() -> None:
    _clear()
    console.print(Panel(Text.from_markup(BANNER.strip()), border_style="cyan", padding=(1, 2)))
    console.print(
        "This wizard will:\n"
        "  [cyan]1[/cyan]. Let you pick which providers to set up\n"
        "  [cyan]2[/cyan]. Test each API key live\n"
        "  [cyan]3[/cyan]. [bold]Fetch the actual model list[/bold] from each provider\n"
        "  [cyan]4[/cyan]. Let you browse and pick a model per provider\n"
        "  [cyan]5[/cyan]. Configure auto-routing\n"
        "  [cyan]6[/cyan]. Write config to [bold]~/.config/aicode/config.toml[/bold]\n"
    )
    console.print("[dim]You can re-run this any time with: aicode setup[/dim]")
    _pause()


# ---------- Step 2: Provider picker ----------

def step_pick_providers() -> List[str]:
    _clear()
    console.rule("[bold cyan]Step 1 of 5 — Pick your providers[/bold cyan]")
    console.print()
    console.print("Choose which providers to configure. Type the numbers, comma-separated.")
    console.print("[dim](Free-tier providers are marked with 🆓)[/dim]\n")

    names = list(PROVIDER_META.keys())
    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("#", style="cyan", width=3)
    table.add_column("Provider", width=22)
    table.add_column("Tagline")
    table.add_column("Env var", style="dim")

    for i, name in enumerate(names, 1):
        meta = PROVIDER_META[name]
        free_marker = " 🆓" if meta["free"] else ""
        label = f"{meta['label']}{free_marker}"
        env = f"${meta['env_var']}" if meta["env_var"] else "(no key needed)"
        table.add_row(str(i), label, meta["tagline"], env)
    console.print(table)
    console.print()

    raw = Prompt.ask("Pick (e.g. 1,3,5)", default="1")
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

def _read_key(provider_name: str) -> str:
    meta = PROVIDER_META[provider_name]
    env_var = meta["env_var"]
    existing = os.environ.get(env_var, "") if env_var else ""
    if existing:
        if Confirm.ask(
            f"Found [green]${env_var}[/green] in your env ([dim]{_mask(existing)}[/dim]). Use it?",
            default=True,
        ):
            return f"${{{env_var}}}"
    while True:
        console.print(f"\n[dim]Get your key at: {meta['signup_url']}[/dim]")
        key = Prompt.ask(f"Paste your {meta['label']} API key", password=True)
        if not key:
            if Confirm.ask("No key entered — skip this provider?", default=False):
                return ""
            continue
        prefix = meta["key_prefix"]
        if prefix and not key.startswith(prefix) and not Confirm.ask(
            f"[yellow]Key doesn't start with '{prefix}'. Use anyway?[/yellow]", default=False
        ):
            continue
        if env_var and Confirm.ask(
            f"Store in shell env as [bold]${env_var}[/bold]? (recommended — keeps it out of the config file)",
            default=True,
        ):
            console.print(
                f"[yellow]Add this to your ~/.bashrc:[/yellow]\n    export {env_var}=\"{_mask(key)}\""
            )
            return f"${{{env_var}}}"
        return key


def step_collect_keys(chosen: List[str]) -> Dict[str, str]:
    _clear()
    console.rule("[bold cyan]Step 2 of 5 — Enter API keys[/bold cyan]")
    console.print()
    keys: Dict[str, str] = {}
    for name in chosen:
        meta = PROVIDER_META[name]
        console.rule(f"[bold]{meta['label']}[/bold] — {meta['tagline']}")
        if name == "ollama":
            base_url = Prompt.ask("Ollama base URL", default="http://localhost:11434/v1")
            keys[name] = "ollama"
            os.environ["AICODE_OLLAMA_BASE_URL"] = base_url
            console.print(f"[green]✓[/green] Ollama configured at {base_url}")
            continue
        key = _read_key(name)
        if key:
            keys[name] = key
            console.print(f"[green]✓[/green] Stored key for {name}")
        else:
            console.print(f"[yellow]! Skipped {name}[/yellow]")
    _pause()
    return keys


# ---------- Step 4: Test keys + fetch models ----------

async def _test_and_fetch(profile: Profile) -> Tuple[bool, str, List[ModelInfo]]:
    """Test the key by fetching the model list. Returns (ok, message, models)."""
    try:
        # First try to fetch models — this also validates the key
        models = await fetch_models_for_profile(profile)
        if not models:
            return False, "no models returned", []
        # Quick sanity check: do a 1-token chat completion to verify the key works
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
        except Exception as e:
            # Model list worked but chat failed — still usable, just warn
            return True, f"key works (model list returned {len(models)} models)", models
    except Exception as e:
        # Fall back to known models
        fallback = _fallback_models(profile.provider)
        return False, f"API error: {str(e)[:100]}", fallback


def step_test_and_fetch_models(
    chosen: List[str],
    keys: Dict[str, str],
) -> Dict[str, Tuple[bool, str, List[ModelInfo]]]:
    """Test each key and fetch its model list. Returns {provider: (ok, msg, models)}."""
    _clear()
    console.rule("[bold cyan]Step 3 of 5 — Testing keys & fetching models[/bold cyan]")
    console.print()
    console.print("Validating each key and fetching the live model list...\n")

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
        with Live(
            Spinner("dots", text=f"  Testing [bold]{name}[/bold] — {meta['label']}..."),
            console=console,
            refresh_per_second=10,
        ) as live:
            ok, msg, models = asyncio.run(_test_and_fetch(profile))
        if ok:
            console.print(f"  [green]✓[/green] {name}: {msg} ({len(models)} models available)")
        else:
            console.print(f"  [red]✗[/red] {name}: {msg}")
            if models:
                console.print(f"      [dim]Using {len(models)} fallback models[/dim]")
        results[name] = (ok, msg, models)

    passed = sum(1 for ok, _, _ in results.values() if ok)
    console.print(f"\n[bold]{passed}/{len(results)} providers passed.[/bold]")
    if passed == 0:
        console.print("[red]No providers worked. Check your keys and re-run: aicode setup[/red]")
        sys.exit(1)
    _pause()
    return results


# ---------- Step 5: Pick models ----------

def _pick_model(provider_name: str, models: List[ModelInfo]) -> ModelInfo:
    """Show a paginated, searchable model picker. Returns the chosen ModelInfo."""
    _clear()
    meta = PROVIDER_META[provider_name]
    console.rule(f"[bold cyan]Pick a model for {meta['label']}[/bold cyan]")
    console.print()

    if not models:
        console.print("[yellow]No models available — using default[/yellow]")
        return ModelInfo(id=meta["default_model"])

    # Paginate: show 15 at a time
    PAGE_SIZE = 15
    page = 0
    while True:
        start = page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_models = models[start:end]
        total_pages = (len(models) + PAGE_SIZE - 1) // PAGE_SIZE

        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
        table.add_column("#", style="cyan", width=4)
        table.add_column("Model", width=45)
        table.add_column("Context", width=12)
        table.add_column("Tags", width=15)
        table.add_column("Description")

        for i, m in enumerate(page_models, start=start + 1):
            tags = []
            if m.is_free:
                tags.append("🆓 FREE")
            if m.supports_vision:
                tags.append("👁")
            if not m.supports_tools:
                tags.append("no-tools")
            ctx = f"{m.context_length:,}" if m.context_length else "-"
            table.add_row(str(i), m.display_name[:44], ctx, " ".join(tags), m.description[:50])

        console.print(table)
        console.print(
            f"\n[dim]Page {page + 1}/{total_pages} · {len(models)} models · "
            f"n=next p=prev q=search <number>=pick[/dim]"
        )

        choice = Prompt.ask("Pick a model", default="1").strip().lower()
        if choice in {"n", "next"} and end < len(models):
            page += 1
            continue
        if choice in {"p", "prev"} and page > 0:
            page -= 1
            continue
        if choice in {"q", "search", "/"}:
            term = Prompt.ask("Search for").strip().lower()
            filtered = [m for m in models if term in m.id.lower() or term in m.name.lower() or term in m.description.lower()]
            if not filtered:
                console.print(f"[yellow]No models match '{term}'[/yellow]")
                _pause()
                continue
            if len(filtered) == 1:
                return filtered[0]
            # Show filtered results and re-pick
            models = filtered
            page = 0
            continue
        try:
            idx = int(choice)
            if 1 <= idx <= len(models):
                return models[idx - 1]
        except ValueError:
            pass
        console.print("[yellow]Invalid choice[/yellow]")


def step_pick_models(
    chosen: List[str],
    keys: Dict[str, str],
    fetch_results: Dict[str, Tuple[bool, str, List[ModelInfo]]],
) -> Dict[str, str]:
    """Let the user pick a model for each provider. Returns {provider: model_id}."""
    _clear()
    console.rule("[bold cyan]Step 4 of 5 — Pick your models[/bold cyan]")
    console.print()
    console.print("For each provider, browse the live model list and pick one.\n")

    models_picked: Dict[str, str] = {}
    for name in chosen:
        if name not in keys:
            continue
        ok, msg, models = fetch_results.get(name, (False, "", []))
        if not ok and not models:
            console.print(f"[yellow]Skipping {name} — no models available[/yellow]")
            continue
        meta = PROVIDER_META[name]
        console.print(f"\n[bold]{meta['label']}[/bold] — {len(models)} model(s) available")
        chosen_model = _pick_model(name, models)
        models_picked[name] = chosen_model.id
        console.print(f"[green]✓[/green] Picked: [bold]{chosen_model.id}[/bold]")
        _pause()
    return models_picked


# ---------- Step 6: Routing ----------

def step_pick_routing(working_profiles: List[str], models_picked: Dict[str, str]) -> RoutingConfig:
    _clear()
    console.rule("[bold cyan]Step 5 of 5 — Configure auto-routing[/bold cyan]")
    console.print()
    console.print(
        "aicode auto-picks the best model for each task. "
        "Map each task type to a profile.\n"
    )

    # Show the picked model next to each profile
    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("#", style="cyan", width=3)
    table.add_column("Profile", width=14)
    table.add_column("Model", width=40)
    for i, p in enumerate(working_profiles, 1):
        model = models_picked.get(p, PROVIDER_META[p]["default_model"])
        table.add_row(str(i), p, model)
    console.print(table)
    console.print()

    def _default_for(preferred: List[str]) -> int:
        for pref in preferred:
            if pref in working_profiles:
                return working_profiles.index(pref) + 1
        return 1

    def _pick(prompt: str, default_idx: int) -> str:
        idx = IntPrompt.ask(prompt, default=default_idx, show_default=True)
        if 1 <= idx <= len(working_profiles):
            return working_profiles[idx - 1]
        return working_profiles[default_idx - 1]

    coding = _pick("Profile for [bold]coding[/bold] tasks", _default_for(["nim", "openrouter", "claude", "openai"]))
    reasoning = _pick("Profile for [bold]reasoning[/bold] tasks", _default_for(["claude", "openai", "nim"]))
    simple = _pick("Profile for [bold]simple[/bold] tasks", _default_for(["groq", "gemini", "ollama"]))
    default = _pick("[bold]Default[/bold] profile (fallback)", _default_for(["nim", "openai", "claude", working_profiles[0]]))

    console.print(f"\n[green]✓[/green] coding → {coding}, reasoning → {reasoning}, simple → {simple}, default → {default}")
    _pause()
    return RoutingConfig(coding=coding, reasoning=reasoning, simple=simple, default=default)


# ---------- Step 7: Summary + write config ----------

def step_summary(
    chosen: List[str],
    keys: Dict[str, str],
    models_picked: Dict[str, str],
    routing: RoutingConfig,
) -> None:
    _clear()
    console.rule("[bold cyan]Setup summary[/bold cyan]")
    console.print()

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("Provider", style="bold", width=14)
    table.add_column("Model", width=45)
    table.add_column("Key", width=20)
    for name in chosen:
        if name not in keys:
            continue
        meta = PROVIDER_META[name]
        model = models_picked.get(name, meta["default_model"])
        key_disp = _mask(keys[name]) if keys[name] and not keys[name].startswith("${") else keys[name]
        table.add_row(meta["label"], model, key_disp)
    console.print(table)
    console.print()

    rt = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    rt.add_column("Task type", width=15)
    rt.add_column("Profile", width=15)
    rt.add_column("Model", width=45)
    for task, prof_name in [("coding", routing.coding), ("reasoning", routing.reasoning), ("simple", routing.simple), ("default", routing.default)]:
        model = models_picked.get(prof_name, PROVIDER_META.get(prof_name, {}).get("default_model", ""))
        rt.add_row(task, prof_name, model)
    console.print(rt)
    console.print()


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
    step_welcome()
    chosen = step_pick_providers()
    keys = step_collect_keys(chosen)
    if not keys:
        console.print("[red]No API keys provided. Aborting.[/red]")
        return 1

    # Test keys + fetch model lists in one step
    fetch_results = step_test_and_fetch_models(chosen, keys)

    # Pick models from the live lists
    models_picked = step_pick_models(chosen, keys, fetch_results)

    # Filter to working profiles for routing
    working = [n for n in chosen if fetch_results.get(n, (False,))[0]]
    if not working:
        console.print("[red]No providers passed testing. Fix your keys and re-run: aicode setup[/red]")
        return 1

    routing = step_pick_routing(working, models_picked)

    # Show summary before writing
    step_summary(chosen, keys, models_picked, routing)
    if not Confirm.ask("Write config and finish?", default=True):
        console.print("[yellow]Aborted — no config written.[/yellow]")
        return 1

    path = step_write_config(chosen, keys, models_picked, routing, config_path)

    console.print()
    console.print(Panel(
        f"[green bold]Setup complete![/green bold]\n\n"
        f"Config: [bold]{path}[/bold]\n\n"
        f"Working providers: {', '.join(working)}\n"
        f"Default profile: [bold]{routing.default}[/bold]\n\n"
        f"Next: run [bold cyan]aicode[/bold cyan] to launch the TUI.",
        title="aicode setup",
        border_style="green",
    ))

    if Confirm.ask("\nRun doctor now?", default=True):
        from .__main__ import cmd_doctor
        import argparse
        cmd_doctor(argparse.Namespace(config=str(path) if path else None))
    return 0
