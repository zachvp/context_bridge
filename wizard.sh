#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_CMD="$SCRIPT_DIR/run_server.sh"
VENV_DIR="$SCRIPT_DIR/.venv"

usage() {
    cat <<EOF
Usage: ./wizard.sh [-h]

Interactive setup wizard. Guides you through:
  - Registering context-bridge as an MCP server in Claude Code
  - Creating a Python virtual environment at .venv/
  - Installing dependencies from requirements.txt
  - Writing .env with your database path

Run once after cloning. Re-run to update the MCP registration or change settings.

Options:
  -h, --help    Show this message and exit.

Prerequisites:
  - Python 3.13+     (python3 --version)
  - Claude Code CLI  (claude --version)
EOF
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { usage; exit 0; }

# --- Prerequisites ---
PYTHON_OK=0
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [[ "$PY_MAJOR" -gt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -ge 13 ) ]]; then
    PYTHON_OK=1
fi

if [[ "$PYTHON_OK" -eq 0 ]]; then
    echo "Error: Python 3.13+ required (found: ${PY_VERSION:-none})."
    echo "  Install from https://python.org or via your system package manager."
    exit 1
fi

if ! command -v claude &> /dev/null; then
    echo "Error: Claude Code CLI not found."
    echo "  Install from https://claude.ai/code and make sure 'claude' is on your PATH."
    exit 1
fi

echo "Context Bridge Setup"
echo "===================="
echo ""

# --- Step 1: MCP registration scope ---
echo "Where should context-bridge be registered?"
echo "  [1] Global — works in all Claude Code sessions (recommended)"
echo "  [2] Project-only — only when Claude Code opens this folder"
echo ""
printf "> "
read -r SCOPE_CHOICE

case "$SCOPE_CHOICE" in
    2)
        SCOPE="local"
        SCOPE_DESC="project-only"
        ;;
    *)
        SCOPE="user"
        SCOPE_DESC="global"
        ;;
esac

# --- Step 2: Handle existing registration ---
if claude mcp get context-bridge > /dev/null 2>&1; then
    echo ""
    printf "context-bridge is already registered. Overwrite? [y/N] "
    read -r OVERWRITE
    if [[ "$OVERWRITE" =~ ^[Yy]$ ]]; then
        claude mcp remove context-bridge
    else
        echo "Aborting."
        exit 0
    fi
fi

# --- Step 3: DB path ---
DEFAULT_DB="$SCRIPT_DIR/chat_memory.db"
echo ""
echo "Where should the database be stored?"
echo "  [1] Default: $DEFAULT_DB"
echo "  [2] Custom path"
echo ""
printf "> "
read -r DB_CHOICE

if [ "$DB_CHOICE" = "2" ]; then
    printf "Enter absolute path: "
    read -r DB_PATH
    DB_PATH="${DB_PATH/#\~/$HOME}"
else
    DB_PATH="$DEFAULT_DB"
fi

# --- Step 4: Python virtual environment ---
echo ""
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment already exists at .venv — skipping creation."
else
    echo "Creating virtual environment at .venv..."
    python3 -m venv "$VENV_DIR"
    echo "  Created."
fi

echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "  Done."

# --- Step 5: Register MCP server ---
echo ""
claude mcp add --scope "$SCOPE" context-bridge "$SERVER_CMD"
echo "  Registered ($SCOPE_DESC)."

# --- Step 6: Write .env ---
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    echo ""
    echo "  .env already exists — skipping. Edit manually to change CONTEXT_BRIDGE_DB_PATH."
else
    sed "s|CONTEXT_BRIDGE_DB_PATH=.*|CONTEXT_BRIDGE_DB_PATH=$DB_PATH|" \
        "$SCRIPT_DIR/.env.example" > "$ENV_FILE"
    echo "  Wrote .env (DB: $DB_PATH)."
fi

# --- Done ---
echo ""
echo "Done! Next steps:"
echo ""
echo "  1. Export your Claude.ai data:"
echo "     Claude.ai → Settings → Account → Export Data"
echo "     Anthropic emails a .dms file within a few minutes."
echo ""
echo "  2. Build the database:"
echo "     ./build_all.sh path/to/export.dms"
echo "     (run ./build_all.sh --help for full options)"
echo ""
echo "  3. Restart Claude Code for the MCP server to take effect."
