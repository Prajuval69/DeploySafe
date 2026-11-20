import hashlib
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploysafe.config import REPO, Policy, load_policy, parse_policy, prepare_workspace, saved_policy
from deploysafe.models import DeployError
from deploysafe.releases import APP_HASH, artifact, bundled, fixture_path, stage
from deploysafe.security import atomic_json, inside, read_json, release_id

from support import temporary_directory, workspace


class ConfigTests(unittest.TestCase):
    def test_all_supplied_policies_are_valid_and_distinct(self):
        defaults, strict, scenario = [load_policy(REPO, f"config/{name}.json")
                                      for name in ("default", "strict", "scenario_policy")]
        self.assertEqual(defaults.workspace, REPO / ".deploysafe_workspace")
        self.assertLess(strict.health_timeout_seconds, defaults.health_timeout_seconds)
        self.assertLess(scenario.health_timeout_seconds, defaults.health_timeout_seconds)
        self.assertNotEqual(scenario, strict)

    def test_rejects_nonloopback_and_capability_toggles(self):
        for data in ({"bind": "0.0.0.0"}, {"allow_shell_commands": True},
                     {"allow_remote_hosts": True}, {"read_only_dashboard": False}):
            with self.subTest(data=data), self.assertRaisesRegex(DeployError, "Policy field"):
                parse_policy(REPO, data)

    def test_rejects_unknown_and_url_fields(self):
        for data in ({"health_url": "http://example.invalid"}, {"health_endpoint": "//example.invalid"},
                     {"allowed_release_root": "tools"}):
            with self.subTest(data=data), self.assertRaises(DeployError):
                parse_policy(REPO, data)

    def test_rejects_ports_outside_range_and_bools(self):
        for port in (899, 9000, True, "8765", 8765.0):
            with self.subTest(port=port), self.assertRaises(DeployError):
                parse_policy(REPO, {"active_port": port})

    def test_rejects_collision_with_dashboard(self):
        with self.assertRaises(DeployError) as raised:
            parse_policy(REPO, {"candidate_port": 8787})
        self.assertEqual(raised.exception.code, "PORT_COLLISION")

    def test_enforces_retry_timeout_and_retention_bounds(self):
        for key, value in (("health_attempts", 16), ("health_timeout_seconds", .01),
                           ("health_timeout_seconds", float("nan")), ("backoff_seconds", 1.01),
                           ("retention_days", 0), ("retention_days", 366)):
            with self.subTest(key=key, value=value), self.assertRaises(DeployError):
                parse_policy(REPO, {key: value})

    def test_rejects_repo_root_parent_and_source_directories(self):
        for value in (".", "..", str(REPO.parent), "fixtures/local", "config", "con"):
            with self.subTest(value=value), self.assertRaises(DeployError):
                parse_policy(REPO, {"workspace": value})

    def test_refuses_to_claim_nonempty_workspace(self):
        with temporary_directory() as directory:
            personal = directory / "personal.txt"
            personal.write_text("preserve this", encoding="utf-8")
            with self.assertRaisesRegex(DeployError, "empty directory"):
                prepare_workspace(Policy(REPO, directory), create=True)
            self.assertEqual(personal.read_text(encoding="utf-8"), "preserve this")

    def test_saved_policy_round_trips_and_detects_moved_marker(self):
        with workspace() as policy:
            self.assertEqual(saved_policy(policy), policy)
            marker = policy.workspace / ".deploysafe-owner.json"
            atomic_json(policy.workspace, marker, {"application": "DeploySafe", "path": "another directory"})
            with self.assertRaisesRegex(DeployError, "ownership marker"):
                saved_policy(policy)

    def test_rejects_duplicate_json_keys(self):
        with temporary_directory() as directory:
            path = directory / "bad.json"
            path.write_bytes(b'{"active_port":8765,"active_port":8766}')
            with self.assertRaises(DeployError) as raised:
                read_json(directory, path)
            self.assertEqual(raised.exception.code, "INVALID_JSON")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def local_copy(self):
        directory = self.enterContext(temporary_directory())
        policy = Policy(directory, directory / ".deploysafe_workspace")
        source = policy.release_root / "1.0.0"
        shutil.copytree(self.policy.release_root / "1.0.0", source)
        return policy, source

    def edit_manifest(self, source, **changes):
        path = source / "release.json"
        manifest = json.loads(path.read_bytes())
        path.write_text(json.dumps({**manifest, **changes}), encoding="utf-8")

    def test_good_release_carries_verified_digest(self):
        candidate = bundled(self.policy, "1.0.0")
        self.assertEqual(hashlib.sha256(candidate.files["app.py"]).hexdigest(), APP_HASH)
        self.assertEqual(candidate.version, "1.0.0")
        self.assertEqual(len(candidate.checksum), 64)

    def test_supplied_checksum_fault_rejected(self):
        with self.assertRaises(DeployError) as raised:
            bundled(self.policy, "1.5.0-tampered")
        self.assertEqual(raised.exception.code, "CHECKSUM_MISMATCH")

    def test_supplied_bad_bind_rejected(self):
        with self.assertRaises(DeployError) as raised:
            bundled(self.policy, "1.3.0-badconfig")
        self.assertEqual(raised.exception.code, "PREFLIGHT_INVALID_BIND")

    def test_external_nested_and_traversal_paths_rejected(self):
        for path in ("tools", "fixtures/demo_releases/../demo_releases/1.0.0",
                     "fixtures/demo_releases/1.0.0/app.py", str(REPO.parent / "1.0.0")):
            with self.subTest(path=path), self.assertRaises(DeployError):
                fixture_path(self.policy, path)

    def test_extra_fixture_directory_is_outside_bundled_inventory(self):
        policy, source = self.local_copy()
        renamed = source.with_name("9.0.0")
        source.rename(renamed)
        with self.assertRaises(DeployError) as raised:
            fixture_path(policy, str(renamed))
        self.assertEqual(raised.exception.code, "UNBUNDLED_RELEASE")

    def test_hostile_version_strings_rejected(self):
        for value in ("1.0", "1.0.0-x/y", "1.0.0\x00", "1.0.0-ABC", "1.0.0\n", 42, "9" * 64 + ".0.0"):
            with self.subTest(value=value), self.assertRaises(DeployError):
                release_id(value)

    def test_invalid_manifest_schema_and_filename_rejected(self):
        policy, source = self.local_copy()
        for updates in ({"schema_version": True}, {"entrypoint": "../app.py"},
                        {"config_file": "local.json"}, {"description": "\x1b[31m"}, {"unknown": True}):
            with self.subTest(updates=updates):
                shutil.copyfile(self.policy.release_root / "1.0.0/release.json", source / "release.json")
                self.edit_manifest(source, **updates)
                with self.assertRaises(DeployError) as raised:
                    bundled(policy, "1.0.0")
                self.assertEqual(raised.exception.code, "INVALID_MANIFEST")

    def test_manifest_must_name_the_directory_version(self):
        policy, source = self.local_copy()
        self.edit_manifest(source, release_id="9.9.9")
        with self.assertRaises(DeployError) as raised:
            bundled(policy, "1.0.0")
        self.assertEqual(raised.exception.code, "RELEASE_ID_MISMATCH")

    def test_recomputed_manifest_does_not_authorize_new_python(self):
        policy, source = self.local_copy()
        contents = b'raise RuntimeError("should never start")'
        (source / "app.py").write_bytes(contents)
        hashes = json.loads((source / "release.json").read_bytes())["sha256"]
        hashes["app.py"] = hashlib.sha256(contents).hexdigest()
        self.edit_manifest(source, sha256=hashes)
        with self.assertRaises(DeployError) as raised:
            bundled(policy, "1.0.0")
        self.assertEqual(raised.exception.code, "UNTRUSTED_CODE")

    def test_rejects_oversized_before_hashing(self):
        policy, source = self.local_copy()
        (source / "app.py").write_bytes(b"x" * (policy.max_release_file_bytes + 1))
        with self.assertRaises(DeployError) as raised:
            bundled(policy, "1.0.0")
        self.assertEqual(raised.exception.code, "FILE_TOO_LARGE")

    def test_symlink_and_reparse_component_refused(self):
        policy, source = self.local_copy()
        prior = Path.lstat
        for info in (SimpleNamespace(st_mode=0o120777), SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)):
            with self.subTest(info=info), patch.object(Path, "lstat", lambda path: info if path == source else prior(path)):
                with self.assertRaises(DeployError) as raised:
                    inside(policy.repo, source / "app.py")
                self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_actual_hard_link_rejected(self):
        policy, source = self.local_copy()
        backup = source / "backup.py"
        app = source / "app.py"
        app.rename(backup)
        try:
            os.link(backup, app)
        except OSError as exc:
            self.skipTest(f"filesystem lacks ordinary-user hard links: {exc}")
        with self.assertRaises(DeployError) as raised:
            bundled(policy, "1.0.0")
        self.assertEqual(raised.exception.code, "UNSAFE_FILE")

    def test_stage_is_exact_copy_and_archive_tamper_detected(self):
        with workspace() as policy:
            original = bundled(policy, "1.0.0")
            run = "a" * 32
            staged = stage(policy, original, run)
            self.assertEqual(staged.checksum, original.checksum)
            self.assertEqual(set(path.name for path in staged.path.iterdir()), set(original.files))
            self.assertFalse((policy.workspace / "staging" / run).exists())
            path = staged.path / "release.json"
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaises(DeployError) as raised:
                artifact(policy, run, original.version, original.checksum)
            self.assertEqual(raised.exception.code, "ARTIFACT_CHANGED")
