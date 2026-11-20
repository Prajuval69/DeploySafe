#!/usr/bin/env bash
set -euo pipefail
if [[ $# -gt 1 ]]; then
    printf '%s\n' 'Usage: bash scripts/linux/demo.sh [SCENARIO]' >&2
    exit 2
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
if [[ $# -eq 1 ]]; then
    exec python3 -B -m deploysafe demo --scenario "$1"
fi
exec python3 -B -m deploysafe demo --all
