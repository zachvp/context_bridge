#!/bin/bash
# Entry point for the MCP server registration. Resolves its own directory
# from $0 so `cd` lands in context_bridge/ regardless of the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Load user config if present (sets CONTEXT_BRIDGE_DB_PATH etc.)
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi

# Use the local venv if available, otherwise fall back to system python3.
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
if [ ! -f "$PYTHON" ]; then
    PYTHON=python3
fi

# Preflight: verify required packages are importable before handing off to
# the MCP runtime, which surfaces errors poorly over stdio.
if ! "$PYTHON" -c "import mcp, sentence_transformers, numpy" 2>/dev/null; then
    echo "Error: required Python packages not found." >&2
    echo "Run: $SCRIPT_DIR/install.sh" >&2
    exit 1
fi

exec "$PYTHON" server.py
