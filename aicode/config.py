"""Configuration system — TOML config at ~/.config/aicode/config.toml.

Profile-based: each profile binds a provider + model + key reference.
Example config:

    [profiles.nim]
    provider = "nim"
    model = "meta/llama-3.3-70b-instruct"
    api_key = "nvapi-xxxxx"
    base_url = "https://integrate.api.nvidia.com/v1"

    [profiles.claude]
    provider = "anthropic"
    model = "claude-3-5-sonnet-20241022"
    api_key = "sk-ant-xxxxx"

    [profiles.gpt]
    provider = "openai"
    model = "gpt-4o"
    api_key = "sk-xxxxx"

    [profiles.gemini]
    provider = "gemini"
    model = "gemini-2.0-flash"
    api_key = "AIzaSyxxxxx"

    [profiles.groq]
    provider = "groq"
    model = "llama-3.3-70b-versatile"
    api_key = "gsk_xxxxx"

    [profiles.openrouter]
    provider = "openrouter"
    model = "anthropic/claude-3.5-sonnet"
    api_key = "sk-or-xxxxx"

    [profiles.ollama]
    provider = "ollama"
    model = "llama3.2:3b"
    base_url = "http://localhost:11434/v1"

    [routing]
    # task_type -> profile name
    coding = "nim"
    reasoning = "claude"
    simple = "groq"
    default = "nim"

    [shell]
    require_approval = true
    timeout = 120

    [ui]
    theme = "dark"
    show_file_tree = true
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib  # py 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

import tomli_w

DEFAULT_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "aicode"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"


@dataclass
class Profile:
    name: str
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def resolved_api_key(self) -> str:
        """Resolve env-var-style references like ${NVIDIA_API_KEY} or return literal."""
        if not self.api_key:
            return ""
        if self.api_key.startswith("${") and self.api_key.endswith("}"):
            return os.environ.get(self.api_key[2:-1], "")
        return self.api_key


@dataclass
class RoutingConfig:
    coding: str = ""
    reasoning: str = ""
    simple: str = ""
    default: str = ""

    def for_task(self, task_type: str) -> str:
        """Return profile name for a task type, falling back to default."""
        return getattr(self, task_type, "") or self.default


@dataclass
class ShellConfig:
    require_approval: bool = True
    timeout: int = 120


@dataclass
class UIConfig:
    theme: str = "dark"
    show_file_tree: bool = True


@dataclass
class CustomCommand:
    """A user-defined slash command. Maps /name → fixed prompt."""

    name: str
    prompt: str
    description: str = ""


@dataclass
class AutoCommitConfig:
    """Auto-commit settings — automatically git commit after successful edits."""

    enabled: bool = False
    message_template: str = "chore: aicode changes — {summary}"
    # If true, only auto-commit when the working tree was clean before the turn
    require_clean_tree: bool = True


@dataclass
class Config:
    profiles: Dict[str, Profile] = field(default_factory=dict)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    commands: Dict[str, CustomCommand] = field(default_factory=dict)
    auto_commit: AutoCommitConfig = field(default_factory=AutoCommitConfig)
    # MCP servers: name → {command, args, env}
    mcp_servers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    path: Path = DEFAULT_CONFIG_PATH

    def get_profile(self, name: str) -> Profile:
        if name not in self.profiles:
            raise KeyError(f"profile '{name}' not found in config")
        return self.profiles[name]

    def default_profile(self) -> Optional[Profile]:
        name = self.routing.default
        return self.profiles.get(name) if name else None

    def is_profile_usable(self, name: str) -> bool:
        """A profile is usable if it exists AND has a resolved API key
        (or is ollama, which doesn't need one)."""
        p = self.profiles.get(name)
        if p is None:
            return False
        if p.provider == "ollama":
            return True
        return bool(p.resolved_api_key())

    def available_profiles(self) -> Dict[str, Profile]:
        """Return only profiles with a working API key (or ollama)."""
        return {n: p for n, p in self.profiles.items() if self.is_profile_usable(n)}


def default_config() -> Config:
    """Return a config with sensible defaults and all 7 providers stubbed."""
    return Config(
        profiles={
            "nim": Profile(
                name="nim",
                provider="nim",
                model="meta/llama-3.3-70b-instruct",
                api_key="${NVIDIA_API_KEY}",
                base_url="https://integrate.api.nvidia.com/v1",
            ),
            "gpt": Profile(
                name="gpt",
                provider="openai",
                model="gpt-4o",
                api_key="${OPENAI_API_KEY}",
                base_url="https://api.openai.com/v1",
            ),
            "claude": Profile(
                name="claude",
                provider="anthropic",
                model="claude-3-5-sonnet-20241022",
                api_key="${ANTHROPIC_API_KEY}",
            ),
            "gemini": Profile(
                name="gemini",
                provider="gemini",
                model="gemini-2.0-flash",
                api_key="${GEMINI_API_KEY}",
            ),
            "groq": Profile(
                name="groq",
                provider="groq",
                model="llama-3.3-70b-versatile",
                api_key="${GROQ_API_KEY}",
                base_url="https://api.groq.com/openai/v1",
            ),
            "openrouter": Profile(
                name="openrouter",
                provider="openrouter",
                model="anthropic/claude-3.5-sonnet",
                api_key="${OPENROUTER_API_KEY}",
                base_url="https://openrouter.ai/api/v1",
            ),
            "ollama": Profile(
                name="ollama",
                provider="ollama",
                model="llama3.2:3b",
                api_key="ollama",
                base_url="http://localhost:11434/v1",
            ),
        },
        routing=RoutingConfig(
            coding="nim",
            reasoning="claude",
            simple="groq",
            default="nim",
        ),
        shell=ShellConfig(require_approval=True, timeout=120),
        ui=UIConfig(theme="dark", show_file_tree=True),
        path=DEFAULT_CONFIG_PATH,
    )


def write_default_config(path: Optional[Path] = None) -> Path:
    """Write the default config to disk and return the path."""
    cfg = default_config()
    target = path or cfg.path
    target.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {
        "profiles": {},
        "routing": cfg.routing.__dict__,
        "shell": cfg.shell.__dict__,
        "ui": cfg.ui.__dict__,
    }
    for name, p in cfg.profiles.items():
        data["profiles"][name] = {
            "provider": p.provider,
            "model": p.model,
            "api_key": p.api_key,
            "base_url": p.base_url,
        }
    with open(target, "wb") as f:
        tomli_w.dump(data, f)
    return target


def load_config(path: Optional[Path] = None) -> Config:
    """Load config from disk; auto-creates default if missing."""
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        write_default_config(target)
    with open(target, "rb") as f:
        data = tomllib.load(f)

    profiles: Dict[str, Profile] = {}
    for name, p in (data.get("profiles") or {}).items():
        profiles[name] = Profile(
            name=name,
            provider=p.get("provider", ""),
            model=p.get("model", ""),
            api_key=p.get("api_key", ""),
            base_url=p.get("base_url", ""),
            extra={k: v for k, v in p.items() if k not in {"provider", "model", "api_key", "base_url"}},
        )

    routing_data = data.get("routing") or {}
    routing = RoutingConfig(
        coding=routing_data.get("coding", ""),
        reasoning=routing_data.get("reasoning", ""),
        simple=routing_data.get("simple", ""),
        default=routing_data.get("default", ""),
    )

    shell_data = data.get("shell") or {}
    shell = ShellConfig(
        require_approval=shell_data.get("require_approval", True),
        timeout=shell_data.get("timeout", 120),
    )

    ui_data = data.get("ui") or {}
    ui = UIConfig(
        theme=ui_data.get("theme", "dark"),
        show_file_tree=ui_data.get("show_file_tree", True),
    )

    # Custom commands: [commands.<name>] prompt = "..." description = "..."
    commands: Dict[str, CustomCommand] = {}
    for name, c in (data.get("commands") or {}).items():
        commands[name] = CustomCommand(
            name=name,
            prompt=c.get("prompt", ""),
            description=c.get("description", ""),
        )

    # Auto-commit: [auto_commit] enabled = true ...
    ac_data = data.get("auto_commit") or {}
    auto_commit = AutoCommitConfig(
        enabled=ac_data.get("enabled", False),
        message_template=ac_data.get("message_template", "chore: aicode changes — {summary}"),
        require_clean_tree=ac_data.get("require_clean_tree", True),
    )

    # MCP servers: [mcp_servers.<name>] command = "..." args = [...]
    mcp_servers: Dict[str, Dict[str, Any]] = {}
    for name, s in (data.get("mcp_servers") or {}).items():
        mcp_servers[name] = {
            "command": s.get("command", ""),
            "args": s.get("args", []),
            "env": s.get("env", {}),
        }

    return Config(
        profiles=profiles,
        routing=routing,
        shell=shell,
        ui=ui,
        commands=commands,
        auto_commit=auto_commit,
        mcp_servers=mcp_servers,
        path=target,
    )
