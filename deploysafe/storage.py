"""SQLite state projection and append-only deployment evidence."""

from contextlib import contextmanager
import json
import sqlite3

from .models import DeployError, utc_now
from .security import printable, regular_file

STATES = {
    "PENDING": {"STAGED", "FAILED"},
    "STAGED": {"PREFLIGHT_OK", "FAILED"},
    "PREFLIGHT_OK": {"STARTING", "FAILED"},
    "STARTING": {"HEALTHY", "FAILED"},
    "HEALTHY": {"ACTIVE", "FAILED"},
    "FAILED": {"ROLLING_BACK", "RECOVERY_FAILED"},
    "ROLLING_BACK": {"ROLLED_BACK", "RECOVERY_FAILED"},
    "ACTIVE": set(), "ROLLED_BACK": set(), "RECOVERY_FAILED": set(),
}
AUDIT = ("releases", "deployment_events", "preflight_checks", "health_checks",
         "active_history", "test_runs", "incidents")
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases (
    id TEXT NOT NULL, checksum TEXT NOT NULL CHECK(length(checksum) = 64),
    created_at TEXT NOT NULL, provenance TEXT NOT NULL, PRIMARY KEY(id, checksum)
);
CREATE TABLE IF NOT EXISTS deployments (
    run_id TEXT PRIMARY KEY CHECK(length(run_id) = 32), from_run TEXT REFERENCES deployments(run_id),
    to_release TEXT, checksum TEXT, port INTEGER CHECK(port BETWEEN 8000 AND 8999),
    action TEXT NOT NULL CHECK(action IN ('deploy','rollback')),
    state TEXT NOT NULL CHECK(state IN ('PENDING','STAGED','PREFLIGHT_OK','STARTING','HEALTHY',
        'ACTIVE','FAILED','ROLLING_BACK','ROLLED_BACK','RECOVERY_FAILED')),
    started_at TEXT NOT NULL, finished_at TEXT, error_code TEXT,
    FOREIGN KEY(to_release, checksum) REFERENCES releases(id, checksum)
);
CREATE TABLE IF NOT EXISTS deployment_events (
    id INTEGER PRIMARY KEY, run_id TEXT REFERENCES deployments(run_id), release_id TEXT,
    timestamp TEXT NOT NULL, stage TEXT NOT NULL, outcome TEXT NOT NULL
        CHECK(outcome IN ('INFO','PASS','FAIL')), code TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preflight_checks (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES deployments(run_id),
    timestamp TEXT NOT NULL, name TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health_checks (
    id INTEGER PRIMARY KEY, run_id TEXT REFERENCES deployments(run_id), timestamp TEXT NOT NULL,
    release_id TEXT NOT NULL, phase TEXT NOT NULL, endpoint TEXT NOT NULL CHECK(endpoint IN ('/health','/version')),
    attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 15), code TEXT NOT NULL,
    latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0), http_status INTEGER, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_history (
    id INTEGER PRIMARY KEY, old_run TEXT REFERENCES deployments(run_id),
    new_run TEXT REFERENCES deployments(run_id), timestamp TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, name TEXT NOT NULL, passed INTEGER NOT NULL
        CHECK(passed IN (0,1)), details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY, run_id TEXT REFERENCES deployments(run_id), timestamp TEXT NOT NULL,
    code TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_run ON deployment_events(run_id, id);
CREATE INDEX IF NOT EXISTS checks_by_run ON health_checks(run_id, id);
CREATE INDEX IF NOT EXISTS deployments_by_release ON deployments(to_release, state);
"""


class Store:
    def __init__(self, root, *, readonly=False):
        path = regular_file(root, "db/deploysafe.sqlite3", missing=not readonly)
        for suffix in ("-wal", "-shm", "-journal"):
            regular_file(root, path.with_name(path.name + suffix), missing=True)
        self.db = sqlite3.connect(path.as_uri() + "?mode=ro" if readonly else path,
                                  uri=readonly, timeout=2)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in ((1, SCHEMA_VERSION) if readonly else (0, 1, SCHEMA_VERSION)):
                raise DeployError("DB_VERSION", "Unsupported audit database schema.")
            if readonly:
                self.db.execute("PRAGMA query_only=ON")
            else:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=FULL")
                if version < SCHEMA_VERSION:
                    self._migrate(version)
        except BaseException:
            self.db.close()
            raise

    def _migrate(self, version):
        guards = []
        for name in AUDIT:
            for action in ("UPDATE", "DELETE"):
                guards.append(f"""CREATE TRIGGER IF NOT EXISTS {name}_no_{action.lower()}
                    BEFORE {action} ON {name} BEGIN
                    SELECT RAISE(ABORT, 'DeploySafe audit rows are immutable'); END;""")
            match = "id = NEW.id AND checksum = NEW.checksum" if name == "releases" else "id = NEW.id"
            guards.append(f"""CREATE TRIGGER IF NOT EXISTS {name}_no_replace BEFORE INSERT ON {name}
                    WHEN EXISTS(SELECT 1 FROM {name} WHERE {match}) BEGIN
                    SELECT RAISE(ABORT, 'DeploySafe audit rows are immutable'); END;""")
        self.db.executescript("BEGIN IMMEDIATE;\n" + (SCHEMA if version == 0 else "") + "\n".join(guards)
                              + f"\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @contextmanager
    def snapshot(self):
        self.db.execute("BEGIN")
        try:
            yield
        finally:
            self.db.rollback()

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def rows(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args)]

    def begin(self, run, old, version, action):
        with self.db:
            self.db.execute("""INSERT INTO deployments(run_id, from_run, to_release, action, state, started_at)
                               VALUES (?, ?, ?, ?, 'PENDING', ?)""", (run, old, version, action, utc_now()))
            self._event(run, version, "PENDING", "INFO", "REQUESTED", f"{action.capitalize()} requested.")

    def _event(self, run, version, stage, outcome, code, message):
        self.db.execute("""INSERT INTO deployment_events(run_id, release_id, timestamp, stage, outcome, code, message)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (run, version, utc_now(), stage, outcome, code, printable(message)))

    def event(self, run, version, stage, outcome, code, message):
        with self.db:
            self._event(run, version, stage, outcome, code, message)

    def _transition(self, run, state, code, message, outcome, *, finished=False):
        row = self.one("SELECT state, to_release FROM deployments WHERE run_id=?", (run,))
        if row is None or state not in STATES[row["state"]]:
            raise DeployError("INVALID_TRANSITION", f"Cannot move deployment to {state}.")
        self.db.execute("""UPDATE deployments SET state=?, finished_at=?,
                           error_code=COALESCE(?, error_code) WHERE run_id=?""",
                        (state, utc_now() if finished else None, code if state == "FAILED" else None, run))
        self._event(run, row["to_release"], state, outcome, code, message)

    def transition(self, run, state, code, message, outcome="PASS", *, finished=False):
        with self.db:
            self._transition(run, state, code, message, outcome, finished=finished)

    def staged(self, run, version, checksum, provenance):
        with self.db:
            self.db.execute("""INSERT INTO releases SELECT ?, ?, ?, ?
                               WHERE NOT EXISTS(SELECT 1 FROM releases WHERE id=? AND checksum=?)""",
                            (version, checksum, utc_now(), provenance, version, checksum))
            self.db.execute("UPDATE deployments SET to_release=?, checksum=? WHERE run_id=?",
                            (version, checksum, run))
            self._transition(run, "STAGED", "STAGED", "Staged and reverified the release snapshot.", "PASS")

    def port(self, run, port):
        with self.db:
            self.db.execute("UPDATE deployments SET port=? WHERE run_id=?", (port, run))

    def _pointer_change(self, old, new, reason):
        self.db.execute("INSERT INTO active_history(old_run, new_run, timestamp, reason) VALUES (?, ?, ?, ?)",
                        (old, new, utc_now(), reason))

    def activate(self, run, old):
        with self.db:
            candidate = self.one("SELECT port, checksum FROM deployments WHERE run_id=?", (run,))
            if not candidate or candidate["port"] is None or candidate["checksum"] is None:
                raise DeployError("INVALID_ACTIVATION", "Committed deployment requires a verified artifact and port.")
            self._transition(run, "ACTIVE", "ACTIVATED", "Post-commit HTTP verification passed.", "PASS",
                             finished=True)
            self._pointer_change(old, run, "verified activation")

    def mark_stopped(self, old):
        if old:
            with self.db:
                self._pointer_change(old, None, "operator stop")
                self._event(old, None, "SHUTDOWN", "PASS", "STOPPED", "Stopped the owned local release.")

    def active(self):
        return self.one("""SELECT d.* FROM deployments d
                           WHERE d.run_id=(SELECT new_run FROM active_history ORDER BY id DESC LIMIT 1)""")

    def prior(self, version):
        return self.rows("""SELECT * FROM deployments WHERE to_release=? AND state='ACTIVE'
                             ORDER BY rowid DESC""", (version,))

    def unfinished(self):
        return self.rows("SELECT * FROM deployments WHERE finished_at IS NULL ORDER BY rowid")

    def preflight(self, run, name, code, message):
        with self.db:
            self.db.execute("""INSERT INTO preflight_checks(run_id, timestamp, name, code, message)
                               VALUES (?, ?, ?, ?, ?)""", (run, utc_now(), name, code, printable(message)))

    def check(self, run, version, phase, attempt, check):
        with self.db:
            self.db.execute("""INSERT INTO health_checks(run_id,timestamp,release_id,phase,endpoint,
                               attempt,code,latency_ms,http_status,message) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (run, utc_now(), version, phase, check.endpoint, attempt, check.code,
                             check.latency_ms, check.http_status, printable(check.message)))

    def incident(self, run, code, message):
        with self.db:
            self.db.execute("INSERT INTO incidents(run_id,timestamp,code,message) VALUES (?,?,?,?)",
                            (run, utc_now(), code, printable(message)))

    def test_result(self, name, passed, details):
        with self.db:
            self.db.execute("INSERT INTO test_runs(timestamp,name,passed,details) VALUES (?,?,?,?)",
                            (utc_now(), name, int(passed), json.dumps(details)))

    def history(self, limit):
        deployments = self.rows("SELECT * FROM deployments ORDER BY rowid DESC LIMIT ?", (limit,))
        events = self.rows("SELECT * FROM deployment_events ORDER BY id DESC LIMIT ?", (limit,))
        checks = self.rows("SELECT * FROM health_checks ORDER BY id DESC LIMIT ?", (limit,))
        incidents = self.rows("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,))
        changes = self.rows("SELECT * FROM active_history ORDER BY id DESC LIMIT ?", (limit,))
        return {"deployments": deployments, "events": events, "health_checks": checks,
                "incidents": incidents, "pointer_changes": changes}
