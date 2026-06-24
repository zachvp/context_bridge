#!/usr/bin/env bash
# Static analysis only: bash syntax check + shellcheck on all shell scripts.

set -uo pipefail

PASS=0; FAIL=0
FAILURES=()

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILURES+=("$1"); }

check() {
    local desc="$1"; shift
    if "$@" > /dev/null 2>&1; then
        pass "$desc"
    else
        fail "$desc"
    fi
}

echo ""
echo "=== Static analysis ==="
check "bash -n wizard.sh"     bash -n wizard.sh
check "bash -n run_server.sh" bash -n run_server.sh
check "bash -n build_all.sh"  bash -n build_all.sh

if command -v shellcheck > /dev/null 2>&1; then
    check "shellcheck wizard.sh"     shellcheck wizard.sh
    check "shellcheck run_server.sh" shellcheck run_server.sh
    check "shellcheck build_all.sh"  shellcheck build_all.sh
else
    echo "  [SKIP] shellcheck not installed"
fi

echo ""
echo "================================================"
printf "  %d passed  %d failed\n" "$PASS" "$FAIL"
if [ "${#FAILURES[@]}" -gt 0 ]; then
    echo ""
    echo "  Failed checks:"
    for f in "${FAILURES[@]}"; do
        echo "    - $f"
    done
fi
echo "================================================"
[ "$FAIL" -eq 0 ]
