"""Verify bundled artifacts before staging and verify the staged copy again."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .models import DeployError, INVALID
from .security import (LOOPBACK, inside, parse_json, read_bytes, release_id, sync_directory,
                       token, write_new)

APP_HASH = "918c22247aa993e697f7235f8271a1d53adc01905764908ef33a3cd4556533e4"
FILES = ("app.py", "app_config.json", "release.json")
MODES = ("healthy", "health_503", "slow_health", "crash")
HASH = re.compile(r"[a-f0-9]{64}")
MANIFEST_KEYS = {"schema_version", "release_id", "entrypoint", "config_file", "sha256", "description"}
BUNDLED_IDS = ("1.0.0", "1.1.0", "1.2.0-healthfail", "1.3.0-badconfig", "1.4.0-timeout",
               "1.5.0-tampered", "1.6.0-crash", "1.7.0-wrongversion")


def sha(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Release:
    version: str
    path: Path
    checksum: str
    files: dict


def fixture_path(policy, value):
    if type(value) is not str or not value or len(value) > 1024:
        raise DeployError("INVALID_RELEASE_PATH", "Supply a bundled release path or ID.", INVALID)
    path = Path(value)
    if len(path.parts) == 1 and not path.is_absolute():
        path = policy.release_root / release_id(value)
    path = inside(policy.repo, path, missing=True)
    if path.parent != policy.release_root:
        raise DeployError("INVALID_RELEASE_PATH", "Release must be directly under fixtures/demo_releases.", INVALID)
    if release_id(path.name) not in BUNDLED_IDS:
        raise DeployError("UNBUNDLED_RELEASE", "Release is outside the bundled fixture inventory.", INVALID)
    return path


def _object(data, label):
    try:
        obj = parse_json(data)
    except (ValueError, RecursionError) as exc:
        raise DeployError("INVALID_JSON", f"{label} must be valid UTF-8 JSON.") from exc
    if type(obj) is not dict:
        raise DeployError("INVALID_SCHEMA", f"{label} must be a JSON object.")
    return obj


def _manifest(data, version):
    manifest = _object(data, "Release manifest")
    if (set(manifest) != MANIFEST_KEYS or type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != 1
            or manifest["entrypoint"] != "app.py" or manifest["config_file"] != "app_config.json"):
        raise DeployError("INVALID_MANIFEST", "Manifest shape, schema version or fixed filenames are invalid.")
    if release_id(manifest["release_id"]) != version:
        raise DeployError("RELEASE_ID_MISMATCH", "Manifest version differs from the requested release.")
    details = manifest["description"]
    hashes = manifest["sha256"]
    if (type(details) is not str or not 1 <= len(details) <= 240 or not details.isprintable()
            or type(hashes) is not dict or set(hashes) != set(FILES[:2])
            or any(type(value) is not str or not HASH.fullmatch(value) for value in hashes.values())):
        raise DeployError("INVALID_MANIFEST", "Description or SHA256 mapping is invalid.")
    return hashes


def _app_config(data, version):
    config = _object(data, "App configuration")
    required = {"bind", "version", "mode"}
    if required - set(config) or set(config) - (required | {"reported_version"}):
        raise DeployError("INVALID_APP_CONFIG", "App configuration has missing or unsupported fields.")
    if config["bind"] != LOOPBACK:
        raise DeployError("PREFLIGHT_INVALID_BIND", "Release must bind to 127.0.0.1.")
    if config["version"] != version or config["mode"] not in MODES:
        raise DeployError("INVALID_APP_CONFIG", "App version or fault mode is invalid.")
    if "reported_version" in config:
        release_id(config["reported_version"])


def inspect(policy, path, version):
    version = release_id(version)
    try:
        path = inside(policy.repo, path)
        files = {"release.json": read_bytes(policy.repo, path / "release.json",
                                            min(16384, policy.max_release_file_bytes))}
        expected = _manifest(files["release.json"], version)
        for name in FILES[:2]:
            data = read_bytes(policy.repo, path / name, policy.max_release_file_bytes)
            if sha(data) != expected[name]:
                raise DeployError("CHECKSUM_MISMATCH", f"SHA256 mismatch for {name}.")
            files[name] = data
        if expected["app.py"] != APP_HASH:
            raise DeployError("UNTRUSTED_CODE", "App code does not match the pinned bundled executable.")
        _app_config(files["app_config.json"], version)
    except OSError as exc:
        raise DeployError("RELEASE_FILES", "Could not read the required release files.") from exc
    checksum = sha(b"".join(bytes.fromhex(sha(files[name])) for name in FILES))
    return Release(version, path, checksum, files)


def bundled(policy, value):
    path = fixture_path(policy, value)
    return inspect(policy, path, path.name)


def artifact(policy, run_id, version, checksum):
    path = policy.workspace / "releases" / token(run_id)
    release = inspect(policy, path, version)
    if release.checksum != checksum:
        raise DeployError("ARTIFACT_CHANGED", "Archived release differs from the audited snapshot.")
    return release


def stage(policy, release, run_id):
    root = policy.workspace
    temporary = inside(root, root / "staging" / token(run_id), missing=True)
    final = inside(root, root / "releases" / run_id, missing=True)
    temporary.mkdir(mode=0o700)
    try:
        for name in FILES:
            write_new(root, temporary / name, release.files[name])
        staged = inspect(policy, temporary, release.version)
        if staged.checksum != release.checksum:
            raise DeployError("STAGE_CHANGED", "Staged copy differs from the verified source.")
        if final.exists():
            raise DeployError("ARTIFACT_EXISTS", "Artifact directory already exists.")
        sync_directory(temporary)
        temporary.rename(final)
        sync_directory(final.parent)
        sync_directory(temporary.parent)
    finally:
        if temporary.exists():
            remove_artifact(policy, temporary)
    return artifact(policy, run_id, release.version, release.checksum)


def remove_artifact(policy, path):
    path = inside(policy.workspace, path, missing=True)
    if path.parent not in (policy.workspace / "staging", policy.workspace / "releases"):
        raise DeployError("UNSAFE_PATH", "Cleanup can only remove a specific workspace artifact.")
    token(path.name)
    if path.is_dir():
        for name in FILES:
            item = inside(policy.workspace, path / name, missing=True)
            item.unlink(missing_ok=True)
        path.rmdir()
