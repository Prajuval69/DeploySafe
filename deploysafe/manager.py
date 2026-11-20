"""Deployment transaction, rollback, and startup reconciliation."""

from datetime import datetime, timedelta, timezone
import os
import sqlite3
import time
import uuid

from . import health, processes, releases
from .config import prepare_workspace, require_python
from .locking import WorkspaceLock
from .models import DEPLOY_FAILED, INVALID, RECOVERY_FAILED, DeployError, Result, utc_now
from .security import atomic_json, read_json

POINTER = "runtime/ACTIVE.json"


def pointer(row):
    if row is None:
        return None
    return {"run_id": row["run_id"], "release_id": row["to_release"], "port": row["port"]}


def port_for(policy, active):
    if active and active["port"] == policy.active_port:
        return policy.candidate_port
    return policy.active_port


def check_writable(policy, *, initialized):
    if initialized:
        prepare_workspace(policy)
    directory = policy.workspace / "staging" if initialized else policy.workspace.parent
    if not os.access(directory, os.W_OK):
        raise DeployError("WORKSPACE_READ_ONLY", "Workspace staging is not writable.")


def validate(policy, value, active=None, *, initialized=False):
    require_python()
    release = releases.bundled(policy, value)
    check_writable(policy, initialized=initialized)
    checks = ["python", "bundled_path", "manifest", "checksums", "app_config", "workspace_writable"]
    if active:
        releases.artifact(policy, active["run_id"], active["to_release"], active["checksum"])
        checks.append("previous_artifact")
    port = port_for(policy, active)
    processes.check_port(port)
    checks.append("candidate_port")
    return {"release_id": release.version, "checksum": release.checksum, "candidate_port": port,
            "checks": checks}


class Manager:
    def __init__(self, policy, store):
        self.policy = policy
        self.store = store
        self.children = {}
        self.active_child = None

    def _write_pointer(self, value):
        atomic_json(self.policy.workspace, POINTER, value)

    def _check(self, run, child, phase, *, attempts=None):
        result = health.verify(self.policy, child,
                               lambda name, attempt, check: self.store.check(
                                   run, child.version, name, attempt, check),
                               phase, attempts=attempts)
        if not result.ok:
            raise DeployError(result.code, result.message, DEPLOY_FAILED)

    def _start(self, run, version, checksum, port, audit_run):
        instance = uuid.uuid4().hex
        child = processes.launch(self.policy, run, version, checksum, port, instance)
        self.children[instance] = child
        try:
            self.store.event(audit_run, version, "PROCESS", "INFO", "OWNED_PROCESS",
                             f"127.0.0.1:{port} · logs/{instance}.log")
            processes.wait_ready(child)
        except BaseException:
            self._stop(child)
            raise
        return child

    def _stop(self, child):
        if child is not None:
            child.stop()
            self.children.pop(child.instance, None)

    def close(self):
        failure = None
        for child in list(self.children.values()):
            try:
                self._stop(child)
            except DeployError as exc:
                failure = exc
        self.active_child = None
        if failure:
            raise failure

    def _restore(self, previous, run, phase):
        if previous is None:
            return
        child = self.active_child
        if child is not None and child.run_id == previous["run_id"] and child.alive:
            try:
                self._check(run, child, phase, attempts=1)
                return
            except DeployError:
                self._stop(child)
                self.active_child = None
        else:
            self._stop(child)
            self.active_child = None

        # Following a supervisor crash, the prior guardian may still be reacting
        # to EOF. Retry only the previously recorded port for a short window.
        port = previous["port"]
        deadline = time.monotonic() + 1
        while True:
            try:
                child = self._start(previous["run_id"], previous["to_release"],
                                    previous["checksum"], port, run)
                break
            except DeployError as exc:
                if exc.code != "PORT_IN_USE" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        try:
            self._check(run, child, phase)
        except DeployError:
            self._stop(child)
            raise
        self.active_child = child

    def reconcile(self):
        with WorkspaceLock(self.policy.workspace, "operation"):
            return self._reconcile()

    def _reconcile(self):
        previous = self.store.active()
        expected = pointer(previous)
        try:
            present = read_json(self.policy.workspace, POINTER)
        except (FileNotFoundError, DeployError):
            present = {}
        unfinished = self.store.unfinished()
        if present != expected:
            self._write_pointer(expected)
            self.store.event(None, None, "RECOVERY", "INFO", "POINTER_RECONCILED",
                             "Rebuilt ACTIVE.json from committed SQLite history.")
        for row in unfinished:
            run = row["run_id"]
            if row["state"] not in ("FAILED", "ROLLING_BACK"):
                self.store.transition(run, "FAILED", "INTERRUPTED", "Deployment was interrupted.",
                                      "FAIL", finished=previous is None)
            if previous and row["state"] != "ROLLING_BACK":
                self.store.transition(run, "ROLLING_BACK", "RESTORING", "Restoring the committed release.", "INFO")
        try:
            self._restore(previous, None, "startup_recovery")
        except (DeployError, OSError) as exc:
            self.store.incident(None, "STARTUP_RECOVERY_FAILED", str(exc))
            self.store.event(None, expected["release_id"], "RECOVERY_FAILED", "FAIL",
                             "STARTUP_RECOVERY_FAILED", str(exc))
            for row in unfinished:
                self.store.transition(row["run_id"], "RECOVERY_FAILED", "RECOVERY_FAILED", str(exc),
                                      "FAIL", finished=True)
            return Result(RECOVERY_FAILED, "RECOVERY_FAILED", str(exc), active_version=expected["release_id"])
        if previous:
            for row in unfinished:
                run = row["run_id"]
                self.store.transition(run, "ROLLED_BACK", "PRIOR_HEALTHY", "Committed release verified after restart.",
                                      finished=True)
            self.store.event(None, expected["release_id"], "RECOVERY", "PASS", "RESTORED",
                             "Committed release passed localhost checks.")
        return Result(0, "OK", "Workspace reconciled.", active_version=expected["release_id"] if expected else None)

    def _preflight(self, run, name, action):
        try:
            value = action()
        except DeployError as exc:
            self.store.preflight(run, name, exc.code, str(exc))
            raise
        except OSError as exc:
            self.store.preflight(run, name, "WORKSPACE_IO", "Workspace operation could not complete.")
            raise DeployError("WORKSPACE_IO", "Workspace operation could not complete.") from exc
        self.store.preflight(run, name, "OK", f"{name} passed.")
        return value

    def _rollback_release(self, version, previous):
        if previous and previous["to_release"] == version:
            raise DeployError("ALREADY_ACTIVE", "Requested release is already active.", INVALID)
        for old in self.store.prior(version):
            if (self.policy.workspace / "releases" / old["run_id"]).exists():
                return releases.artifact(self.policy, old["run_id"], version, old["checksum"])
        raise DeployError("NO_VERIFIED_RELEASE", "No retained, previously verified release matches.", INVALID)

    def deploy(self, value, *, rollback=False):
        with WorkspaceLock(self.policy.workspace, "operation"):
            return self._deploy(value, rollback)

    def _deploy(self, value, rollback):
        previous = self.store.active()
        known = pointer(previous)
        run = uuid.uuid4().hex
        try:
            version = releases.release_id(value) if rollback else releases.fixture_path(self.policy, value).name
        except DeployError as exc:
            self.store.begin(run, known["run_id"] if known else None, None, "rollback" if rollback else "deploy")
            self.store.preflight(run, "release_path", exc.code, str(exc))
            return self._fail(run, previous, None, exc)
        self.store.begin(run, known["run_id"] if known else None, version, "rollback" if rollback else "deploy")
        candidate = None
        try:
            get_source = (lambda: self._rollback_release(version, previous)) if rollback else (
                lambda: releases.bundled(self.policy, value))
            release = self._preflight(run, "manifest_checksums_app_config", get_source)
            self._preflight(run, "python", require_python)
            self._preflight(run, "workspace_writable", lambda: check_writable(self.policy, initialized=True))
            self._preflight(run, "stage_integrity", lambda: releases.stage(self.policy, release, run))
            self.store.staged(run, version, release.checksum, "verified archive" if rollback else "bundled fixture")
            if previous and not rollback:
                self._preflight(run, "previous_artifact", lambda: releases.artifact(
                    self.policy, previous["run_id"], previous["to_release"], previous["checksum"]))
                self._preflight(run, "previous_health", lambda: self._restore(previous, run, "previous"))
            port = port_for(self.policy, previous)
            self._preflight(run, "candidate_port", lambda: processes.check_port(port))
            self.store.port(run, port)
            self.store.transition(run, "PREFLIGHT_OK", "PREFLIGHT_OK", "Preflight checks passed.")
            self.store.transition(run, "STARTING", "STARTING", "Launching an owned loopback process.", "INFO")
            candidate = self._start(run, version, release.checksum, port, run)
            self._check(run, candidate, "candidate")
            self.store.transition(run, "HEALTHY", "HEALTHY", "Candidate health and version checks passed.")
            self._write_pointer({"release_id": version, "run_id": run, "port": port})
            self._check(run, candidate, "post_commit")
            self.store.activate(run, known["run_id"] if known else None)
        except (DeployError, OSError, sqlite3.Error) as exc:
            if not isinstance(exc, DeployError):
                exc = DeployError("COMMIT_IO", "Could not persist the deployment transaction.", DEPLOY_FAILED)
            row = self.store.one("SELECT state FROM deployments WHERE run_id=?", (run,))
            if row["state"] in ("STARTING", "HEALTHY") and exc.exit_code < DEPLOY_FAILED:
                exc = DeployError(exc.code, str(exc), DEPLOY_FAILED)
            return self._fail(run, previous, candidate, exc)
        retired, self.active_child = self.active_child, candidate
        try:
            self._stop(retired)
            self._prune()
        except (DeployError, OSError) as exc:
            self.store.incident(run, "CLEANUP_FAILED", str(exc))
        return Result(0, "OK", "Release activated and verified.", run, version, "ACTIVE")

    def _fail(self, run, previous, candidate, failure):
        known = pointer(previous)
        self.store.transition(run, "FAILED", failure.code, str(failure), "FAIL", finished=not known)
        try:
            if known:
                self.store.transition(run, "ROLLING_BACK", "RESTORING", "Checking the prior committed release.", "INFO")
            self._stop(candidate)
            self._write_pointer(known)
            if known:
                self._restore(previous, run, "rollback")
                self.store.transition(run, "ROLLED_BACK", "PRIOR_HEALTHY", "Prior release is healthy.",
                                      finished=True)
        except (DeployError, OSError) as exc:
            self.store.incident(run, "RECOVERY_FAILED", str(exc))
            self.store.transition(run, "RECOVERY_FAILED", "RECOVERY_FAILED", str(exc), "FAIL", finished=True)
            return Result(RECOVERY_FAILED, failure.code, f"{failure} Recovery failed: {exc}", run,
                          known["release_id"] if known else None, "RECOVERY_FAILED")
        try:
            releases.remove_artifact(self.policy, self.policy.workspace / "releases" / run)
        except (DeployError, OSError) as exc:
            self.store.incident(run, "CLEANUP_FAILED", str(exc))
        return Result(failure.exit_code, failure.code, str(failure), run,
                      known["release_id"] if known else None, "ROLLED_BACK" if known else "FAILED")

    def _prune(self):
        rows = self.store.rows("SELECT run_id, finished_at FROM deployments WHERE state='ACTIVE' ORDER BY rowid DESC")
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.policy.retention_days)
        owned = {child.run_id for child in self.children.values()} | {self.store.active()["run_id"]}
        for row in rows[self.policy.retain_release_count:]:
            if row["run_id"] not in owned and datetime.fromisoformat(row["finished_at"]) < cutoff:
                releases.remove_artifact(self.policy, self.policy.workspace / "releases" / row["run_id"])

    def stop(self):
        with WorkspaceLock(self.policy.workspace, "operation"):
            previous = self.store.active()
            self.close()
            self._write_pointer(None)
            self.store.mark_stopped(previous["run_id"] if previous else None)
        return {"stopped": True}

    def status(self):
        row = self.store.active()
        selected = pointer(row)
        live = False
        code = "INACTIVE" if selected is None else "PROCESS_OFFLINE"
        if selected and self.active_child and self.active_child.run_id == selected["run_id"] and self.active_child.alive:
            result = health.verify(self.policy, self.active_child, lambda *_: None, "status", attempts=1)
            live, code = result.ok, result.code
        return {"active": selected, "live": live, "health": code, "observed_at": utc_now()}
