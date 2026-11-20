import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploysafe import health, releases
from deploysafe.locking import WorkspaceLock
from deploysafe.manager import Manager, POINTER, pointer
from deploysafe.models import DeployError
from deploysafe.security import atomic_json, read_json
from deploysafe.storage import SCHEMA_VERSION, Store

from support import workspace


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.policy = self.enterContext(workspace())
        self.store = self.enterContext(Store(self.policy.workspace))
        self.run = "a" * 32
        self.store.begin(self.run, None, "1.0.0", "deploy")

    def _activate(self):
        self.store.staged(self.run, "1.0.0", "f" * 64, "bundled fixture")
        for stage in ("PREFLIGHT_OK", "STARTING", "HEALTHY"):
            self.store.transition(self.run, stage, stage, stage)
        self.store.port(self.run, 8765)
        self.store.activate(self.run, None)

    def test_sets_wal_foreign_keys_and_schema_version(self):
        for pragma, expected in (("journal_mode", "wal"), ("foreign_keys", 1), ("user_version", SCHEMA_VERSION)):
            self.assertEqual(self.store.db.execute(f"PRAGMA {pragma}").fetchone()[0], expected)

    def test_foreign_key_prevents_unowned_health_event(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.check("f" * 32, "1.0.0", "candidate", 1, health.Check("/health", "OK", "", 1))
        self.assertEqual(self.store.history(20)["health_checks"], [])

    def test_audit_events_are_immutable(self):
        for sql in ("UPDATE deployment_events SET code='changed'", "DELETE FROM deployment_events",
                    """INSERT OR REPLACE INTO deployment_events
                       SELECT id, run_id, release_id, timestamp, stage, outcome, 'changed', message
                       FROM deployment_events"""):
            with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                with self.store.db:
                    self.store.db.execute(sql)
        self.assertEqual(self.store.history(20)["events"][0]["code"], "REQUESTED")

    def test_pointer_history_and_health_evidence_are_immutable(self):
        self._activate()
        self.store.check(self.run, "1.0.0", "post_commit", 1, health.Check("/health", "OK", "", 1))
        for table in ("active_history", "health_checks", "releases"):
            with self.subTest(table=table), self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                with self.store.db:
                    self.store.db.execute(f"DELETE FROM {table}")

    def test_activation_and_history_survive_reopen_and_readonly_cannot_write(self):
        self._activate()
        with Store(self.policy.workspace, readonly=True) as reader:
            self.assertEqual(pointer(reader.active()), {"release_id": "1.0.0", "port": 8765, "run_id": self.run})
            self.assertEqual(reader.history(8)["pointer_changes"][0]["new_run"], self.run)
            with self.assertRaises(sqlite3.OperationalError):
                reader.db.execute("INSERT INTO app_meta VALUES ('unexpected', '1')")

    def test_illegal_transition_changes_neither_projection_nor_audit(self):
        with self.assertRaisesRegex(DeployError, "Cannot move"):
            self.store.transition(self.run, "ACTIVE", "INJECTED", "invalid")
        result = self.store.history(20)
        self.assertEqual(result["deployments"][0]["state"], "PENDING")
        self.assertEqual([event["code"] for event in result["events"]], ["REQUESTED"])

    def test_active_pointer_and_transition_commit_together(self):
        self.store.staged(self.run, "1.0.0", "b" * 64, "bundled fixture")
        for stage in ("PREFLIGHT_OK", "STARTING", "HEALTHY"):
            self.store.transition(self.run, stage, stage, stage)
        self.store.port(self.run, 8765)
        with patch.object(self.store, "_pointer_change", side_effect=sqlite3.OperationalError("fault")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.activate(self.run, None)
        result = self.store.history(20)
        self.assertEqual(result["deployments"][0]["state"], "HEALTHY")
        self.assertNotIn("ACTIVE", [event["stage"] for event in result["events"]])
        self.assertIsNone(self.store.active())

    def test_schema_migration_keeps_existing_events_and_enables_replacement_guard(self):
        with self.store.db:
            self.store.db.execute("PRAGMA user_version=1")
            self.store.db.execute("DROP TRIGGER deployment_events_no_replace")
        with Store(self.policy.workspace) as upgraded:
            self.assertEqual(upgraded.db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(upgraded.history(10)["events"][0]["code"], "REQUESTED")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                upgraded.db.execute("INSERT OR REPLACE INTO deployment_events SELECT * FROM deployment_events")


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.policy = self.enterContext(workspace())
        self.store = self.enterContext(Store(self.policy.workspace))
        self.manager = Manager(self.policy, self.store)
        self.stopped = []
        self.launched = []
        self.enterContext(patch("deploysafe.manager.processes.check_port"))
        self.enterContext(patch("deploysafe.manager.processes.wait_ready"))
        self.enterContext(patch("deploysafe.manager.processes.launch", side_effect=self.launch))
        self.enterContext(patch("deploysafe.manager.health.verify", side_effect=self.verify))
        self.addCleanup(self.manager.close)
        self.manager.reconcile()
        self.faults = {}

    def launch(self, policy, run, version, digest, port, instance):
        child = SimpleNamespace(run_id=run, version=version, port=port, instance=instance, alive=True)

        def stop():
            self.stopped.append(version)
            child.alive = False

        child.stop = stop
        self.launched.append(child)
        return child

    def verify(self, _policy, child, record, phase, **_options):
        code = self.faults.get((child.version, phase), "OK")
        check = health.Check("/health", code, f"{phase} returned {code}", 1)
        record(phase, 1, check)
        return check

    def test_success_has_ordered_state_transitions_and_postcheck(self):
        result = self.manager.deploy("1.0.0")
        events = reversed(self.store.history(20)["events"])
        states = [row["stage"] for row in events if row["run_id"] == result.run_id and row["stage"] != "PROCESS"]
        self.assertEqual(states, ["PENDING", "STAGED", "PREFLIGHT_OK", "STARTING", "HEALTHY", "ACTIVE"])
        self.assertEqual(read_json(self.policy.workspace, POINTER), pointer(self.store.active()))
        self.assertIn("post_commit", {row["phase"] for row in self.store.history(20)["health_checks"]})

    def test_upgrade_preserves_prior_until_verified_and_switches_port(self):
        self.manager.deploy("1.0.0")
        original = self.manager.active_child
        verified_while_live = []
        original_check = self.manager._check

        def check(run, child, phase, **extras):
            if phase == "post_commit":
                verified_while_live.append(original.alive)
            return original_check(run, child, phase, **extras)

        with patch.object(self.manager, "_check", side_effect=check):
            result = self.manager.deploy("1.1.0")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(verified_while_live, [True])
        self.assertEqual(self.stopped, ["1.0.0"])
        self.assertEqual(pointer(self.store.active())["port"], self.policy.candidate_port)

    def test_preflight_rejects_without_launch_or_staging(self):
        result = self.manager.deploy("1.3.0-badconfig")
        self.assertEqual((result.exit_code, result.state), (3, "FAILED"))
        self.assertEqual(self.launched, [])
        self.assertEqual(list((self.policy.workspace / "releases").iterdir()), [])
        check = self.store.one("SELECT code FROM preflight_checks WHERE run_id=?", (result.run_id,))
        self.assertEqual(check["code"], "PREFLIGHT_INVALID_BIND")

    def test_bad_candidate_preserves_live_previous_and_records_recovery(self):
        baseline = self.manager.deploy("1.0.0")
        prior = self.manager.active_child
        self.faults[("1.2.0-healthfail", "candidate")] = "HTTP_503"
        result = self.manager.deploy("1.2.0-healthfail")
        self.assertEqual((result.exit_code, result.state), (4, "ROLLED_BACK"))
        self.assertEqual(self.store.active()["run_id"], baseline.run_id)
        self.assertIs(self.manager.active_child, prior)
        self.assertTrue(prior.alive)
        self.assertEqual(self.stopped, ["1.2.0-healthfail"])
        self.assertEqual(self.store.history(20)["health_checks"][0]["phase"], "rollback")

    def test_post_commit_failure_reverts_pointer_before_recovery_check(self):
        baseline = self.manager.deploy("1.0.0")
        self.faults[("1.1.0", "post_commit")] = "WRONG_VERSION"
        snapshots = []
        original_check = self.manager._check

        def check(run, child, phase, **extras):
            snapshots.append((phase, read_json(self.policy.workspace, POINTER)["release_id"]))
            return original_check(run, child, phase, **extras)

        with patch.object(self.manager, "_check", side_effect=check):
            result = self.manager.deploy("1.1.0")
        self.assertEqual(result.state, "ROLLED_BACK")
        self.assertIn(("post_commit", "1.1.0"), snapshots)
        self.assertIn(("rollback", "1.0.0"), snapshots)
        self.assertEqual(self.store.active()["run_id"], baseline.run_id)

    def test_recovery_failure_is_an_incident_and_not_rolled_back(self):
        self.manager.deploy("1.0.0")
        self.faults[("1.0.0", "rollback")] = "HEALTH_TIMEOUT"
        result = self.manager.deploy("1.5.0-tampered")
        self.assertEqual((result.exit_code, result.state), (5, "RECOVERY_FAILED"))
        self.assertEqual(self.store.history(10)["incidents"][0]["code"], "RECOVERY_FAILED")
        self.assertIsNone(self.manager.active_child)

    def test_manual_rollback_uses_verified_archived_digest(self):
        baseline = self.manager.deploy("1.0.0")
        self.manager.deploy("1.1.0")
        rolled = self.manager.deploy("1.0.0", rollback=True)
        self.assertEqual((rolled.exit_code, rolled.active_version), (0, "1.0.0"))
        row = self.store.active()
        self.assertNotEqual(row["run_id"], baseline.run_id)
        self.assertEqual(row["action"], "rollback")
        self.assertEqual(row["checksum"], releases.bundled(self.policy, "1.0.0").checksum)

    def test_manual_rollback_refuses_unverified_release(self):
        self.manager.deploy("1.0.0")
        result = self.manager.deploy("1.2.0-healthfail", rollback=True)
        self.assertEqual((result.exit_code, result.code), (2, "NO_VERIFIED_RELEASE"))
        self.assertEqual(len(self.launched), 1)
        self.assertTrue(self.manager.active_child.alive)

    def test_damaged_prior_archive_blocks_upgrade_before_starting_a_candidate(self):
        baseline = self.manager.deploy("1.0.0")
        manifest = self.policy.workspace / "releases" / baseline.run_id / "release.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        result = self.manager.deploy("1.1.0")
        self.assertEqual((result.exit_code, result.code, result.state), (3, "ARTIFACT_CHANGED", "ROLLED_BACK"))
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.store.active()["run_id"], baseline.run_id)
        self.assertTrue(self.manager.active_child.alive)

    def test_lock_conflict_leaves_audit_and_pointer_unchanged(self):
        with WorkspaceLock(self.policy.workspace, "operation"):
            with self.assertRaises(DeployError) as raised:
                self.manager.deploy("1.0.0")
        self.assertEqual(raised.exception.exit_code, 6)
        self.assertEqual(self.store.history(20)["deployments"], [])
        self.assertIsNone(read_json(self.policy.workspace, POINTER))

    def test_reconciliation_restores_sqlite_authority_and_interrupted_run(self):
        baseline = self.manager.deploy("1.0.0")
        run = "f" * 32
        self.store.begin(run, baseline.run_id, "1.1.0", "deploy")
        self.store.staged(run, "1.1.0", "f" * 64, "fixture")
        for stage in ("PREFLIGHT_OK", "STARTING", "HEALTHY"):
            self.store.transition(run, stage, stage, stage)
        atomic_json(self.policy.workspace, POINTER, {"release_id": "1.1.0", "run_id": run, "port": 8916})
        self.assertEqual(self.manager.reconcile().exit_code, 0)
        self.assertEqual(read_json(self.policy.workspace, POINTER)["run_id"], baseline.run_id)
        self.assertEqual(self.store.history(20)["deployments"][0]["state"], "ROLLED_BACK")
        self.assertIn("POINTER_RECONCILED", [row["code"] for row in self.store.history(20)["events"]])

    def test_explicit_stop_preserves_audit_and_clears_active_projection(self):
        baseline = self.manager.deploy("1.0.0")
        self.assertEqual(self.manager.stop(), {"stopped": True})
        self.assertIsNone(self.store.active())
        self.assertIsNone(read_json(self.policy.workspace, POINTER))
        self.assertEqual(self.store.history(2)["pointer_changes"][0]["old_run"], baseline.run_id)
        self.assertFalse(self.launched[0].alive)
