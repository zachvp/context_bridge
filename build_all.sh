#!/usr/bin/env bash
# Full DB rebuild: Claude.ai export first, then Code sessions on top.
# Always run in this order — build_db.py replaces the DB file entirely,
# so ingest_code_sessions.py must run after it or its data is wiped.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env if present (sets CONTEXT_BRIDGE_DB_PATH etc.)
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env"
    set +a
fi

cd "$SCRIPT_DIR"

echo "=== Step 1: Claude.ai export ==="
python3 build_db.py "$@"

echo ""
echo "=== Step 2: Claude Code sessions ==="
python3 ingest_code_sessions.py
