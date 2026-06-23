#!/usr/bin/env bash
# Structural lint: validates that config files, scripts, and docs agree.
# Run from context_bridge/ root.
# Exits non-zero if any check fails.
set -uo pipefail

PASS=0; FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

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
echo "=== Version parity ==="
PY_VERSION=$(cat .python-version 2>/dev/null | tr -d '[:space:]')
if [[ -z "$PY_VERSION" ]]; then
    fail ".python-version missing or empty"
else
    check ".python-version ($PY_VERSION) matches Dockerfile FROM" \
        grep -q "FROM python:${PY_VERSION}" tests/Dockerfile
fi

# ---------------------------------------------------------------------------
echo ""
echo "=== --help exits 0 ==="
check "install.sh --help"    bash install.sh --help
check "build_all.sh --help"  bash build_all.sh --help
check "run_server.sh --help" bash run_server.sh --help

# ---------------------------------------------------------------------------
echo ""
echo "=== Test files exist ==="
check "tests/smoke_test.py"           test -f tests/smoke_test.py
check "tests/retrieval_smoke_test.py" test -f tests/retrieval_smoke_test.py
check "tests/mcp_smoke_test.py"       test -f tests/mcp_smoke_test.py
check "tests/test_build_db.py"        test -f tests/test_build_db.py
check "tests/test_install.sh"         test -f tests/test_install.sh
check "tests/check_docs.sh"           test -f tests/check_docs.sh

# ---------------------------------------------------------------------------
echo ""
echo "=== Required project files ==="
check "requirements.txt"  test -f requirements.txt
check ".env.example"      test -f .env.example
check "schema.sql"        test -f schema.sql
check "build_all.sh"      test -f build_all.sh
check "install.sh"        test -f install.sh
check "run_server.sh"     test -f run_server.sh

# ---------------------------------------------------------------------------
echo ""
echo "================================================"
printf "  %d passed  %d failed\n" "$PASS" "$FAIL"
echo "================================================"
[ "$FAIL" -eq 0 ]
