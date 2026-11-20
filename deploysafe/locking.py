"""A kernel-backed exclusive lock plus an atomic, inspectable owner token."""

import errno
import os
import time
import uuid

from .models import BUSY, DeployError, utc_now
from .security import json_bytes, read_json, regular_file, write_new

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _lock(stream, acquire):
    if os.name == "nt":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


class WorkspaceLock:
    def __init__(self, root, name, timeout=0):
        self.root = root
        self.guard = root / "locks" / f"{name}.guard"
        self.owner = root / "locks" / f"{name}.json"
        self.timeout = timeout
        self.token = uuid.uuid4().hex

    def __enter__(self):
        guard = regular_file(self.root, self.guard, missing=True)
        self.stream = guard.open("a+b")
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    _lock(self.stream, True)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise DeployError("LOCK_BUSY", "Another DeploySafe operation owns this workspace.", BUSY) from exc
                    time.sleep(0.025)
            # The kernel releases the guard on process exit. Only its new owner
            # may remove an old informational token, without consulting its PID.
            regular_file(self.root, self.owner, missing=True).unlink(missing_ok=True)
            write_new(self.root, self.owner, json_bytes(
                {"token": self.token, "pid": os.getpid(), "started_at": utc_now()}))
            return self
        except BaseException:
            self.stream.close()
            raise

    def __exit__(self, *_):
        try:
            if self.owner.exists() and read_json(self.root, self.owner).get("token") == self.token:
                self.owner.unlink()
        finally:
            try:
                _lock(self.stream, False)
            finally:
                self.stream.close()


def held(root, name):
    guard = regular_file(root, root / "locks" / f"{name}.guard", missing=True)
    try:
        stream = guard.open("rb+")
    except FileNotFoundError:
        return False
    with stream:
        try:
            _lock(stream, True)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return True
            raise
        _lock(stream, False)
    return False
