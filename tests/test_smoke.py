"""Smoke tests — verify the core modules import and basic flows work without an API key."""

import os
import sys
import tempfile
from pathlib import Path

import pytest


def test_imports():
    """All top-level modules import cleanly."""
    from aicode import __version__
    from aicode.config import Config, Profile, load_config, write_default_config, default_config
    from aicode.providers import (
        Provider, Message, ToolCall, Response, ToolSpec,
        OpenAICompatProvider, AnthropicProvider, GeminiProvider,
        build_provider, PROVIDER_CLASSES,
    )
    from aicode.providers.registry import Router, classify_task, TaskClass
    from aicode.tools import (
        Tool, ToolResult, ToolRegistry,
        ReadFileTool, WriteFileTool, EditFileTool, ListFilesTool, GrepTool,
        ShellTool, GitTool, default_registry,
    )
    from aicode.agent import Agent, AgentEvent, AgentEventType, build_system_prompt
    assert __version__
    assert "nim" in PROVIDER_CLASSES
    assert "anthropic" in PROVIDER_CLASSES
    assert "gemini" in PROVIDER_CLASSES


def test_config_roundtrip(tmp_path: Path):
    """Write default config, read it back, expect 7 providers."""
    from aicode.config import write_default_config, load_config
    path = tmp_path / "config.toml"
    written = write_default_config(path)
    assert written == path
    cfg = load_config(path)
    assert set(cfg.profiles) >= {"nim", "gpt", "claude", "gemini", "groq", "openrouter", "ollama"}
    assert cfg.routing.default == "nim"
    assert cfg.routing.coding == "nim"


def test_profile_env_resolution(monkeypatch):
    """Profile.resolved_api_key() reads ${VAR} references."""
    from aicode.config import Profile
    p = Profile(name="x", provider="nim", model="m", api_key="${MY_KEY}")
    monkeypatch.setenv("MY_KEY", "secret123")
    assert p.resolved_api_key() == "secret123"
    monkeypatch.delenv("MY_KEY")
    assert p.resolved_api_key() == ""
    # Literal key passes through
    p2 = Profile(name="y", provider="nim", model="m", api_key="literal-key")
    assert p2.resolved_api_key() == "literal-key"


def test_build_provider_openai_compat():
    """build_provider returns OpenAICompatProvider for known OpenAI-compatible kinds."""
    from aicode.config import Profile
    from aicode.providers import build_provider, OpenAICompatProvider
    for kind in ["openai", "nim", "groq", "openrouter", "ollama"]:
        p = Profile(name=kind, provider=kind, model="m", api_key="k")
        prov = build_provider(p)
        assert isinstance(prov, OpenAICompatProvider), f"{kind} should map to OpenAICompatProvider"


def test_build_provider_anthropic_and_gemini():
    from aicode.config import Profile
    from aicode.providers import build_provider, AnthropicProvider, GeminiProvider
    prov = build_provider(Profile(name="c", provider="anthropic", model="claude-3", api_key="k"))
    assert isinstance(prov, AnthropicProvider)
    prov = build_provider(Profile(name="g", provider="gemini", model="gemini-2.0", api_key="k"))
    assert isinstance(prov, GeminiProvider)


def test_router_classification():
    from aicode.providers.registry import classify_task
    assert classify_task("hi there").kind == "simple"
    assert classify_task("debug why my function returns None").kind == "reasoning"
    assert classify_task("implement a new function to parse CSV files").kind == "coding"
    assert classify_task("fix the bug in the auth module").kind == "coding"


def test_router_picks_profile(monkeypatch):
    """When all keys are set, router picks the configured target directly."""
    from aicode.config import default_config
    from aicode.providers.registry import Router
    # Set all keys so every profile is usable
    for var, val in [
        ("NVIDIA_API_KEY", "k1"), ("OPENAI_API_KEY", "k2"),
        ("ANTHROPIC_API_KEY", "k3"), ("GEMINI_API_KEY", "k4"),
        ("GROQ_API_KEY", "k5"), ("OPENROUTER_API_KEY", "k6"),
    ]:
        monkeypatch.setenv(var, val)
    cfg = default_config()
    router = Router(cfg)
    p = router.for_text("hi")
    assert p.name == cfg.routing.simple
    assert router.last_warning is None  # no fallback
    p = router.for_text("implement a function that does X")
    assert p.name == cfg.routing.coding
    assert router.last_warning is None


def test_config_available_profiles_filter(monkeypatch):
    """available_profiles() only returns profiles with a key (or ollama)."""
    from aicode.config import default_config
    cfg = default_config()
    # No env vars set → only ollama should be usable
    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    available = cfg.available_profiles()
    assert set(available) == {"ollama"}, f"expected only ollama, got {set(available)}"

    # Set NIM key → nim should now be usable too
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    available = cfg.available_profiles()
    assert "nim" in available
    assert "ollama" in available
    assert "claude" not in available  # still missing


def test_router_falls_back_when_configured_target_missing_key(monkeypatch):
    """If routing.coding points to 'claude' but no ANTHROPIC_API_KEY is set,
    the router must fall back to a usable profile (e.g. nim if its key is set)."""
    from aicode.config import default_config, RoutingConfig
    from aicode.providers.registry import Router

    # Clear all keys
    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    # Only NIM key is set
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    cfg = default_config()
    cfg.routing = RoutingConfig(coding="claude", reasoning="claude", simple="claude", default="claude")
    router = Router(cfg)

    p = router.for_text("implement a function to parse CSV")
    assert p.name == "nim", f"expected fallback to nim, got {p.name}"
    assert router.last_warning is not None
    assert "claude" in router.last_warning
    assert "nim" in router.last_warning


def test_router_only_uses_usable_profiles(monkeypatch):
    """If only ollama is usable, every task type routes to ollama."""
    from aicode.config import default_config
    from aicode.providers.registry import Router

    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)

    cfg = default_config()
    router = Router(cfg)

    # Despite routing saying coding → nim, only ollama is usable
    p = router.for_text("implement a function")
    assert p.name == "ollama"
    p = router.for_text("debug why this fails")
    assert p.name == "ollama"
    p = router.for_text("hi")
    assert p.name == "ollama"


def test_router_warns_when_no_profiles_usable(monkeypatch):
    """Router raises a clear error when no profiles are usable at all."""
    from aicode.config import default_config
    from aicode.providers.registry import Router

    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)

    cfg = default_config()
    # Remove ollama so nothing is usable
    del cfg.profiles["ollama"]
    router = Router(cfg)

    with pytest.raises(RuntimeError, match="no usable profiles"):
        router.for_text("hello")


def test_config_is_profile_usable(monkeypatch):
    """is_profile_usable returns True only when key is resolved (or ollama)."""
    from aicode.config import default_config
    cfg = default_config()
    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    assert cfg.is_profile_usable("ollama") is True
    assert cfg.is_profile_usable("nim") is False
    assert cfg.is_profile_usable("claude") is False
    assert cfg.is_profile_usable("nonexistent") is False
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    assert cfg.is_profile_usable("nim") is True


def test_file_tools_roundtrip(tmp_path: Path):
    from aicode.tools.file_ops import ReadFileTool, WriteFileTool, EditFileTool, ListFilesTool
    cwd = str(tmp_path)
    write = WriteFileTool(cwd=cwd)
    r = write.run(path="hello.py", content="def hello():\n    return 'world'\n")
    assert r.success
    read = ReadFileTool(cwd=cwd)
    r = read.run(path="hello.py")
    assert r.success and "def hello" in r.output
    edit = EditFileTool(cwd=cwd)
    r = edit.run(path="hello.py", old_text="return 'world'", new_text="return 'universe'")
    assert r.success
    r = read.run(path="hello.py")
    assert "universe" in r.output
    ls = ListFilesTool(cwd=cwd)
    r = ls.run()
    assert r.success and "hello.py" in r.output


def test_grep_tool(tmp_path: Path):
    from aicode.tools.file_ops import WriteFileTool, GrepTool
    cwd = str(tmp_path)
    WriteFileTool(cwd=cwd).run(path="a.py", content="foo = 1\nbar = 2\n")
    WriteFileTool(cwd=cwd).run(path="b.py", content="baz = 3\n")
    r = GrepTool(cwd=cwd).run(pattern="bar")
    assert r.success and "a.py" in r.output and "bar = 2" in r.output


def test_shell_tool_safe_command():
    from aicode.tools.shell import ShellTool
    # Safe commands should run without approval even with require_approval=True
    t = ShellTool(cwd=".", require_approval=True)
    r = t.run(command="echo hello")
    assert r.success
    assert "hello" in r.output
    assert not r.requires_confirmation


def test_shell_tool_dangerous_command_needs_approval():
    from aicode.tools.shell import ShellTool
    t = ShellTool(cwd=".", require_approval=True)
    r = t.run(command="rm -rf /tmp/something")
    # Should require approval, not actually run
    assert r.requires_confirmation
    assert "requires approval" in r.output


def test_default_registry():
    from aicode.config import default_config
    from aicode.tools import default_registry
    cfg = default_config()
    reg = default_registry(cwd=".", config=cfg)
    names = reg.names()
    assert "read_file" in names
    assert "write_file" in names
    assert "edit_file" in names
    assert "shell" in names
    assert "git" in names


def test_system_prompt_mentions_tools():
    from aicode.agent.prompts import build_system_prompt
    prompt = build_system_prompt("/tmp", ["read_file", "shell", "git"])
    assert "read_file" in prompt
    assert "shell" in prompt
    assert "Working directory" in prompt


def test_agent_initialization(tmp_path: Path):
    """Agent can be constructed with a config and history is seeded with system prompt."""
    from aicode.config import default_config
    from aicode.agent import Agent
    cfg = default_config()
    agent = Agent(config=cfg, cwd=str(tmp_path))
    assert len(agent.history) == 1
    assert agent.history[0].role == "system"
    assert "read_file" in agent.history[0].content


def test_wizard_metadata_complete():
    """Wizard has metadata for all 7 built-in providers."""
    from aicode.wizard import PROVIDER_META, _mask
    assert set(PROVIDER_META) == {"nim", "gpt", "claude", "gemini", "groq", "openrouter", "ollama"}
    for name, meta in PROVIDER_META.items():
        assert meta["label"]
        assert meta["default_model"]
        assert isinstance(meta["alt_models"], list) and meta["alt_models"]
    # Masking helper
    assert _mask("short") == "*****"
    assert _mask("ab") == "**"
    assert _mask("nvapi-abc123def").startswith("nvap")


def test_wizard_writes_config(tmp_path: Path, monkeypatch):
    """The wizard's write step produces a loadable config."""
    from aicode.wizard import step_write_config
    from aicode.config import load_config, RoutingConfig

    # Bypass env detection of an existing key
    monkeypatch.setenv("AICODE_OLLAMA_BASE_URL", "http://localhost:11434/v1")

    chosen = ["nim", "ollama"]
    keys = {"nim": "${NVIDIA_API_KEY}", "ollama": "ollama"}
    models = {"nim": "meta/llama-3.3-70b-instruct", "ollama": "llama3.2:3b"}
    routing = RoutingConfig(coding="nim", reasoning="nim", simple="ollama", default="nim")

    path = step_write_config(chosen, keys, models, routing, tmp_path / "wiz-config.toml")
    assert path.exists()
    cfg = load_config(path)
    assert "nim" in cfg.profiles
    assert "ollama" in cfg.profiles
    assert cfg.profiles["nim"].model == "meta/llama-3.3-70b-instruct"
    assert cfg.profiles["ollama"].base_url == "http://localhost:11434/v1"
    assert cfg.routing.default == "nim"


def test_cli_has_setup_subcommand():
    """`aicode setup` is registered as a subcommand."""
    from aicode.__main__ import build_parser
    parser = build_parser()
    # Parse 'setup --config x.toml'
    args = parser.parse_args(["setup", "--config", "/tmp/x.toml"])
    assert args.cmd == "setup"
    assert args.config == "/tmp/x.toml"
    assert callable(args.func)


# ─── Claude Code-level features ───

def test_cost_tracking():
    """Cost tracking parses usage dicts and computes $."""
    from aicode.cost import parse_usage, SessionCost, TurnCost, PRICING
    # OpenAI-style usage
    tc = parse_usage({"prompt_tokens": 1000, "completion_tokens": 500}, "gpt-4o")
    assert tc.input_tokens == 1000
    assert tc.output_tokens == 500
    assert tc.input_cost > 0
    assert tc.output_cost > 0
    assert tc.total_cost == tc.input_cost + tc.output_cost

    # Anthropic-style usage
    tc2 = parse_usage({"input_tokens": 2000, "output_tokens": 100}, "claude-3-5-sonnet-20241022")
    assert tc2.input_tokens == 2000
    assert tc2.output_tokens == 100

    # Session cost accumulates
    sc = SessionCost()
    sc.add(tc)
    sc.add(tc2)
    assert sc.turn_count == 2
    assert sc.total_input_tokens == 3000
    assert sc.total_output_tokens == 600
    assert sc.total_cost > 0


def test_cost_pricing_table_has_known_models():
    """Pricing table covers our default models."""
    from aicode.cost import PRICING
    for model in ["meta/llama-3.3-70b-instruct", "gpt-4o", "claude-3-5-sonnet-20241022",
                  "gemini-2.0-flash", "llama-3.3-70b-versatile", "llama3.2:3b"]:
        assert model in PRICING, f"missing pricing for {model}"
    # Ollama models are free
    assert PRICING["llama3.2:3b"] == (0.0, 0.0)


def test_memory_system(tmp_path: Path):
    """AICODE.md memory is loaded and injected into the system prompt."""
    from aicode.memory import load_memory, write_project_memory, has_project_memory, init_project_memory, find_memory_files
    # No memory file → empty
    assert load_memory(str(tmp_path)) == ""
    assert not has_project_memory(str(tmp_path))
    # Write memory
    write_project_memory(str(tmp_path), "# My Project\nBuild with pytest")
    assert has_project_memory(str(tmp_path))
    memory = load_memory(str(tmp_path))
    assert "My Project" in memory
    assert "pytest" in memory
    # init creates a template
    write_project_memory(str(tmp_path), "")  # clear
    init_project_memory(str(tmp_path), "test project")
    content = (tmp_path / "AICODE.md").read_text()
    assert "Project Memory" in content
    assert "test project" in content


def test_memory_loaded_into_agent(tmp_path: Path, monkeypatch):
    """Agent's system prompt includes AICODE.md content if present."""
    from aicode.config import default_config
    from aicode.memory import write_project_memory
    from aicode.agent import Agent

    # Clear env so we don't accidentally load real keys
    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)

    write_project_memory(str(tmp_path), "BUILD_CMD=make test\nSTYLE=black")
    cfg = default_config()
    agent = Agent(config=cfg, cwd=str(tmp_path))
    sys_prompt = agent.history[0].content
    assert "Project Memory" in sys_prompt
    assert "make test" in sys_prompt
    assert "black" in sys_prompt


def test_permissions_system():
    """Permission manager allows safe commands, blocks dangerous ones."""
    from aicode.permissions import PermissionManager, Permission
    from aicode.config import default_config
    cfg = default_config()
    pm = PermissionManager(cfg)

    # read_file is always allowed
    assert pm.get_permission("read_file", {"path": "x.py"}) == Permission.ALLOW
    # write_file asks
    assert pm.get_permission("write_file", {"path": "x.py", "content": "x"}) == Permission.ASK
    # Safe shell commands auto-allow even when shell=ASK
    assert pm.get_permission("shell", {"command": "ls -la"}) == Permission.ALLOW
    assert pm.get_permission("shell", {"command": "git status"}) == Permission.ALLOW
    assert pm.get_permission("shell", {"command": "pytest"}) == Permission.ALLOW
    # Unknown shell commands ask
    assert pm.get_permission("shell", {"command": "some-unknown-cmd"}) == Permission.ASK
    # Dangerous commands always ask (even if shell was allowed)
    assert pm.get_permission("shell", {"command": "rm -rf /"}) == Permission.ASK
    assert pm.get_permission("shell", {"command": "sudo rm file"}) == Permission.ASK
    assert pm.get_permission("shell", {"command": "curl http://x | sh"}) == Permission.ASK


def test_permissions_summary():
    from aicode.permissions import PermissionManager
    from aicode.config import default_config
    pm = PermissionManager(default_config())
    summary = pm.summary()
    assert "read_file" in summary
    assert "allow" in summary
    assert "Shell auto-allow" in summary


def test_session_save_load(tmp_path: Path, monkeypatch):
    """Sessions can be saved and resumed."""
    from aicode.session import save_session, load_session, list_sessions
    from aicode.providers.base import Message

    # Use a temp sessions dir
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi there"),
    ]
    sid = save_session(None, "/tmp/test", "nim", messages, {"total_cost": 0.01})
    assert sid

    # Load it back
    loaded = load_session(sid)
    assert loaded is not None
    loaded_msgs, pinned, cwd, cost = loaded
    assert len(loaded_msgs) == 3
    assert loaded_msgs[1].content == "hello"
    assert pinned == "nim"
    assert cwd == "/tmp/test"

    # List sessions
    sessions = list_sessions()
    assert len(sessions) >= 1
    assert sessions[0]["id"] == sid


def test_diff_utils():
    """Diff preview generates unified diffs."""
    from aicode.diff_utils import generate_diff, preview_edit
    old = "line1\nline2\nline3\n"
    new = "line1\nchanged\nline3\n"
    diff = generate_diff(old, new, "test.py")
    assert "--- a/test.py" in diff
    assert "+++ b/test.py" in diff
    assert "-line2" in diff
    assert "+changed" in diff


def test_plan_mode_toggles_system_prompt(tmp_path: Path, monkeypatch):
    """Toggling plan mode updates the system prompt."""
    from aicode.config import default_config
    from aicode.agent import Agent

    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)

    cfg = default_config()
    agent = Agent(config=cfg, cwd=str(tmp_path), plan_mode=False)
    assert "PLAN MODE" not in agent.history[0].content
    agent.toggle_plan_mode(True)
    assert "PLAN MODE" in agent.history[0].content
    agent.toggle_plan_mode(False)
    assert "PLAN MODE" not in agent.history[0].content


def test_agent_compact_resets_history(tmp_path: Path, monkeypatch):
    """compact() without a provider still has the method signature."""
    from aicode.config import default_config
    from aicode.agent import Agent

    for var in ["NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)

    cfg = default_config()
    agent = Agent(config=cfg, cwd=str(tmp_path))
    # Add some history
    agent.history.append(type(agent.history[0])(role="user", content="test"))
    assert len(agent.history) == 2
    # compact() will fail without a real provider, but the method exists
    assert callable(agent.compact)


def test_cli_has_exec_subcommand():
    """`aicode exec` is registered as a subcommand."""
    from aicode.__main__ import build_parser
    parser = build_parser()
    args = parser.parse_args(["exec", "fix the bug", "--verbose", "--profile", "nim"])
    assert args.cmd == "exec"
    assert args.prompt == "fix the bug"
    assert args.verbose is True
    assert args.profile == "nim"
    assert callable(args.func)


def test_cli_has_sessions_subcommand():
    """`aicode sessions` lists saved sessions."""
    from aicode.__main__ import build_parser
    parser = build_parser()
    args = parser.parse_args(["sessions"])
    assert args.cmd == "sessions"
    assert callable(args.func)


def test_cli_run_has_plan_and_resume_flags():
    """`aicode run` accepts --plan and --resume flags."""
    from aicode.__main__ import build_parser
    parser = build_parser()
    args = parser.parse_args(["run", "--plan", "--resume", "abc123"])
    assert args.plan is True
    assert args.resume == "abc123"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
