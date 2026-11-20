"""Bounded, proxy-free localhost HTTP health and version checks."""

from dataclasses import dataclass
import http.client
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from .models import DeployError
from .security import LOOPBACK, parse_json, port_number, release_id

ENDPOINTS = ("/health", "/version")
MAX_BODY = 2048


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


@dataclass(frozen=True)
class Check:
    endpoint: str
    code: str
    message: str
    latency_ms: int
    http_status: int | None = None

    @property
    def ok(self):
        return self.code == "OK"


def probe(port, endpoint, version, timeout):
    port_number(port)
    release_id(version)
    if endpoint not in ENDPOINTS:
        raise DeployError("INVALID_ENDPOINT", "Only /health and /version are supported.")
    started = time.monotonic()

    def result(code, message, status=None):
        return Check(endpoint, code, message, int((time.monotonic() - started) * 1000), status)

    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f"http://{LOOPBACK}:{port}{endpoint}", timeout=timeout) as response:
            status = response.status
            if status != 200:
                return result(f"HTTP_{status}", f"Expected HTTP 200, received {status}.", status)
            data = response.read(MAX_BODY + 1)
    except HTTPError as exc:
        exc.close()
        return result(f"HTTP_{exc.code}", f"Expected HTTP 200, received {exc.code}.", exc.code)
    except (URLError, OSError, http.client.HTTPException) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return result("HEALTH_TIMEOUT", "Local HTTP probe timed out.")
        if isinstance(reason, ConnectionRefusedError):
            return result("CONNECTION_REFUSED", "Nothing accepted the local connection.")
        return result("HEALTH_IO", "The local HTTP response could not be read.")
    try:
        if len(data) > MAX_BODY:
            raise ValueError("oversized response")
        body = parse_json(data)
    except (ValueError, RecursionError):
        return result("MALFORMED_JSON", "Response is invalid or exceeds the size limit.", status)
    if (type(body) is not dict or set(body) != {"status", "version"}
            or body["status"] != "ok" or type(body["version"]) is not str):
        return result("INVALID_HEALTH", "Expected status 'ok' and a string version.", status)
    if body["version"] != version:
        return result("WRONG_VERSION", f"Local service did not report expected version {version}.", status)
    return result("OK", f"Local service confirmed {version}.", status)


def verify(policy, child, record, phase, *, attempts=None):
    attempts = policy.health_attempts if attempts is None else attempts
    for attempt in range(1, attempts + 1):
        if not child.alive:
            last = Check("/health", "PROCESS_EXITED", "Owned fixture process exited.", 0)
            record(phase, attempt, last)
            return last
        for endpoint in ENDPOINTS:
            last = probe(child.port, endpoint, child.version, policy.health_timeout_seconds)
            record(phase, attempt, last)
            if not last.ok:
                break
        if last.ok and child.alive:
            return last
        if attempt < attempts:
            time.sleep(policy.backoff_seconds)
    if not child.alive:
        last = Check("/health", "PROCESS_EXITED", "Owned fixture process exited.", 0)
        record(phase, attempts, last)
    return last
