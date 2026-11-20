"""Own fixture processes through Popen handles and a supervisor lifeline."""

from dataclasses import dataclass
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from .models import DeployError, utc_now
from .releases import artifact
from .security import LOOPBACK, atomic_json, port_number, token

LOG_LIMIT = 65536
WORKER = Path(__file__).with_name("_worker.py").absolute()


def environment():
    keys = ("SYSTEMROOT", "WINDIR") if os.name == "nt" else ()
    return {key: os.environ[key] for key in keys if key in os.environ}


def worker_command(role, *args):
    return [sys.executable, "-I", "-B", "-u", str(WORKER), role, *map(str, args)]


def check_port(port):
    port_number(port)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((LOOPBACK, port))
    except OSError as exc:
        raise DeployError("PORT_IN_USE", f"127.0.0.1:{port} cannot be reserved.") from exc


def wait_ready(child):
    deadline = time.monotonic() + 2
    while child.alive and time.monotonic() < deadline:
        try:
            with socket.create_connection((LOOPBACK, child.port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.025)


@dataclass
class Child:
    process: subprocess.Popen
    version: str
    port: int
    run_id: str
    instance: str

    @property
    def alive(self):
        return self.process.poll() is None

    def stop(self):
        # EOF instructs the guardian to stop its own fixture. Never signal a PID
        # read from a stale workspace file, or kill the guardian out from under it.
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise DeployError("STOP_TIMEOUT", "Owned process guardian did not confirm cleanup.") from exc


def launch(policy, run_id, version, checksum, port, instance):
    port_number(port)
    token(instance)
    release = artifact(policy, run_id, version, checksum)
    check_port(port)
    try:
        process = subprocess.Popen(
            worker_command("guard", policy.workspace, run_id, version, checksum, port, instance),
            cwd=release.path, env=environment(), shell=False, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise DeployError("START_FAILED", "Could not start the fixture guardian.") from exc
    return Child(process, version, port, run_id, instance)


def capture(stream, log):
    total = 0
    while chunk := stream.read1(8192):
        if total < LOG_LIMIT:
            data = bytes(byte for byte in chunk if byte in (9, 10, 13) or 32 <= byte < 127)
            data = data[:LOG_LIMIT - total]
            log.write(data)
            log.flush()
            total += len(data)


def stop_owned(process):
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
        except ProcessLookupError:
            pass
    return process.wait(timeout=1)


def guard(policy, run_id, version, checksum, port, instance):
    from .security import regular_file

    token(instance)
    port_number(port)
    release = artifact(policy, run_id, version, checksum)
    lifeline_lost = threading.Event()

    def wait_for_eof():
        try:
            while sys.stdin.buffer.read(1):
                pass
        finally:
            lifeline_lost.set()

    threading.Thread(target=wait_for_eof, daemon=True).start()
    log_path = regular_file(policy.workspace, f"logs/{instance}.log", missing=True)
    registry = policy.workspace / "runtime" / f"{instance}.process.json"
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-u", str(release.path / "app.py"),
             "--port", str(port), "--config", str(release.path / "app_config.json")],
            cwd=release.path, env=environment(), shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        reader = threading.Thread(target=capture, args=(process.stdout, log), daemon=True)
        reader.start()
        metadata = {"pid": process.pid, "guardian_pid": os.getpid(), "release_id": version,
                    "run_id": run_id, "workspace": str(policy.workspace), "port": port,
                    "started_at": utc_now()}
        try:
            atomic_json(policy.workspace, registry, metadata)
            while process.poll() is None and not lifeline_lost.wait(0.015):
                pass
        finally:
            code = stop_owned(process)
            reader.join(timeout=1)
            process.stdout.close()
            atomic_json(policy.workspace, registry, {**metadata, "exited_at": utc_now(), "exit_code": code})
    # The lifeline thread can still be blocked in read() when the fixture crashes;
    # skip buffered interpreter finalization after all owned resources are closed.
    os._exit(0 if code == 0 else 1)
