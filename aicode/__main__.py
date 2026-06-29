"""Entry point — `aicode` command (Claude Code-style)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .config import DEFAULT_CONFIG_PATH, load_config, write_default_config
from .exec_mode import cmd_exec
from .session import list_sessions, load_session
from .tui.app import AICodeApp
from .wizard import run_wizard


def cmd_config_init(args: argparse.Namespace) -> int:
    path = write_default_config(Path(args.path) if args.path else None)
    print(f"✓ wrote default config to {path}")
    print()
    print("Next steps:")
    print(f"  1. Edit {path} and fill in your API keys")
    print("  2. (Or) set environment variables referenced by ${...} in the config:")
    print("       NVIDIA_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY,")
    print("       GROQ_API_KEY, OPENROUTER_API_KEY")
    print("  3. Run: aicode")
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.path) if args.path else None)
    print(f"# config path: {cfg.path}")
    print(f"# profiles ({len(cfg.profiles)}):")
    for name, p in cfg.profiles.items():
        key_disp = "***" if p.resolved_api_key() else "(missing)"
        print(f"  - {name}: {p.provider}/{p.model} key={key_disp} base_url={p.base_url or '(default)'}")
    print("# routing:")
    print(f"  coding    → {cfg.routing.coding}")
    print(f"  reasoning → {cfg.routing.reasoning}")
    print(f"  simple    → {cfg.routing.simple}")
    print(f"  default   → {cfg.routing.default}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        print(f"No config found at {config_path}.", file=sys.stderr)
        print("Launching setup wizard...\n", file=sys.stderr)
        rc = run_wizard(config_path)
        if rc != 0:
            return rc

    cfg = load_config(config_path)

    usable = [n for n, p in cfg.profiles.items() if p.resolved_api_key() or p.provider == "ollama"]
    if not usable:
        print("✗ No API keys configured. Launching setup wizard...", file=sys.stderr)
        rc = run_wizard(config_path)
        if rc != 0:
            return rc
        cfg = load_config(config_path)
        usable = [n for n, p in cfg.profiles.items() if p.resolved_api_key() or p.provider == "ollama"]
        if not usable:
            print("✗ Still no API keys. Aborting.", file=sys.stderr)
            return 1

    profile = None
    if args.profile:
        if args.profile not in cfg.profiles:
            print(f"✗ Unknown profile: {args.profile}", file=sys.stderr)
            print(f"  Available: {', '.join(cfg.profiles)}", file=sys.stderr)
            return 1
        profile = cfg.profiles[args.profile]

    cwd = args.cwd or os.getcwd()
    app = AICodeApp(
        config=cfg,
        cwd=cwd,
        profile=profile,
        plan_mode=args.plan,
        session_id=args.resume,
    )
    app.run()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"aicode v{__version__}")
    print(f"python: {sys.version.split()[0]}")
    print(f"platform: {sys.platform}")
    print()
    cfg = load_config(Path(args.config) if args.config else None)
    print(f"config: {cfg.path}")
    print()
    print("Profiles:")
    all_ok = True
    for name, p in cfg.profiles.items():
        has_key = bool(p.resolved_api_key()) or p.provider == "ollama"
        marker = "✓" if has_key else "✗"
        print(f"  {marker} {name:<14} {p.provider:<12} {p.model}")
        if not has_key:
            all_ok = False
    print()
    if all_ok:
        print("✓ All profiles are ready.")
    else:
        print("✗ Some profiles are missing API keys. Edit the config and set them,")
        print("  or set the matching environment variables.")
    return 0 if all_ok else 1


def cmd_setup(args: argparse.Namespace) -> int:
    return run_wizard(Path(args.config) if args.config else None)


def cmd_sessions(args: argparse.Namespace) -> int:
    """List saved sessions."""
    sessions = list_sessions()
    if not sessions:
        print("No saved sessions.")
        return 0
    print(f"{'ID':<14} {'Msgs':>5}  {'CWD':<30} Preview")
    print("-" * 80)
    for s in sessions[:20]:
        preview = (s["preview"] or "(empty)")[:40]
        cwd = (s["cwd"] or "")[:30]
        print(f"{s['id']:<14} {s['message_count']:>5}  {cwd:<30} {preview}")
    print(f"\nResume with: aicode --resume <id>")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aicode",
        description="AI coding agent for Termux — Claude Code-style with multi-provider support (NVIDIA NIM, OpenAI, Anthropic, Gemini, Groq, OpenRouter, Ollama)",
    )
    p.add_argument("--version", action="version", version=f"aicode {__version__}")
    sub = p.add_subparsers(dest="cmd")

    # Default: run (TUI)
    p_run = sub.add_parser("run", help="Launch the TUI (default)")
    p_run.add_argument("--profile", "-p", help="Pin a profile (e.g. nim, gpt, claude)")
    p_run.add_argument("--cwd", help="Working directory (default: current)")
    p_run.add_argument("--config", help="Path to config.toml")
    p_run.add_argument("--plan", action="store_true", help="Start in plan mode (read-only, plans before executing)")
    p_run.add_argument("--resume", "-r", metavar="SESSION_ID", help="Resume a saved session")
    p_run.set_defaults(func=cmd_run)

    # exec — non-interactive mode
    p_exec = sub.add_parser("exec", help="Non-interactive: run one prompt and exit (like `claude -p`)")
    p_exec.add_argument("prompt", nargs="?", default=None, help="Prompt text (or pipe via stdin)")
    p_exec.add_argument("--profile", "-p", help="Pin a profile")
    p_exec.add_argument("--cwd", help="Working directory")
    p_exec.add_argument("--config", help="Path to config.toml")
    p_exec.add_argument("--plan", action="store_true", help="Plan mode (no tool execution)")
    p_exec.add_argument("--verbose", "-v", action="store_true", help="Show tool calls + cost")
    p_exec.set_defaults(func=cmd_exec)

    # config
    p_init = sub.add_parser("config", help="Manage config")
    p_init_sub = p_init.add_subparsers(dest="subcmd", required=True)
    p_init_init = p_init_sub.add_parser("init", help="Write default config")
    p_init_init.add_argument("--path", help="Custom config path")
    p_init_init.set_defaults(func=cmd_config_init)
    p_init_show = p_init_sub.add_parser("show", help="Show resolved config")
    p_init_show.add_argument("--path", help="Custom config path")
    p_init_show.set_defaults(func=cmd_config_show)

    # doctor
    p_doc = sub.add_parser("doctor", help="Diagnose config + environment")
    p_doc.add_argument("--config", help="Path to config.toml")
    p_doc.set_defaults(func=cmd_doctor)

    # setup
    p_setup = sub.add_parser("setup", help="Run the interactive setup wizard")
    p_setup.add_argument("--config", help="Path to write config.toml")
    p_setup.set_defaults(func=cmd_setup)

    # sessions
    p_sess = sub.add_parser("sessions", help="List saved sessions")
    p_sess.set_defaults(func=cmd_sessions)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        args = parser.parse_args(["run", *argv] if argv else ["run"])

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
