#!/usr/bin/env bash
set -euo pipefail
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --json ) ]]; then
    printf '%s\n' 'Usage: bash scripts/linux/status.sh [--json]' >&2
    exit 2
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
exec python3 -B -m deploysafe status "$@"
