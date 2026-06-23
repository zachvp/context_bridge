#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_CMD="$SCRIPT_DIR/run_server.sh"
VENV_DIR="$SCRIPT_DIR/.venv"

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
echo "     Anthropic emails a .dms file — it's a ZIP; rename it to .zip."
echo ""
echo "  2. Build the database:"
echo "     cd $SCRIPT_DIR"
echo "     mkdir -p data/inspect"
echo "     unzip data/chat-archive-<date>.zip -d data/inspect"
echo "     python3 build_db.py"
echo ""
echo "  3. Restart Claude Code for the MCP server to take effect."
