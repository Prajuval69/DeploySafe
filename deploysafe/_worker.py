"""Isolated interpreter entry point for DeploySafe-owned workers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploysafe.config import REPO, Policy, saved_policy, workspace_path
from deploysafe.processes import guard
from deploysafe.supervisor import serve


def main():
    role, workspace, *args = sys.argv[1:]
    default = Policy(REPO, workspace_path(REPO, workspace))
    policy = saved_policy(default)
    if policy is None:
        raise SystemExit("Workspace policy unavailable.")
    if role == "serve" and not args:
        serve(policy)
    elif role == "guard" and len(args) == 5:
        run, version, checksum, port, instance = args
        guard(policy, run, version, checksum, int(port), instance)
    else:
        raise SystemExit("Invalid DeploySafe worker invocation.")


if __name__ == "__main__":
    main()
