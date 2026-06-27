#!/usr/bin/env bash
set -e

echo "--- format check ---"
if ! ruff format --check . 2>&1; then
    echo ""
    echo "Fix:  ruff format <file>   (or: ruff format . to fix all)"
    exit 1
fi

echo "--- docker build ---"
docker build -f tests/Dockerfile -t context-bridge-test .

echo "--- docker test ---"
docker run --rm context-bridge-test
