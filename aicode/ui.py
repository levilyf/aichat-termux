"""Modern interactive UI components — splash screens, animated panels, fancy prompts.

Uses rich + questionary for a polished, modern CLI experience inspired by
aider, opencode, and claude-code. Replaces the old plain-text prompts with
interactive checkboxes, search selects, and animated spinners.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

try:
    import questionary
    from questionary import Style
except ImportError:
    questionary = None  # type: ignore
    Style = None  # type: ignore

# ─── Theme ────────────────────────────────────────────────────────────────────

AICODE_THEME = Theme({
    "primary": "bold cyan",
    "secondary": "bold magenta",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "info": "blue",
    "dim": "dim",
    "accent": "magenta",
    "highlight": "bold white on blue",
})

console = Console(theme=AICODE_THEME)

# Custom questionary style — flat, modern, no borders
QSTYLE = Style([
    ("qmark", "fg:#5f8787 bold"),
    ("question", "fg:#ffffff bold"),
    ("selected", "fg:#00d7af bold"),
    ("pointer", "fg:#00d7af bold"),
    ("highlighted", "fg:#00d7af bold"),
    ("answer", "fg:#87d7ff bold"),
    ("instruction", "fg:#808080 italic"),
    ("text", "fg:#ffffff"),
    ("disabled", "fg:#808080 italic"),
]) if Style else None


# ─── Splash ───────────────────────────────────────────────────────────────────

SPLASH_LINES = [
    ("     ___   ___  ___  ___", "cyan"),
    ("    / _ \\ / _ \\/ _ \\/ __|", "cyan"),
    ("   | (_) | (_) | (_) \\__ \\", "bright_cyan"),
    ("    \\___/ \\___/ \\___/|___/", "bright_white"),
]


def show_splash(version: str = "", subtitle: str = "") -> None:
    """Animated splash screen on startup."""
    _clear()

    # ASCII art with fade-in
    art_lines: List[Text] = []
    for line, color in SPLASH_LINES:
        art_lines.append(Text(line, style=color))

    art_text = Text("\n").join(art_lines)

    # Subtitle
    subtitle_text = Text(subtitle or "AI coding agent for Termux", style="dim italic")
    version_text = Text(f"v{version}", style="cyan bold") if version else Text("")

    # Animated spinner while "loading"
    content = Group(
        Text(""),
        Align.center(art_text),
        Text(""),
        Align.center(subtitle_text),
        Align.center(version_text) if version else Text(""),
        Text(""),
        Align.center(Spinner("dots", text=Text("Loading...", style="dim"))),
    )

    with Live(content, console=console, refresh_per_second=10, transient=True) as live:
        for _ in range(8):  # ~0.8s splash
            time.sleep(0.1)
            live.update(content)

    _clear()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _clear() -> None:
    os.system("clear" if os.name == "posix" else "cls")


def _pause(msg: str = "Press Enter to continue...") -> None:
    Prompt.ask(f"[dim]{msg}[/dim]", default="")


def _mask(s: str) -> str:
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


# ─── Provider picker ──────────────────────────────────────────────────────────

def pick_providers_interactive(providers: List[Dict[str, Any]]) -> List[str]:
    """Multi-select providers with questionary checkboxes."""
    if not questionary:
        # Fallback to plain prompt
        return _pick_providers_fallback(providers)

    _clear()
    console.print(Panel(
        Text("Choose your providers", style="bold cyan"),
        subtitle="[dim]Use ↑↓ to navigate, space to select, Enter to confirm[/dim]",
        border_style="cyan",
    ))

    choices = []
    for p in providers:
        free_marker = " 🆓" if p.get("free") else ""
        label = f"{p['label']}{free_marker}  [dim]— {p['tagline']}[/dim]"
        choices.append(questionary.Choice(title=label, value=p["name"], checked=p.get("free", False)))

    selected = questionary.checkbox(
        "Pick providers to configure",
        choices=choices,
        style=QSTYLE,
    ).ask()

    if not selected:
        console.print("[red]No providers selected. Aborting.[/red]")
        sys.exit(1)

    return selected


def _pick_providers_fallback(providers: List[Dict[str, Any]]) -> List[str]:
    """Plain-text fallback when questionary isn't available."""
    _clear()
    console.print("[bold cyan]Pick your providers[/bold cyan]")
    console.print("[dim](Free-tier providers marked with 🆓)[/dim]\n")
    for i, p in enumerate(providers, 1):
        free = " 🆓" if p.get("free") else ""
        console.print(f"  {i}. {p['label']}{free} — {p['tagline']}")
    raw = Prompt.ask("\nPick (e.g. 1,3,5)", default="1")
    indices: List[int] = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip()
        try:
            idx = int(tok)
            if 1 <= idx <= len(providers):
                indices.append(idx - 1)
        except ValueError:
            pass
    return [providers[i]["name"] for i in dict.fromkeys(indices)] if indices else [providers[0]["name"]]


# ─── API key input ────────────────────────────────────────────────────────────

def ask_api_key(provider_label: str, signup_url: str, env_var: str = "", key_prefix: str = "") -> str:
    """Ask for an API key with masked input, env detection, and signup URL shown."""
    # Check env first
    if env_var:
        existing = os.environ.get(env_var, "")
        if existing:
            if Confirm.ask(
                f"Found [green]${env_var}[/green] in env ([dim]{_mask(existing)}[/dim]). Use it?",
                default=True,
            ):
                return f"${{{env_var}}}"

    console.print(f"\n[dim]Get your key at: [link={signup_url}]{signup_url}[/link][/dim]")
    if key_prefix:
        console.print(f"[dim]Expected key prefix: {key_prefix}[/dim]")

    if questionary:
        key = questionary.password(f"Paste your {provider_label} API key:").ask()
    else:
        key = Prompt.ask(f"Paste your {provider_label} API key", password=True)

    if not key:
        return ""

    # Validate prefix
    if key_prefix and not key.startswith(key_prefix):
        if not Confirm.ask(
            f"[yellow]Key doesn't start with '{key_prefix}'. Use anyway?[/yellow]",
            default=False,
        ):
            return ask_api_key(provider_label, signup_url, env_var, key_prefix)

    # Offer to store as env var
    if env_var and Confirm.ask(
        f"Store in shell env as [bold]${env_var}[/bold]? (recommended)",
        default=True,
    ):
        console.print(f"[yellow]Add to ~/.bashrc:[/yellow]\n    export {env_var}=\"{_mask(key)}\"")
        return f"${{{env_var}}}"

    return key


# ─── Model picker (searchable) ────────────────────────────────────────────────

def pick_model_interactive(
    provider_label: str,
    models: List[Any],  # List[ModelInfo]
    default_model_id: str = "",
) -> str:
    """Searchable model picker using questionary's autocomplete."""
    if not models:
        return default_model_id

    if not questionary:
        return _pick_model_fallback(provider_label, models, default_model_id)

    _clear()
    console.print(Panel(
        Text(f"Pick a model for {provider_label}", style="bold cyan"),
        subtitle=f"[dim]{len(models)} models available — type to search[/dim]",
        border_style="cyan",
    ))

    # Build choices with rich descriptions
    choices = []
    for m in models:
        # Format: "Model Name (model-id)  ctx=131072  🆓  description"
        parts = [m.display_name]
        if hasattr(m, "context_length") and m.context_length:
            parts.append(f"ctx={m.context_length:,}")
        if getattr(m, "is_free", False):
            parts.append("🆓")
        if getattr(m, "supports_vision", False):
            parts.append("👁")
        if not getattr(m, "supports_tools", True):
            parts.append("no-tools")
        if m.description:
            parts.append(m.description[:60])
        label = "  ".join(parts)

        # Default-select the default model
        is_default = m.id == default_model_id
        choices.append(questionary.Choice(title=label, value=m.id, checked=is_default))

    # Use select with fuzzy search
    selected = questionary.select(
        "Pick a model (type to search):",
        choices=choices,
        style=QSTYLE,
        use_shortcuts=False,
        use_arrow_keys=True,
        use_search_filter=True,
    ).ask()

    return selected or default_model_id


def _pick_model_fallback(provider_label: str, models: List[Any], default_model_id: str) -> str:
    """Paginated plain-text fallback."""
    _clear()
    console.print(f"[bold cyan]Pick a model for {provider_label}[/bold cyan] — {len(models)} available\n")

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
            if getattr(m, "is_free", False):
                tags.append("🆓")
            if getattr(m, "supports_vision", False):
                tags.append("👁")
            ctx = f"{m.context_length:,}" if getattr(m, "context_length", None) else "-"
            table.add_row(str(i), m.display_name[:44], ctx, " ".join(tags), m.description[:50])

        console.print(table)
        console.print(f"\n[dim]Page {page+1}/{total_pages} · n=next p=prev q=search <num>=pick[/dim]")

        choice = Prompt.ask("Pick", default="1").strip().lower()
        if choice in {"n", "next"} and end < len(models):
            page += 1
            continue
        if choice in {"p", "prev"} and page > 0:
            page -= 1
            continue
        if choice in {"q", "search"}:
            term = Prompt.ask("Search").lower()
            filtered = [m for m in models if term in m.id.lower() or term in getattr(m, "name", "").lower() or term in m.description.lower()]
            if not filtered:
                console.print(f"[yellow]No match[/yellow]")
                continue
            if len(filtered) == 1:
                return filtered[0].id
            models = filtered
            page = 0
            continue
        try:
            idx = int(choice)
            if 1 <= idx <= len(models):
                return models[idx-1].id
        except ValueError:
            pass


# ─── Spinner ──────────────────────────────────────────────────────────────────

def with_spinner(text: str, fn: Callable[[], Any]) -> Any:
    """Run a function with an animated spinner. Returns the function's result."""
    from rich.spinner import Spinner as RSpinner

    spinner = RSpinner("dots", text=Text(text, style="cyan"))
    with Live(spinner, console=console, refresh_per_second=10, transient=True) as live:
        result = fn()
        return result


async def with_spinner_async(text: str, coro):
    """Run an async coroutine with an animated spinner."""
    import asyncio
    from rich.spinner import Spinner as RSpinner

    spinner = RSpinner("dots", text=Text(text, style="cyan"))
    task = asyncio.ensure_future(coro)
    with Live(spinner, console=console, refresh_per_second=10, transient=True) as live:
        while not task.done():
            live.update(spinner)
            await asyncio.sleep(0.05)
    return task.result()


# ─── Pretty panels ────────────────────────────────────────────────────────────

def show_info_panel(title: str, body: str, color: str = "cyan") -> None:
    """Show a rich info panel."""
    console.print(Panel(
        Markdown(body) if "\n" in body else Text(body),
        title=f"[bold {color}]{title}[/bold {color}]",
        border_style=color,
        padding=(1, 2),
    ))


def show_error(message: str, suggestion: str = "") -> None:
    """Show an error with optional suggestion."""
    content = f"[bold red]✗ Error[/bold red]\n\n{message}"
    if suggestion:
        content += f"\n\n[dim]💡 {suggestion}[/dim]"
    console.print(Panel(content, border_style="red", padding=(1, 2)))


def show_success(title: str, body: str = "") -> None:
    """Show a success message."""
    content = f"[bold green]✓ {title}[/bold green]"
    if body:
        content += f"\n\n{body}"
    console.print(Panel(content, border_style="green", padding=(1, 2)))


# ─── Summary table ────────────────────────────────────────────────────────────

def show_config_summary(
    profiles: List[Dict[str, str]],
    routing: Dict[str, str],
) -> None:
    """Render a beautiful summary table of the chosen config."""
    _clear()
    console.print(Panel(
        Text("Setup Summary", style="bold cyan"),
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()

    # Profiles table
    ptable = Table(
        title="[bold]Profiles[/bold]",
        show_header=True,
        header_style="bold magenta",
        border_style="bright_black",
        padding=(0, 2),
    )
    ptable.add_column("Provider", style="bold", width=14)
    ptable.add_column("Model", width=45)
    ptable.add_column("Key", width=20)
    for p in profiles:
        ptable.add_row(p["label"], p["model"], p["key"])
    console.print(ptable)
    console.print()

    # Routing table
    rtable = Table(
        title="[bold]Auto-routing[/bold]",
        show_header=True,
        header_style="bold magenta",
        border_style="bright_black",
        padding=(0, 2),
    )
    rtable.add_column("Task type", width=15)
    rtable.add_column("Profile", width=15)
    rtable.add_column("Model", width=45)
    for task, profile_name in routing.items():
        # Find the model for this profile
        model = next((p["model"] for p in profiles if p["name"] == profile_name), "")
        rtable.add_row(task, profile_name, model)
    console.print(rtable)


# ─── Fancy confirm ────────────────────────────────────────────────────────────

def confirm(message: str, default: bool = True) -> bool:
    """A nicer confirm prompt with emoji."""
    if questionary:
        return questionary.confirm(message, default=default).ask()
    return Confirm.ask(message, default=default)


# ─── Status display ───────────────────────────────────────────────────────────

def show_status_grid(items: List[Tuple[str, str, str]]) -> None:
    """Show a grid of (label, value, color) tuples in a nice layout."""
    table = Table(show_header=False, box=None, padding=(0, 2), border_style="bright_black")
    table.add_column("Label", style="dim", width=20)
    table.add_column("Value", width=50)
    for label, value, color in items:
        table.add_row(label, Text(value, style=color))
    console.print(table)


# ─── Help rendering ───────────────────────────────────────────────────────────

def render_help(commands: Dict[str, Dict[str, str]]) -> None:
    """Render a beautiful grouped help table.

    commands = {
        "Session": {
            "/help": "show all slash commands",
            "/clear": "reset conversation",
            ...
        },
        "Models": { ... },
    }
    """
    for group, cmds in commands.items():
        table = Table(
            title=f"[bold cyan]{group}[/bold cyan]",
            show_header=True,
            header_style="bold magenta",
            box=None,
            padding=(0, 2),
        )
        table.add_column("Command", style="bold", width=22)
        table.add_column("Description")
        for cmd, desc in cmds.items():
            table.add_row(cmd, desc)
        console.print(table)
        console.print()
