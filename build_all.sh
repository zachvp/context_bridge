#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<EOF
Usage: ./build_all.sh [--log FILE] [-h] [EXPORT]

Rebuild chat_memory.db from a Claude.ai export and Claude Code sessions.

Arguments:
  EXPORT        Path to a .dms or .zip Claude.ai export file.
                Omit if data/inspect/ is already populated.

Options:
  --log FILE    Tee all output to FILE (printed live and saved to disk).
  -h, --help    Show this message and exit.

Steps run in order:
  1. Unpack EXPORT to data/inspect/  (skipped if no EXPORT given)
  2. python3 build_db.py             (Claude.ai export -> DB)
  3. python3 ingest_code_sessions.py (Claude Code sessions -> DB, incremental)

Always run in this order — build_db.py replaces the DB file entirely, so
ingest_code_sessions.py must follow it or its data is wiped.
EOF
}

# --- Parse options ---
LOG_FILE=""
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --log)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --log requires a file path" >&2
                exit 1
            fi
            LOG_FILE="$2"
            shift 2
            ;;
        *) POSITIONAL_ARGS+=("$1"); shift ;;
    esac
done
set -- "${POSITIONAL_ARGS[@]+"${POSITIONAL_ARGS[@]}"}"

if [[ -n "$LOG_FILE" ]]; then
    exec > >(tee "$LOG_FILE") 2>&1
fi

# Report which step failed on any error exit.
trap 'echo "" >&2; echo "Error: build_all.sh failed at line $LINENO (exit $?)" >&2' ERR

# Load .env if present (sets CONTEXT_BRIDGE_DB_PATH etc.)
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env"
    set +a
fi

cd "$SCRIPT_DIR"

# --- Optional Step 0: unpack export file ---
if [ $# -ge 1 ] && [ -n "$1" ]; then
    EXPORT_FILE="${1/#\~/$HOME}"

    # Resolve to absolute path
    case "$EXPORT_FILE" in
        /*) ;;
        *) EXPORT_FILE="$SCRIPT_DIR/$EXPORT_FILE" ;;
    esac

    if [ ! -f "$EXPORT_FILE" ]; then
        echo "Error: export file not found: $EXPORT_FILE" >&2
        exit 1
    fi

    # .dms is just a ZIP with a non-standard extension
    if [[ "$EXPORT_FILE" == *.dms ]]; then
        ZIP_FILE="${EXPORT_FILE%.dms}.zip"
        cp "$EXPORT_FILE" "$ZIP_FILE"
        EXPORT_FILE="$ZIP_FILE"
        echo "=== Step 0: Renamed .dms → .zip ==="
        echo "  $ZIP_FILE"
        echo ""
    fi

    echo "=== Step 0: Unpacking export ==="
    rm -rf "$SCRIPT_DIR/data/inspect"
    mkdir -p "$SCRIPT_DIR/data/inspect"
    unzip -q "$EXPORT_FILE" -d "$SCRIPT_DIR/data/inspect"
    echo "  Unpacked to data/inspect/"
    echo ""

    # Don't pass the file arg on to build_db.py
    shift
fi

echo "=== Step 1: Claude.ai export ==="
python3 build_db.py "$@"

echo ""
echo "=== Step 2: Claude Code sessions ==="
python3 ingest_code_sessions.py
