#!/usr/bin/env bash
# Entry point for the MCP server registration. Resolves its own directory
# from $0 so `cd` lands in context_bridge/ regardless of the caller's cwd.

usage() {
    cat <<EOF
Usage: ./run_server.sh [-h]

Start the context-bridge MCP server (stdio transport).
Registered automatically by wizard.sh — use this for manual smoke checks
outside of a Claude Code session.

Options:
  -h, --help    Show this message and exit.
EOF
}

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { usage; exit 0; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WIZARD_CMD="$SCRIPT_DIR/wizard.sh"
SERVER_SCRIPT="$PROJECT_ROOT/server.py"
cd "$PROJECT_ROOT" || exit 1

# Load user config if present (sets CONTEXT_BRIDGE_DB_PATH etc.)
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi

# Use the local venv if available, otherwise fall back to system python3.
PYTHON="$PROJECT_ROOT/.venv/bin/python3"
if [ ! -f "$PYTHON" ]; then
    PYTHON=python3
fi

# Preflight: verify required packages are importable before handing off to
# the MCP runtime, which surfaces errors poorly over stdio.
if ! "$PYTHON" -c "import mcp, fastembed, numpy" 2>/dev/null; then
    echo "Error: required Python packages not found." >&2
    echo "Run: $WIZARD_CMD" >&2
    exit 1
fi

exec "$PYTHON" "$SERVER_SCRIPT"
