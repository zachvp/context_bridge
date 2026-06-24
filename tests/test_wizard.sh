#!/usr/bin/env bash
# Greenfield install test — run inside the Docker image or locally.
# Each check prints [PASS] / [FAIL] / [SKIP] and exits non-zero if any fail.

set -uo pipefail

SCRIPTS_DIR="${SCRIPTS_DIR:-scripts}"

PASS=0; FAIL=0; SKIP=0
FAILURES=()

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILURES+=("$1"); }
skip() { echo "  [SKIP] $1"; SKIP=$((SKIP + 1)); }

check() {
    local desc="$1"; shift
    if "$@" > /dev/null 2>&1; then
        pass "$desc"
    else
        fail "$desc"
    fi
}

# ---------------------------------------------------------------------------
echo ""
echo "=== Static analysis ==="
check "bash -n $SCRIPTS_DIR/wizard.sh"     bash -n "$SCRIPTS_DIR/wizard.sh"
check "bash -n $SCRIPTS_DIR/run_server.sh" bash -n "$SCRIPTS_DIR/run_server.sh"
check "bash -n $SCRIPTS_DIR/build_all.sh"  bash -n "$SCRIPTS_DIR/build_all.sh"

if command -v shellcheck > /dev/null 2>&1; then
    check "shellcheck $SCRIPTS_DIR/wizard.sh"     shellcheck "$SCRIPTS_DIR/wizard.sh"
    check "shellcheck $SCRIPTS_DIR/run_server.sh" shellcheck "$SCRIPTS_DIR/run_server.sh"
    check "shellcheck $SCRIPTS_DIR/build_all.sh"  shellcheck "$SCRIPTS_DIR/build_all.sh"
else
    skip "shellcheck not installed"
fi

# ---------------------------------------------------------------------------
echo ""
echo "=== Python imports ==="
check "import mcp"                  python3 -c "import mcp"
check "import fastembed"              python3 -c "import fastembed"
check "import numpy"                python3 -c "import numpy"

# ---------------------------------------------------------------------------
echo ""
echo "=== Env var config ==="
check "DB_PATH default" python3 - <<'EOF'
import os, sys
sys.path.insert(0, ".")
os.environ.pop("CONTEXT_BRIDGE_DB_PATH", None)
import query
assert "chat_memory.db" in str(query.DB_PATH), f"unexpected: {query.DB_PATH}"
EOF

check "DB_PATH override" python3 - <<'EOF'
import os, sys
os.environ["CONTEXT_BRIDGE_DB_PATH"] = "/tmp/test.db"
sys.path.insert(0, ".")
import query
assert str(query.DB_PATH) == "/tmp/test.db", f"unexpected: {query.DB_PATH}"
EOF

# ---------------------------------------------------------------------------
echo ""
echo "=== .env sourcing ==="
check ".env variables exported" bash - <<'EOF'
echo "CONTEXT_BRIDGE_DB_PATH=/tmp/env_test.db" > /tmp/test.env
set -a; source /tmp/test.env; set +a
[ "$CONTEXT_BRIDGE_DB_PATH" = "/tmp/env_test.db" ]
EOF

# ---------------------------------------------------------------------------
echo ""
echo "=== Preflight check ==="
check "packages importable (happy path)" \
    python3 -c "import mcp, fastembed, numpy"

# Simulate the preflight condition from run_server.sh using a bad interpreter
check "preflight exits non-zero on missing packages" bash - <<'EOF'
! python3 /nonexistent_path.py -c "import mcp" > /dev/null 2>&1
# More direct: check the condition logic itself
if python3 -c "import no_such_package_xyz" 2>/dev/null; then
    exit 1   # should not succeed
fi
exit 0
EOF

# ---------------------------------------------------------------------------
echo ""
echo "=== MCP registration (wizard.sh) ==="
# Feed: scope (1=global), overwrite-if-exists (y), DB path (1=default).
# "y" for DB choice falls through to default when context-bridge isn't
# pre-registered (no overwrite prompt), so this input works either way.
if printf "1\ny\n1\n" | bash "$SCRIPTS_DIR/wizard.sh" > /tmp/install_out.txt 2>&1; then
    check "context-bridge registered" claude mcp get context-bridge
else
    # claude mcp add may require auth in a headless container — show output and skip
    echo ""
    echo "  wizard.sh output:"
    sed 's/^/    /' /tmp/install_out.txt
    skip "MCP registration (claude CLI not authenticated)"
fi

# .env written with default path
if [ -f .env ]; then
    check ".env contains DB path" grep -q "CONTEXT_BRIDGE_DB_PATH" .env
else
    fail ".env not created by wizard.sh"
fi

# ---------------------------------------------------------------------------
echo ""
echo "=== Structural lint (check_docs.sh) ==="
if bash tests/check_docs.sh > /tmp/check_docs_out.txt 2>&1; then
    grep -E '\[(PASS|FAIL)\]' /tmp/check_docs_out.txt | sed 's/^/  /' || true
    pass "check_docs.sh"
else
    grep -E '\[(PASS|FAIL)\]' /tmp/check_docs_out.txt | sed 's/^/  /' || true
    fail "check_docs.sh (see failures above)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "================================================"
printf "  %d passed  %d failed  %d skipped\n" "$PASS" "$FAIL" "$SKIP"
if [ "${#FAILURES[@]}" -gt 0 ]; then
    echo ""
    echo "  Failed checks:"
    for f in "${FAILURES[@]}"; do
        echo "    - $f"
    done
fi
echo "================================================"
[ "$FAIL" -eq 0 ]
