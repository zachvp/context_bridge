#!/usr/bin/env bash
# Structural lint: validates that config files, scripts, and docs agree.
# Run from context_bridge/ root.
# Exits non-zero if any check fails.
set -uo pipefail

SCRIPTS_DIR="${SCRIPTS_DIR:-scripts}"

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
check "$SCRIPTS_DIR/wizard.sh --help"    bash "$SCRIPTS_DIR/wizard.sh" --help
check "$SCRIPTS_DIR/build_all.sh --help"  bash "$SCRIPTS_DIR/build_all.sh" --help
check "$SCRIPTS_DIR/run_server.sh --help" bash "$SCRIPTS_DIR/run_server.sh" --help

# ---------------------------------------------------------------------------
echo ""
echo "=== Test files exist ==="
check "tests/test_build_db.py"             test -f tests/test_build_db.py
check "tests/test_ingest.py"               test -f tests/test_ingest.py
check "tests/test_ingest_code_sessions.py" test -f tests/test_ingest_code_sessions.py
check "tests/test_query.py"                test -f tests/test_query.py
check "tests/conftest.py"                  test -f tests/conftest.py
check "tests/test_wizard.sh"               test -f tests/test_wizard.sh
check "tests/check_docs.sh"                test -f tests/check_docs.sh

# ---------------------------------------------------------------------------
echo ""
echo "=== Required project files ==="
check "requirements.txt"               test -f requirements.txt
check "pyproject.toml"                 test -f pyproject.toml
check ".env.example"                   test -f .env.example
check "migrations/001_initial.sql"     test -f migrations/001_initial.sql
check "$SCRIPTS_DIR/build_all.sh"      test -f "$SCRIPTS_DIR/build_all.sh"
check "$SCRIPTS_DIR/wizard.sh"         test -f "$SCRIPTS_DIR/wizard.sh"
check "$SCRIPTS_DIR/run_server.sh"     test -f "$SCRIPTS_DIR/run_server.sh"
check ".github/workflows/ci.yml"       test -f .github/workflows/ci.yml

# ---------------------------------------------------------------------------
echo ""
echo "================================================"
printf "  %d passed  %d failed\n" "$PASS" "$FAIL"
echo "================================================"
[ "$FAIL" -eq 0 ]
