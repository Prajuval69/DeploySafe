"""Stable CLI exit codes and validation errors."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

INVALID = 2
PREFLIGHT = 3
DEPLOY_FAILED = 4
RECOVERY_FAILED = 5
BUSY = 6


class DeployError(Exception):
    def __init__(self, code, message, exit_code=PREFLIGHT):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True)
class Result:
    exit_code: int
    code: str
    message: str
    run_id: str | None = None
    active_version: str | None = None
    state: str | None = None

    def as_dict(self):
        return asdict(self)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
