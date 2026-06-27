#!/usr/bin/env bash
set -e

echo "--- format check ---"
ruff format --check .

echo "--- docker build ---"
docker build -f tests/Dockerfile -t context-bridge-test .

echo "--- docker test ---"
docker run --rm context-bridge-test
