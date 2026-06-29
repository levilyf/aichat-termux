# aicode — AI coding agent for Termux

An autonomous coding agent that runs in your terminal — built for Termux (Android) but works on any Linux/macOS box. Like **Claude Code** / `opencode`, but with **first-class support for NVIDIA NIM** and 6+ other LLM providers.

## Features

### Claude Code-level capabilities
- **Full TUI** (Textual) — sidebar with file tree, streaming chat panel, status bar with live cost, multi-line input
- **Modern CLI UX** — rich panels + tables + spinners, questionary interactive prompts (checkboxes, searchable selects), rich-argparse --help, animated splash screen
- **Plan mode** — `/plan` makes the agent read-only and produce a plan before any changes
- **AICODE.md memory** — project context auto-loaded into every turn (like CLAUDE.md)
- **Cost tracking** — tokens + $ per turn and per session, in the status bar and via `/status` `/cost`
- **Session save/resume** — `/save` persists conversation, `aicode --resume <id>` restores it
- **`/compact`** — summarize conversation history to save context
- **`/init`** — analyze project and generate AICODE.md
- **`/review`** — review uncommitted git changes
- **Permissions** — fine-grained allow/deny/ask rules per tool + safe/dangerous shell patterns + **modal popup** for approvals
- **Non-interactive mode** — `aicode exec "prompt"` + stdin piping for scripting
- **Auto-routing** — picks the best profile for each task (coding / reasoning / simple), with smart fallback to only usable profiles
- **Custom slash commands** — define your own `/commands` in config.toml with `$ARGS` substitution
- **Auto-commit** — automatically git commit after successful edits (configurable)
- **MCP support** — connect to Model Context Protocol servers for external tools (filesystem, databases, APIs)
- **Collapsible tool calls** — tool output starts collapsed, click/expand to see details

### Provider support
- **7 providers out of the box** — NVIDIA NIM, OpenAI, Anthropic Claude, Google Gemini, Groq, OpenRouter, Ollama (local)
- **Interactive setup wizard** — pick providers, paste keys, live validation, **fetch live model lists from each provider's API**, browse and pick models, write config

### Agent loop
- **Tool-calling agent loop** — reads, writes, edits files; runs shell commands; git ops
- **Multi-file edits** — apply several edits per turn via the agent loop
- **Safety** — dangerous shell commands require approval; tool output is truncated to keep context lean
- **Streaming** — text deltas stream in as the model types; tool calls render inline

## Quick install (Termux)

```bash
# 1. Termux system deps
pkg update && pkg install -y python git

# 2. Clone / copy this folder, then:
cd aicode-agent
bash setup-termux.sh

# 3. Run the interactive setup wizard (picks providers, tests keys, writes config)
aicode setup

# 4. Launch the TUI
aicode
```

The wizard walks you through everything: which providers to enable, pasting API keys (with masking), live key validation, **fetching the actual model list from each provider's API**, browsing and picking models from a paginated searchable table, and configuring auto-routing. First-time users just run `aicode` and it auto-launches the wizard if no config exists.

The model picker shows live data with context length, free-tier markers, vision/tool support, and descriptions — and gracefully falls back to known defaults if the API list call fails.

## Provider setup

Each provider is configured as a **profile** in `~/.config/aicode/config.toml`. API keys can be inlined or referenced as `${ENV_VAR}`. Run `aicode config init` to write a default config you can edit.

### NVIDIA NIM (recommended — generous free tier)

1. Go to <https://build.nvidia.com> → sign in → **Get API Key**
2. Set it:
   ```bash
   export NVIDIA_API_KEY="nvapi-xxxxxxxx"
   ```
3. Default config already wires this up as the `nim` profile using `meta/llama-3.3-70b-instruct`. Other popular models on NIM:
   - `meta/llama-3.3-70b-instruct`
   - `meta/llama-3.1-405b-instruct`
   - `mistralai/mixtral-8x7b-instruct-v0.1`
   - `deepseek-ai/deepseek-r1`
   - `qwen/qwen2.5-coder-32b-instruct` ← excellent for coding
   - `nvidia/llama-3.1-nemotron-70b-instruct`

### OpenAI

```bash
export OPENAI_API_KEY="sk-xxxxxxxx"
```
Default model: `gpt-4o`. Also works: `gpt-4o-mini`, `gpt-4.1`, `o1`, `o3-mini`.

### Anthropic Claude

```bash
export ANTHROPIC_API_KEY="sk-ant-xxxxxxxx"
```
Default model: `claude-3-5-sonnet-20241022`. Also: `claude-3-5-haiku-20241022`, `claude-3-opus-20240229`.

### Google Gemini

```bash
export GEMINI_API_KEY="AIzaSyxxxxxxxx"
```
Get a key at <https://aistudio.google.com/app/apikey>. Default: `gemini-2.0-flash`. Also: `gemini-2.5-pro`, `gemini-1.5-pro`.

### Groq (very fast)

```bash
export GROQ_API_KEY="gsk_xxxxxxxx"
```
Get a key at <https://console.groq.com>. Default: `llama-3.3-70b-versatile`.

### OpenRouter (one key → 100+ models)

```bash
export OPENROUTER_API_KEY="sk-or-xxxxxxxx"
```
Get a key at <https://openrouter.ai>. Default: `anthropic/claude-3.5-sonnet`. Also: `openai/gpt-4o`, `google/gemini-2.0-flash-exp:free`, `meta-llama/llama-3.3-70b-instruct:free`.

### Ollama (local, no API key)

Install Ollama on Termux is non-trivial; if you have a server running elsewhere, point the config at it:

```toml
[profiles.ollama]
provider = "ollama"
model = "llama3.2:3b"
api_key = "ollama"
base_url = "http://192.168.1.10:11434/v1"
```

## Usage

### CLI

```bash
aicode                          # launch TUI (auto-routing on); auto-runs setup wizard if no config
aicode --plan                   # start in plan mode (read-only, plans before executing)
aicode --resume <session-id>    # resume a saved session
aicode -p nim                   # pin a profile
aicode setup                    # interactive setup wizard
aicode exec "fix the bug"       # non-interactive: run one prompt and exit
aicode exec "review this" -v    # verbose: show tool calls + cost
echo "explain" | aicode exec    # pipe stdin as prompt
cat file.py | aicode exec "review this code"
aicode sessions                 # list saved sessions
aicode config init              # write a default config (non-interactive)
aicode config show              # show resolved config + which keys are set
aicode doctor                   # diagnose environment
```

### Inside the TUI

| Command | Action |
|---|---|
| `/help` | show all slash commands |
| `/clear` | reset conversation |
| `/compact` | summarize conversation to save context |
| `/status` | session info: model, tokens, cost, duration |
| `/cost` | detailed per-turn cost breakdown |
| `/save` | save current session to disk |
| `/resume <id>` | resume a saved session |
| `/sessions` | list saved sessions |
| `/model <name>` | pin a profile (e.g. `/model claude`) |
| `/auto` | re-enable auto-routing |
| `/profiles` | list configured profiles + usability |
| `/init` | analyze project and create AICODE.md |
| `/memory` | show AICODE.md content |
| `/memory edit` | write/edit AICODE.md |
| `/files` | refresh file tree |
| `/tools` | list available tools |
| `/commands` | list your custom slash commands |
| `/plan` | toggle plan mode |
| `/review` | review uncommitted changes (git diff) |
| `/permissions` | show permission rules |
| `/autocommit` | toggle auto-commit after edits |
| `/mcp` | show MCP server status |
| `/quit` | exit |
| `/<custom>` | any command you define in `[commands]` |

### AICODE.md — project memory

Like Claude Code's `CLAUDE.md`, aicode auto-loads `AICODE.md` from your project root (and `~/.aicode.md` globally) into every turn. Use it to give the agent persistent context:

```markdown
# Project Memory

## Build & Test
- `pytest tests/` to run tests
- `ruff check .` to lint

## Architecture
- src/ contains the core library
- tests/ uses pytest

## Gotchas
- Don't edit the generated/ folder — it's auto-built
```

Run `/init` inside the TUI to auto-generate a starter AICODE.md, or write one manually.

### Plan mode

Toggle with `/plan` or start with `aicode --plan`. In plan mode:
- The agent **cannot execute any tools** that modify state
- It reads files to understand the codebase, then produces a numbered plan
- You review the plan and toggle off plan mode to execute

Great for exploring unfamiliar code or reviewing proposed changes before they happen.

### Non-interactive mode (`aicode exec`)

Like `claude -p` — runs one turn, prints output, exits. Perfect for scripting:

```bash
# Fix a bug non-interactively
aicode exec "fix the failing test in tests/test_auth.py"

# Pipe a file for review
cat main.py | aicode exec "review this code for security issues"

# Use a specific profile
aicode exec --profile claude "refactor this function to be more readable"

# Verbose mode shows tool calls + cost
aicode exec -v "add type hints to all functions in src/"

# Plan mode (no execution, just output a plan)
aicode exec --plan "how would you add a /health endpoint?"
```

### Cost tracking

The status bar shows live cost. Use `/status` for a full session summary and `/cost` for per-turn breakdown:

```
Session totals — 5 turn(s)
- Input tokens:  45,231
- Output tokens: 8,442
- Total cost:    $0.0872
```

Pricing is built in for all default models (NIM, OpenAI, Anthropic, Gemini, Groq, OpenRouter, Ollama). Edit `aicode/cost.py` to update prices.

### Custom slash commands

Define your own `/commands` in `config.toml`. Use `$ARGS` to inject whatever the user types after the command:

```toml
[commands.test]
description = "Run tests and fix any failures"
prompt = "Run the test suite (try pytest first), then fix any failures."

[commands.refactor]
description = "Refactor a file — /refactor <path>"
prompt = "Refactor $ARGS to be cleaner. Preserve behavior."

[commands.explain]
description = "Explain a file — /explain <path>"
prompt = "Read $ARGS and explain what it does in detail."
```

Then in the TUI:
```
/test
/refactor src/main.py
/explain src/auth/login.py
```

Run `/commands` inside the TUI to see all your custom commands.

### Auto-commit

Automatically git commit after the agent makes successful edits. Configure in `config.toml`:

```toml
[auto_commit]
enabled = true
message_template = "chore: aicode — {summary}"
require_clean_tree = true  # only commit if the tree was clean before
```

Toggle on/off inside the TUI with `/autocommit`. The `{summary}` placeholder is replaced with a git diff stat (e.g. "3 files changed, 10 insertions(+)").

### MCP (Model Context Protocol) support

Connect to external MCP servers to give the agent additional tools (filesystem access, databases, APIs, etc.). Each server runs as a subprocess speaking JSON-RPC over stdio:

```toml
[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/sandbox"]
env = {}

[mcp_servers.sqlite]
command = "uvx"
args = ["mcp-server-sqlite", "--db-path", "./data.db"]
```

On startup, aicode spawns each MCP server, does the initialize handshake, discovers its tools, and registers them in the agent's tool registry with a `mcp_<server>_<tool>` prefix (to avoid name collisions). The agent can then call these tools transparently alongside the built-in ones.

Run `/mcp` inside the TUI to see server status and tool counts. Browse available MCP servers at <https://github.com/modelcontextprotocol/servers>.

### Permissions & approval modals

Every tool call goes through a permission check:
- **allow** — runs without asking (e.g. `read_file`, `list_files`, safe shell commands like `ls`, `git status`)
- **ask** — pops up a modal showing the tool name + arguments, with **Allow (y)** / **Deny (n)** buttons (e.g. `write_file`, `edit_file`, unknown shell commands)
- **deny** — blocked entirely (e.g. `rm -rf /`, `sudo`, `curl | sh`)

Safe shell patterns (auto-allow even when `shell = ask`): `ls`, `pwd`, `cat`, `git status/diff/log`, `pytest`, `ruff`, `npm run`, `go test`, etc.

Dangerous patterns (always ask, even if shell was allowed): `rm -rf /`, `sudo`, `dd`, `mkfs`, `shutdown`, `curl | sh`, `git push --force`.

Run `/permissions` to see the full ruleset.

### Collapsible tool calls

Tool call output in the TUI starts **collapsed** — you see the tool name + args + success/fail icon, but not the (potentially long) output. Click or press Enter on a tool call to expand it and see the full output. This keeps the chat scrollable when the agent runs many tools in a row.

## Architecture

```
aicode-agent/
├── pyproject.toml              # package + entry point
├── requirements.txt
├── setup-termux.sh             # one-shot installer
├── config.example.toml         # fully-documented example config
├── aicode/
│   ├── __main__.py             # CLI: aicode / exec / setup / doctor / sessions
│   ├── config.py               # TOML config + profiles + routing + commands + auto_commit + mcp
│   ├── cost.py                 # Pricing table + TurnCost + SessionCost
│   ├── memory.py               # AICODE.md project memory loader
│   ├── permissions.py          # Permission manager (allow/deny/ask)
│   ├── session.py              # Session save/resume (JSON on disk)
│   ├── diff_utils.py           # Diff preview utilities
│   ├── exec_mode.py            # Non-interactive `aicode exec` mode
│   ├── wizard.py               # Interactive setup wizard (live model fetching, questionary UI)
│   ├── mcp.py                  # MCP client (JSON-RPC over stdio)
│   ├── autocommit.py           # Auto-commit after edits
│   ├── models.py               # Live model list fetchers + KNOWN_MODELS metadata
│   ├── ui.py                   # Modern CLI components (splash, panels, spinners, questionary)
│   ├── providers/
│   │   ├── base.py             # Provider, Message, ToolCall, Response, ToolSpec
│   │   ├── openai_compat.py    # OpenAI/NIM/Groq/OpenRouter/Ollama/Together/...
│   │   ├── anthropic.py        # Claude Messages API + SSE
│   │   ├── gemini.py           # Gemini v1beta generateContent stream
│   │   └── registry.py         # build_provider() + auto-router with fallback
│   ├── tools/
│   │   ├── base.py             # Tool + ToolRegistry
│   │   ├── file_ops.py         # read/write/edit/list/grep
│   │   ├── shell.py            # bash exec with approval gate
│   │   ├── git.py              # git wrapper
│   │   ├── mcp_tool.py         # MCP tool wrapper → bridges into ToolRegistry
│   │   └── registry.py         # default_registry()
│   ├── agent/
│   │   ├── loop.py             # streaming tool-calling loop (plan mode, cost, permissions, auto-commit, MCP)
│   │   └── prompts.py          # system prompt
│   └── tui/
│       ├── app.py              # main Textual app (21+ slash commands)
│       ├── widgets.py          # ChatMessage, FileTreeWidget, StatusBar, ToolCallWidget (collapsible)
│       ├── modals.py           # PermissionModal + ConfirmModal
│       └── styles.tcss         # textual CSS
```

## How auto-routing works

The `Router` classifies each user message into one of three buckets using regex heuristics:

- **coding** — `implement`, `fix`, `write`, `edit`, `test`, code blocks → uses `routing.coding` profile
- **reasoning** — `debug`, `why`, `explain`, `design`, `architect` → `routing.reasoning`
- **simple** — `hi`, `what is`, short questions → `routing.simple`

Defaults in the config route coding tasks to NIM (Llama 3.3 70B), reasoning to Claude, simple to Groq. Edit `[routing]` in `config.toml` to customize. Pin a profile with `/model <name>` to disable auto-routing entirely.

### Important: routing only uses *available* profiles

The router will **never** send a request to a provider whose API key isn't set. If `routing.coding = "claude"` but `ANTHROPIC_API_KEY` is missing, the router walks a built-in preference chain (e.g. `nim → gpt → openrouter → groq → gemini → ollama`) and uses the first **usable** profile instead — emitting a warning so you know.

This means:
- **Only configured NIM?** Every task uses NIM.
- **Lost your Claude key?** Reasoning tasks silently fall back to the next-best option.
- **No keys at all?** The router raises a clear error pointing you to `aicode setup`.

Check which profiles are currently usable with `/profiles` inside the TUI — each shows a ✓ (usable) or ✗ (missing key) marker.

## Extending

### Add a new OpenAI-compatible provider

Just add a profile — no code changes needed:

```toml
[profiles.deepseek]
provider = "deepseek"
model = "deepseek-coder"
api_key = "${DEEPSEEK_API_KEY}"
base_url = "https://api.deepseek.com/v1"
```

The `openai_compat.py` provider already supports `deepseek`, `together`, `fireworks`, `mistral` as `provider` values (see `PROVIDER_DEFAULTS`).

### Add a new tool

```python
# aicode/tools/my_tool.py
from .base import Tool, ToolResult

class MyTool(Tool):
    name = "my_tool"
    description = "Does the thing"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

    def run(self, x: str, **_) -> ToolResult:
        return ToolResult(True, f"did the thing with {x}")
```

Then register it in `aicode/tools/registry.py`:

```python
from .my_tool import MyTool
reg.register(MyTool())
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `aicode: command not found` after install | Run `source ~/.bashrc` or add `export PATH="$HOME/.local/bin:$PATH"` |
| `No API keys configured` | Run `aicode config show` to see which keys are resolved; set the matching env var or edit the config |
| `nim API error 401` | `NVIDIA_API_KEY` is wrong or expired — regenerate at build.nvidia.com |
| Anthropic tool_use errors | Make sure your model is `claude-3-5-sonnet` or newer — older models don't support tool calling |
| TUI renders slowly | Try a smaller model or pin `groq` for fast tasks |
| Ollama connection refused | Set `base_url` to your server's IP, not localhost, in the profile |

## License

MIT — do what you want.
