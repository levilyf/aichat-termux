# Design Document

## Overview

aicode is a terminal-based AI coding agent for Termux/Linux. It provides a
tool-calling agent loop across multiple LLM providers (NVIDIA NIM, OpenAI,
Anthropic, Gemini, Groq, OpenRouter, Ollama) with a Textual TUI.

## Architecture

```
CLI (__main__.py)
├── run → TUI (tui/app.py)
├── exec → non-interactive (exec_mode.py)
├── setup → wizard (wizard.py)
├── doctor → diagnostics
└── sessions → list saved sessions

Agent (agent/loop.py)
├── Provider (providers/) — streams chat completions
├── ToolRegistry (tools/) — file ops, shell, git, MCP
├── Router (providers/registry.py) — picks profile per task
├── Permissions (permissions.py) — allow/deny/ask per tool
├── Memory (memory.py) — AICODE.md project context
├── Cost (cost.py) — token + $ tracking
├── Session (session.py) — save/resume conversations
└── AutoCommit (autocommit.py) — git commit after edits

Config (config.py) — TOML at ~/.config/aicode/config.toml
UI (ui.py) — shared rich console + questionary helpers
```

## Key design decisions

### 1. Composition over inheritance

The `Agent` class does not subclass anything. The UI layer passes callbacks
(`on_event`, `on_permission_request`) to the Agent constructor. The Agent
emits events as it runs; the UI subscribes. This avoids the dead-code pattern
where `request_approval` was defined on both Agent and AICodeApp but neither
called the other.

### 2. Agent.chat() is a coroutine, not an async generator

`chat()` returns `None` and emits events via `on_event`. Callers `await`
it. Earlier versions declared it as `AsyncIterator[AgentEvent]` but used
`return` instead of `yield`, which raised `TypeError` at runtime.

### 3. Provider abstraction

All providers implement `async chat(messages, tools, on_delta) -> Response`.
OpenAI-compatible providers (NIM, OpenAI, Groq, OpenRouter, Ollama, etc.)
share `OpenAICompatProvider` — only `base_url` and auth headers differ.
Anthropic and Gemini have native implementations due to their different APIs.

### 4. Tool registry

Tools subclass `Tool` and implement `run(**args) -> ToolResult`. The
registry dispatches by name. MCP tools are prefixed `mcp_<server>_<tool>`
to avoid collisions and routed through `MCPManager.call_tool()` directly
(not through the registry's `execute`).

### 5. Permission model

Each tool call goes through `PermissionManager.get_permission()` which
returns `ALLOW`, `ASK`, or `DENY`. For `ASK`, the Agent calls the
`on_permission_request` callback. If no callback is registered, the default
is DENY (safe). Shell commands have pattern-based overrides: safe commands
(`ls`, `git status`, `pytest`) auto-allow; dangerous commands (`rm -rf /`,
`sudo`) always ask.

### 6. Config is the single source of truth

`config.toml` defines profiles (provider + model + key), routing rules,
custom commands, MCP servers, and auto-commit settings. The wizard writes
it; `load_config()` reads it; all modules consume the `Config` dataclass.

## Conventions

- **Console**: use `from .ui import console` everywhere. Do not create local
  `Console()` instances. Do not use bare `print()`.
- **Type hints**: use `List`, `Dict`, `Optional` from `typing` (not PEP 585
  `list[str]`) for consistency across the codebase.
- **Error handling**: catch specific exceptions (`httpx.HTTPError`,
  `json.JSONDecodeError`). Do not use bare `except Exception` unless
  re-raising as a user-facing error message.
- **Imports**: top-level for standard imports. Function-local imports only
  for breaking circular dependencies (and there shouldn't be any).
- **Tests**: test behavior, not implementation. Every public function should
  have at least one test that exercises it end-to-end. Tautological tests
  (`assert callable(x)`) are forbidden.

## Known limitations

- The TUI's `ToolCallWidget` has a `toggle()` method but no click/keyboard
  handler is registered, so collapsible tool calls can't be expanded in the
  UI yet.
- The `[permissions]` config section for per-tool overrides is not
  implemented — only hardcoded defaults apply.
- `$FILE` substitution in custom commands is documented but not implemented.
  Only `$ARGS` works.
- `diff_utils.py` exists but is not wired into the edit/write tools.
- Top-level CLI flags (`aicode --plan`, `aicode --resume <id>`) don't work
  without the `run` subcommand.
