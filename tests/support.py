from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile

from deploysafe.config import DEFAULT_WORKSPACE, REPO, Policy, prepare_workspace, save_policy


@contextmanager
def temporary_directory():
    parent = prepare_workspace(Policy(REPO, REPO / DEFAULT_WORKSPACE), create=True) / "demos"
    with tempfile.TemporaryDirectory(prefix="test-", dir=parent) as name:
        yield Path(name)


@contextmanager
def workspace(ports=(8915, 8916, 8917)):
    with temporary_directory() as directory:
        policy = replace(Policy(REPO, directory), active_port=ports[0], candidate_port=ports[1],
                         dashboard_port=ports[2], health_timeout_seconds=0.2, health_attempts=3,
                         backoff_seconds=0.04)
        prepare_workspace(policy, create=True)
        save_policy(policy)
        yield policy
