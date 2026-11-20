"""Policy and marked workspace directories."""

from dataclasses import dataclass, replace
import math
from pathlib import Path
import re
import sys

from .models import DeployError, INVALID
from .security import (LOOPBACK, WORKSPACE_PART, atomic_json, inside, port_number,
                       read_json, regular_file, json_bytes, write_new)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = ".deploysafe_workspace"
RELEASE_ROOT = Path("fixtures/demo_releases")
LAYOUT = ("staging", "releases", "logs", "reports", "db", "locks", "runtime", "demos")
MARKER = ".deploysafe-owner.json"
RESERVED = {".git", "config", "deploysafe", "docs", "fixtures", "prompts",
            "scripts", "templates", "tests", "tools"}


def workspace_path(repo, value):
    if type(value) is not str or not value or len(value) > 200:
        raise DeployError("INVALID_WORKSPACE", "Workspace requires a project-contained path.", INVALID)
    path = inside(repo, Path(value), missing=True)
    parts = path.relative_to(repo).parts
    windows_reserved = re.compile(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])", re.I)
    if (not parts or parts[0].lower() in RESERVED
            or any(not WORKSPACE_PART.fullmatch(part) or part.endswith(".")
                   or windows_reserved.fullmatch(part.split(".")[0]) for part in parts)):
        raise DeployError("INVALID_WORKSPACE", "Workspace path uses a reserved name.", INVALID)
    return path


@dataclass(frozen=True)
class Policy:
    repo: Path
    workspace: Path
    active_port: int = 8765
    candidate_port: int = 8766
    dashboard_port: int = 8787
    health_timeout_seconds: float = 0.5
    health_attempts: int = 6
    backoff_seconds: float = 0.15
    max_release_file_bytes: int = 100000
    retain_release_count: int = 5
    retention_days: int = 1
    logging_level: str = "INFO"

    @property
    def release_root(self):
        return self.repo / RELEASE_ROOT

    def with_workspace(self, path):
        return replace(self, workspace=workspace_path(self.repo, str(path)))

    def as_dict(self):
        return {**FIXED, "workspace": self.workspace.relative_to(self.repo).as_posix(),
                **{key: getattr(self, key) for key in RANGES},
                "logging_level": self.logging_level}


FIXED = {"schema_version": 1, "bind": LOOPBACK, "health_endpoint": "/health",
         "version_endpoint": "/version", "allowed_release_root": RELEASE_ROOT.as_posix(),
         "read_only_dashboard": True, "allow_remote_hosts": False, "allow_shell_commands": False}

RANGES = {"active_port": (int, 8000, 8999), "candidate_port": (int, 8000, 8999),
          "dashboard_port": (int, 8000, 8999), "health_timeout_seconds": (float, 0.1, 5),
          "health_attempts": (int, 1, 15), "backoff_seconds": (float, 0, 1),
          "max_release_file_bytes": (int, 1024, 1000000), "retain_release_count": (int, 1, 100),
          "retention_days": (int, 1, 365)}


def parse_policy(repo, data):
    if type(data) is not dict or set(data) - (set(FIXED) | set(RANGES) | {"workspace", "logging_level"}):
        raise DeployError("INVALID_POLICY", "Unknown policy fields or invalid policy object.", INVALID)
    for key, required in FIXED.items():
        value = data.get(key, required)
        if type(value) is not type(required) or value != required:
            raise DeployError("INVALID_POLICY", f"Policy field {key} must be {required!r}.", INVALID)
    values = {}
    for key, (kind, low, high) in RANGES.items():
        if key not in data:
            continue
        value = data[key]
        types = (int, float) if kind is float else (int,)
        if type(value) not in types or not low <= value <= high or not math.isfinite(value):
            raise DeployError("INVALID_POLICY", f"Policy field {key} must be between {low} and {high}.", INVALID)
        values[key] = value
    level = data.get("logging_level", "INFO")
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise DeployError("INVALID_POLICY", "Invalid logging level.", INVALID)
    workspace = workspace_path(repo, data.get("workspace", DEFAULT_WORKSPACE))
    policy = Policy(repo, workspace, logging_level=level, **values)
    if len({policy.active_port, policy.candidate_port, policy.dashboard_port}) != 3:
        raise DeployError("PORT_COLLISION", "The three configured ports must be distinct.", INVALID)
    return policy


def load_policy(repo=REPO, path="config/default.json"):
    try:
        return parse_policy(repo, read_json(repo, path))
    except OSError as exc:
        raise DeployError("INVALID_POLICY", "Cannot read the project policy file.", INVALID) from exc


def require_python():
    if sys.version_info < (3, 11):
        raise DeployError("PYTHON_VERSION", "Python 3.11 or newer is required.", INVALID)


def prepare_workspace(policy, *, create=False):
    root = workspace_path(policy.repo, str(policy.workspace))
    marker = root / MARKER
    identity = {"application": "DeploySafe", "schema_version": 1, "path": str(root)}
    if create:
        if not root.parent.is_dir():
            raise DeployError("INVALID_WORKSPACE", "Workspace parent must already exist.", INVALID)
        root.mkdir(mode=0o700, exist_ok=True)
    if not root.is_dir():
        raise DeployError("WORKSPACE_MISSING", "Run deploysafe init to create this workspace.", INVALID)
    if not marker.exists() and create:
        if any(root.iterdir()):
            raise DeployError("UNOWNED_WORKSPACE", "Choose an empty directory for the workspace.", INVALID)
        try:
            write_new(root, marker, json_bytes(identity))
        except FileExistsError:
            pass
    if not marker.exists() or read_json(root, marker) != identity:
        raise DeployError("UNOWNED_WORKSPACE", "Workspace ownership marker is absent or invalid.", INVALID)
    for folder in LAYOUT:
        path = inside(root, root / folder, missing=create)
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        if not path.is_dir():
            raise DeployError("WORKSPACE_LAYOUT", f"Workspace directory {folder} is unavailable.", INVALID)
    return root


def saved_policy(policy):
    path = regular_file(policy.workspace, "policy.json", missing=True) if policy.workspace.exists() else None
    if path is None or not path.exists():
        return None
    prepare_workspace(policy)
    saved = parse_policy(policy.repo, read_json(policy.workspace, path))
    if saved.workspace != policy.workspace:
        raise DeployError("POLICY_WORKSPACE", "Saved policy refers to a different workspace.", INVALID)
    return saved


def save_policy(policy):
    atomic_json(policy.workspace, "policy.json", policy.as_dict())


def validate_dashboard_port(policy, value):
    selected = port_number(value)
    if selected in (policy.active_port, policy.candidate_port):
        raise DeployError("PORT_COLLISION", "Dashboard must use a separate port.", INVALID)
    return selected
