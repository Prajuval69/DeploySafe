# Linux Bash scripts

`scripts/linux/deploy.sh` and `demo.sh` must use `set -euo pipefail`, quote every path, derive repository root from script location, use `python3 -m deploysafe`, and propagate exit codes. No sudo, systemctl writes, OS service changes, cron edits, firewall changes, package-manager installation or process-group mass kills. Only operate within sandbox. Test actual Linux shell syntax (`bash -n`) and behavior where available.
