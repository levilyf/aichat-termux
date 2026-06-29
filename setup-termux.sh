#!/data/data/com.termux/files/usr/bin/bash
# setup-termux.sh — install aicode on Termux
#
# Usage:
#   pkg update && pkg install -y python git
#   git clone <your-repo> aicode && cd aicode
#   bash setup-termux.sh
#
# Or one-liner (after copying this folder into Termux):
#   bash setup-termux.sh

set -euo pipefail

cyan="\033[36m"; green="\033[32m"; yellow="\033[33m"; red="\033[31m"; reset="\033[0m"
log()  { echo -e "${cyan}[aicode]${reset} $*"; }
ok()   { echo -e "${green}✓${reset} $*"; }
warn() { echo -e "${yellow}!${reset} $*"; }
err()  { echo -e "${red}✗${reset} $*" >&2; }

# --- 1. Detect Termux ---
if [ ! -d "/data/data/com.termux" ]; then
  warn "Not running inside Termux — proceeding anyway, but some steps may differ."
fi

# --- 2. Ensure system packages ---
log "Checking system packages..."
if command -v pkg >/dev/null 2>&1; then
  pkg update -y >/dev/null 2>&1 || true
  for p in python python-pip git rust clang make; do
    if ! command -v "${p/python/python3}" >/dev/null 2>&1; then
      log "installing ${p}..."
      pkg install -y "$p" >/dev/null 2>&1 || warn "could not install ${p}"
    fi
  done
  ok "system packages ready"
elif command -v apt >/dev/null 2>&1; then
  sudo apt update -y >/dev/null 2>&1 || true
  sudo apt install -y python3 python3-pip python3-venv git build-essential >/dev/null 2>&1 || true
  ok "system packages ready"
else
  warn "no pkg/apt detected — assuming python3 + pip already available"
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  err "python3 not found. Install it first: pkg install python"
  exit 1
fi

# --- 3. Create venv (optional but recommended) ---
log "Setting up virtual environment..."
VENV_DIR="${VENV_DIR:-$HOME/.aicode-venv}"
if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON" -m venv "$VENV_DIR" >/dev/null 2>&1 || {
    warn "venv creation failed — installing into user site-packages instead"
  }
fi
if [ -d "$VENV_DIR" ]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  PYTHON=python
  ok "venv at $VENV_DIR"
fi

# --- 4. Install aicode ---
log "Installing aicode..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

pip install --upgrade pip >/dev/null 2>&1 || true
pip install -e . >/dev/null 2>&1 || {
  err "pip install failed. Trying with --user..."
  pip install --user -e .
}

if ! command -v aicode >/dev/null 2>&1; then
  warn "aicode not on PATH. Add this to your ~/.bashrc:"
  echo "  export PATH=\"$VENV_DIR/bin:\$PATH\""
  echo "  # or: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
ok "aicode installed"

# --- 5. Initialize config via the wizard (or skip with --no-wizard) ---
log "Initializing config..."
CONFIG_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/aicode/config.toml"
if [ "${AICODE_NO_WIZARD:-0}" = "1" ]; then
  if [ ! -f "$CONFIG_PATH" ]; then
    aicode config init >/dev/null 2>&1 || true
    ok "wrote default config to $CONFIG_PATH (wizard skipped)"
  else
    ok "config already exists at $CONFIG_PATH"
  fi
else
  if [ ! -f "$CONFIG_PATH" ] || [ "${AICODE_FORCE_WIZARD:-0}" = "1" ]; then
    log "Launching setup wizard..."
    aicode setup || warn "wizard exited with error — you can re-run: aicode setup"
  else
    ok "config already exists at $CONFIG_PATH (run 'aicode setup' to reconfigure)"
  fi
fi

# --- 6. Show where to get API keys (in case the wizard was skipped) ---
echo ""
if [ "${AICODE_NO_WIZARD:-0}" = "1" ]; then
  log "API keys — get them from:"
  echo "  NVIDIA NIM : https://build.nvidia.com  (free tier, recommended)"
  echo "  OpenAI     : https://platform.openai.com/api-keys"
  echo "  Anthropic  : https://console.anthropic.com/settings/keys"
  echo "  Gemini     : https://aistudio.google.com/app/apikey"
  echo "  Groq       : https://console.groq.com/keys"
  echo "  OpenRouter : https://openrouter.ai/keys"
  echo ""
  echo "  Then run: aicode setup"
fi

# --- 7. Doctor ---
echo ""
log "Running doctor check..."
aicode doctor || warn "doctor reported issues — run 'aicode setup' to fix"

echo ""
ok "Setup complete! Run: aicode"
echo ""
echo "  Quick start:"
echo "    aicode                  # launch TUI"
echo "    aicode setup            # reconfigure anytime"
echo "    aicode --profile nim    # pin a profile"
echo "    aicode config show      # see resolved config"
echo "    aicode doctor           # diagnose"
