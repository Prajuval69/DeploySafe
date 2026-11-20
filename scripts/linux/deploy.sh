#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
    printf '%s\n' 'Usage: bash scripts/linux/deploy.sh RELEASE' >&2
    exit 2
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
exec python3 -B -m deploysafe deploy --release "$1"
