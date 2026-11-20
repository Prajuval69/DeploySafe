"""Only the checked-in test suite and named bundled validation are runnable."""

import subprocess
import sys

from . import releases
from .models import DeployError, PREFLIGHT
from .processes import environment


def bundled_validation(policy):
    checked = []
    expected = {"1.3.0-badconfig": "PREFLIGHT_INVALID_BIND", "1.5.0-tampered": "CHECKSUM_MISMATCH"}
    for version in releases.BUNDLED_IDS:
        try:
            releases.bundled(policy, version)
            code = "OK"
        except DeployError as exc:
            code = exc.code
        checked.append({"release_id": version, "code": code, "passed": code == expected.get(version, "OK")})
    return {"passed": all(item["passed"] for item in checked), "releases": checked}


def run_suite(policy):
    try:
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                                cwd=policy.repo, env=environment(), shell=False, stdout=sys.stderr,
                                stderr=sys.stderr, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise DeployError("TEST_TIMEOUT", "Bundled test suite exceeded its 180-second limit.", PREFLIGHT) from exc
    return {"passed": result.returncode == 0, "test_process_exit": result.returncode}
