"""Non-interactive mode — `aicode exec "prompt"` and stdin piping.

Like `claude -p "prompt"`. Runs one turn, prints output, exits.
Supports:
  aicode exec "fix the bug in foo.py"
  echo "explain this" | aicode exec
  cat file.py | aicode exec "review this code"
"""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown

from .agent.loop import Agent, AgentEvent, AgentEventType
from .config import Config, load_config


async def run_exec(
    prompt: Optional[str],
    config: Config,
    cwd: str,
    profile_name: Optional[str] = None,
    plan_mode: bool = False,
    verbose: bool = False,
) -> int:
    """Run a single turn non-interactively. Returns exit code."""
    console = Console()

    # Read stdin if available (piped input)
    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()

    # Combine stdin + prompt
    if stdin_text and prompt:
        full_prompt = f"{prompt}\n\n--- stdin ---\n{stdin_text}"
    elif stdin_text:
        full_prompt = stdin_text
    elif prompt:
        full_prompt = prompt
    else:
        console.print("[red]No prompt provided. Usage: aicode exec \"your prompt\"[/red]")
        console.print("  Or pipe: echo \"explain\" | aicode exec")
        return 1

    # Determine profile
    profile = None
    if profile_name:
        if profile_name not in config.profiles:
            console.print(f"[red]Unknown profile: {profile_name}[/red]")
            return 1
        profile = config.profiles[profile_name]

    # Validate at least one usable profile
    usable = [n for n, p in config.profiles.items() if p.resolved_api_key() or p.provider == "ollama"]
    if not usable:
        console.print("[red]No API keys configured. Run: aicode setup[/red]")
        return 1

    # Buffer for output
    output_parts: list = []
    tool_calls_made: list = []
    errors: list = []

    def on_event(event: AgentEvent) -> None:
        if event.type == AgentEventType.TEXT_DELTA:
            output_parts.append(event.text)
        elif event.type == AgentEventType.TOOL_CALL and verbose:
            tc = event.tool_call
            args_str = ", ".join(f"{k}={v!r}" for k, v in tc.arguments.items())
            tool_calls_made.append(f"  → {tc.name}({args_str})")
        elif event.type == AgentEventType.ERROR:
            errors.append(event.error)
        elif event.type == AgentEventType.PROFILE_SWITCHED and verbose:
            console.print(f"[dim]profile: {event.profile}[/dim]")
        elif event.type == AgentEventType.COST_UPDATE and verbose:
            if event.cost:
                console.print(f"[dim]cost: {event.cost.format()}[/dim]")

    agent = Agent(
        config=config,
        cwd=cwd,
        profile=profile,
        on_event=on_event,
        plan_mode=plan_mode,
    )

    try:
        async for _ in agent.chat(full_prompt):
            pass
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1

    # Print tool calls if verbose
    if verbose and tool_calls_made:
        console.print("[bold]Tool calls:[/bold]")
        for tc in tool_calls_made:
            console.print(tc)

    # Print errors
    for err in errors:
        console.print(f"[yellow]! {err}[/yellow]")

    # Print the assistant output as markdown
    output = "".join(output_parts).strip()
    if output:
        console.print(Markdown(output))

    # Print cost summary if verbose
    if verbose:
        console.print()
        console.print(f"[dim]{agent.session_cost.format_summary()}[/dim]")

    return 0


def cmd_exec(args) -> int:
    """CLI entry point for `aicode exec`."""
    import os
    config = load_config(args.config) if hasattr(args, "config") and args.config else load_config()
    cwd = args.cwd or os.getcwd()
    return asyncio.run(run_exec(
        prompt=args.prompt,
        config=config,
        cwd=cwd,
        profile_name=args.profile,
        plan_mode=args.plan,
        verbose=args.verbose,
    ))
