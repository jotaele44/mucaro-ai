import json
from pathlib import Path

import pytest

from scripts import certify_security_gates as cert


@pytest.mark.parametrize("observation,expected", [("missing", "FAIL"), ("loopback", "PASS"), ("public", "FAIL")])
def test_network_gate_preserves_fail_closed_receipts_when_pid_unobservable(monkeypatch, observation, expected):
    stopped = []
    class Process:
        pid = 123456789
        def __init__(self, args, **kwargs):
            data = Path(args[args.index("--data-dir") + 1])
            data.mkdir(parents=True, mode=0o700)
            path = data / "instance.json"
            path.write_text(json.dumps({"security": {"production_ready": True}}))
            path.chmod(0o600)
        def poll(self):
            return None
        def send_signal(self, signal):
            stopped.append(signal)
        def wait(self, timeout):
            return 0
    def observed(pid):
        assert pid == Process.pid
        if observation == "missing":
            raise FileNotFoundError("PID namespace prevents observation")
        return ["127.0.0.1:8000"] if observation == "loopback" else ["0.0.0.0:8000"]
    monkeypatch.setattr(cert.subprocess, "Popen", Process)
    monkeypatch.setattr(cert, "listening_addresses", observed)
    result = cert.network_exposure_gate()
    assert result["status"] == expected
    if observation == "missing":
        assert result["detail"]["observation_status"] == "BLOCKED"
    assert stopped
