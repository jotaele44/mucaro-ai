"""Native macOS offline certification gate; requires preprovisioned repository and dependencies."""
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if platform.system() != "Darwin":
    print(json.dumps({"status": "BLOCKED", "reason": "MACOS_NATIVE_HOST_REQUIRED", "host": platform.system()}))
    sys.exit(2)
if not (root / "backend/app/model_router.py").exists() or not (root / ".git").exists():
    print(json.dumps({"status": "BLOCKED", "reason": "COMPLETE_REPOSITORY_REQUIRED"}))
    sys.exit(2)
sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
cmd = [
    sys.executable, "-m", "unittest", "-v",
    "backend.tests.test_model_contract_v1",
    "backend.tests.test_model_contract_router_integration",
    "backend.tests.test_model_local_redirect_security",
]
try:
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=180)
except subprocess.TimeoutExpired:
    print(json.dumps({"status": "FAIL", "reason": "TEST_TIMEOUT", "git_sha": sha}))
    sys.exit(1)
print(json.dumps({
    "status": "PASS" if result.returncode == 0 else "FAIL",
    "git_sha": sha,
    "host": platform.platform(),
    "python": sys.version.split()[0],
    "exit_code": result.returncode,
    "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
    "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
    "stderr_tail": result.stderr[-3000:],
}))
sys.exit(result.returncode)
