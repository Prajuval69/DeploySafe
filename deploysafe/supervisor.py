"""Persistent fixture ownership with a workspace-only JSON mailbox."""

from datetime import datetime, timezone
import logging
import os
import sqlite3
import subprocess
import time
import uuid

from .config import prepare_workspace
from .locking import WorkspaceLock, held
from .manager import Manager, validate
from .models import BUSY, RECOVERY_FAILED, DeployError, utc_now
from .processes import environment, worker_command
from .security import atomic_json, read_json, regular_file, token
from .storage import Store

LEASE = "runtime/supervisor.json"
OPERATIONS = ("deploy", "rollback", "recover", "stop", "validate")
LOGGER = logging.getLogger(__name__)


def _lease(policy):
    try:
        data = read_json(policy.workspace, LEASE)
        if type(data) is not dict:
            return None
        token(data.get("session"))
        return data
    except (FileNotFoundError, DeployError):
        return None


def _owned_lease(policy):
    current = _lease(policy)
    if not current or not held(policy.workspace, "supervisor"):
        return None
    try:
        owner = read_json(policy.workspace, "locks/supervisor.json")
    except (FileNotFoundError, DeployError):
        return None
    return current if type(owner) is dict and owner.get("token") == current["session"] else None


def observed(policy, active):
    baseline = {"active": active, "live": False, "health": "PROCESS_OFFLINE" if active else "INACTIVE",
                "observed_at": None}
    if active is None or not held(policy.workspace, "supervisor"):
        return baseline
    try:
        status = _owned_lease(policy)["status"]
        observation = datetime.fromisoformat(status["observed_at"])
        age = (datetime.now(timezone.utc) - observation).total_seconds()
        if (0 <= age <= 4 and status["active"] == active and type(status["live"]) is bool
                and type(status["health"]) is str and len(status["health"]) <= 40):
            return {**baseline, "live": status["live"], "health": status["health"],
                    "observed_at": status["observed_at"]}
    except (TypeError, KeyError, ValueError):
        pass
    return {**baseline, "health": "OBSERVATION_PENDING"}


def _dispatch(manager, request):
    action, value = request.get("action"), request.get("value")
    if action in ("deploy", "rollback"):
        data = manager.deploy(value, rollback=action == "rollback").as_dict()
    elif action == "recover":
        data = manager.reconcile().as_dict()
    elif action == "stop":
        data = manager.stop()
    elif action == "validate":
        data = validate(manager.policy, value, manager.store.active(), initialized=True)
    else:
        raise DeployError("INVALID_OPERATION", "Unsupported supervisor operation.", 2)
    return {"data": data}


def _requests(policy, session, manager):
    runtime = policy.workspace / "runtime"
    for path in runtime.glob("*.request.json"):
        name = path.name.removesuffix(".request.json")
        regular_file(policy.workspace, path)
        try:
            token(name)
            request = read_json(policy.workspace, path)
        except DeployError:
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        if type(request) is not dict or request.get("session") != session:
            continue
        stopping = False
        try:
            response = _dispatch(manager, request)
            stopping = request["action"] == "stop"
        except DeployError as exc:
            response = {"error": {"code": exc.code, "message": str(exc), "exit_code": exc.exit_code}}
        except (OSError, sqlite3.Error):
            response = {"error": {"code": "SUPERVISOR_IO", "message": "Check workspace and audit history.",
                                  "exit_code": RECOVERY_FAILED}}
            stopping = True
        yield name, response, stopping
        if stopping:
            break


def serve(policy):
    root = prepare_workspace(policy)
    logging.basicConfig(level=policy.logging_level, format="%(levelname)s %(message)s")
    with WorkspaceLock(root, "supervisor", timeout=1) as lock, Store(root) as store:
        manager = Manager(policy, store)
        lease = root / LEASE
        try:
            startup = manager.reconcile().as_dict()
            if startup["exit_code"]:
                LOGGER.warning("Startup recovery failed. See SQLite incidents for details.")
            LOGGER.info("Workspace supervisor ready.")
            store.event(None, None, "SUPERVISOR", "INFO", "SUPERVISOR_STARTED",
                        "Workspace supervisor owns the local fixture lifecycle.")
            last_update = 0
            while True:
                if time.monotonic() - last_update >= 1:
                    atomic_json(root, lease, {"session": lock.token, "status": manager.status(),
                                              "startup": startup, "published_at": utc_now()})
                    last_update = time.monotonic()
                for name, response, stopping in _requests(policy, lock.token, manager):
                    if stopping:
                        lease.unlink(missing_ok=True)
                    else:
                        atomic_json(root, lease, {"session": lock.token, "status": manager.status(),
                                                  "startup": startup, "published_at": utc_now()})
                    atomic_json(root, f"runtime/{name}.response.json", response)
                    if stopping:
                        LOGGER.info("Workspace supervisor exiting.")
                        return
                time.sleep(0.05)
        finally:
            lease.unlink(missing_ok=True)
            manager.close()


def _spawn(policy):
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True})
    path = regular_file(policy.workspace, f"logs/{uuid.uuid4().hex}.supervisor.log", missing=True)
    with path.open("xb") as log:
        return subprocess.Popen(worker_command("serve", policy.workspace),
                                cwd=policy.workspace, env=environment(), shell=False,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options)


def _server(policy, *, start):
    existing = _lease(policy)
    process = None
    if not held(policy.workspace, "supervisor"):
        if not start:
            return None
        process = _spawn(policy)
    deadline = time.monotonic() + 12 + _check_budget(policy)
    while time.monotonic() < deadline:
        current = _owned_lease(policy)
        if current and (not process or current["session"] != (existing or {}).get("session")):
            return current
        if process and process.poll() is not None:
            break
        time.sleep(0.05)
    raise DeployError("SUPERVISOR_START", "Supervisor did not become ready. Inspect logs and run recover.",
                      RECOVERY_FAILED)


def _check_budget(policy):
    return 2 * policy.health_attempts * (policy.health_timeout_seconds + policy.backoff_seconds) + 3


def request(policy, action, value=None):
    if action not in OPERATIONS:
        raise DeployError("INVALID_OPERATION", "Unsupported local control request.", 2)
    root = prepare_workspace(policy)
    with WorkspaceLock(root, "client"):
        server = _server(policy, start=action in ("deploy", "rollback", "recover"))
        if server is None:
            if action == "validate":
                with Store(root, readonly=True) as store:
                    return validate(policy, value, store.active(), initialized=True)
            with WorkspaceLock(root, "supervisor"), Store(root) as store:
                return Manager(policy, store).stop()
        name = uuid.uuid4().hex
        inbox = root / "runtime" / f"{name}.request.json"
        outbox = root / "runtime" / f"{name}.response.json"
        atomic_json(root, inbox, {"session": server["session"], "action": action, "value": value})
        deadline = time.monotonic() + 5 * _check_budget(policy) + 20
        try:
            while time.monotonic() < deadline:
                if outbox.exists():
                    reply = read_json(root, outbox, 65536)
                    if "error" in reply:
                        raise DeployError(**reply["error"])
                    if action == "stop":
                        stop_deadline = time.monotonic() + 2
                        while held(root, "supervisor") and time.monotonic() < stop_deadline:
                            time.sleep(0.025)
                    return reply["data"]
                if not held(root, "supervisor") and not outbox.exists():
                    break
                time.sleep(0.05)
            raise DeployError("SUPERVISOR_REPLY", "No reply. Inspect status and history before retrying.",
                              BUSY if held(root, "supervisor") else RECOVERY_FAILED)
        finally:
            inbox.unlink(missing_ok=True)
            outbox.unlink(missing_ok=True)
