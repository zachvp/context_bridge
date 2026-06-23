#!/usr/bin/env bash
set -e

docker build -f tests/Dockerfile -t context-bridge-test .
docker run --rm context-bridge-test
