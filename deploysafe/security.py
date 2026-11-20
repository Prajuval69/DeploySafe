"""Bounded data readers and project-contained path primitives."""

import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .models import DeployError, INVALID

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9-]+)?", re.ASCII)
TOKEN = re.compile(r"[0-9a-f]{32}")
WORKSPACE_PART = re.compile(r"[.A-Za-z0-9_-]+", re.ASCII)
PORT_RANGE = range(8000, 9000)
LOOPBACK = "127.0.0.1"


def release_id(value):
    if type(value) is not str or len(value) > 64 or not VERSION.fullmatch(value):
        raise DeployError("INVALID_RELEASE_ID", "Expected a version such as 1.1.0.", INVALID)
    return value


def token(value):
    if type(value) is not str or not TOKEN.fullmatch(value):
        raise DeployError("INVALID_TOKEN", "Invalid operation identifier.", INVALID)
    return value


def port_number(value):
    if type(value) is not int or value not in PORT_RANGE:
        raise DeployError("INVALID_PORT", "Port must be an integer from 8000 through 8999.", INVALID)
    return value


def inside(root, path, *, missing=False):
    """Check every component, including Windows reparse points and junctions."""
    root, path = Path(root), Path(path)
    if not path.is_absolute():
        path = root / path
    if ".." in path.parts or not path.is_relative_to(root):
        raise DeployError("UNSAFE_PATH", "Path must stay within its permitted root.", INVALID)
    relative = path.relative_to(root)
    if os.name == "nt" and any(":" in part or part.endswith((".", " ")) for part in relative.parts):
        raise DeployError("UNSAFE_PATH", "Windows aliases and alternate streams are forbidden.", INVALID)
    for component in (root, *(root / p for p in reversed(relative.parents)
                              if p != Path(".")), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            if not missing:
                raise
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DeployError("UNSAFE_PATH", "Links and reparse points are forbidden.", INVALID)
    return path


def regular_file(root, path, *, missing=False):
    path = inside(root, path, missing=missing)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return path
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DeployError("UNSAFE_FILE", "Expected a regular, single-link file.", INVALID)
    return path


def read_bytes(root, path, limit):
    path = regular_file(root, path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise DeployError("FILE_TOO_LARGE", f"{path.name} exceeds {limit} bytes.")
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("Non-finite JSON number")


def parse_json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def read_json(root, path, limit=16384):
    try:
        return parse_json(read_bytes(root, path, limit))
    except (ValueError, RecursionError) as exc:
        raise DeployError("INVALID_JSON", "File must contain valid UTF-8 JSON.") from exc


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_new(root, path, data):
    path = regular_file(root, path, missing=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_json(root, path, value):
    path = regular_file(root, path, missing=True)
    temp = path.with_name(f".{uuid.uuid4().hex}.tmp")
    write_new(root, temp, json_bytes(value))
    try:
        for attempt in range(6):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.02)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def printable(value, length=240):
    return "".join(char if char.isprintable() else " " for char in str(value))[:length]
