#!/usr/bin/env bash
# Run every package test suite plus the docker-compose e2e suite.
# Each suite uses its own .venv. Reports all failures; exits non-zero if any fail.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
fail=0

run() {
    local name="$1" dir="$2"
    echo "=== $name ==="
    if (cd "$ROOT/$dir" && .venv/bin/pytest -q); then
        echo "--- $name OK"
    else
        echo "--- $name FAILED"
        fail=1
    fi
}

run contract contract
run agent    agent
run client   client
run manager  manager
run e2e      e2e

echo
if [ "$fail" -eq 0 ]; then echo "ALL SUITES PASSED"; else echo "SOME SUITES FAILED"; fi
exit "$fail"
