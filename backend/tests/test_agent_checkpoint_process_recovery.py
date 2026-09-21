from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from backend.app.agent_checkpoints import get_checkpoint


SHA = "b" * 40


def _run(repo: Path, root: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MUCARO_AI_ROOT"] = str(root)
    env["PYTHONPATH"] = str(repo)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_checkpoint_survives_fresh_process_and_resumes_exactly_once(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    writer = _run(
        repo,
        tmp_path,
        f"""
import json
from backend.app.agent_checkpoints import create_checkpoint, transition_checkpoint
from backend.app.config import Settings
cfg = Settings.load()
created = create_checkpoint(
    session_id="session-cross-process",
    run_id="run-cross-process",
    objective="Resume exact state",
    source_sha="{SHA}",
    state="EXECUTING",
    completed_steps=[{{"id": "done-1", "status": "PASS"}}],
    pending_steps=[{{"id": "next-1", "status": "OPEN"}}],
    contradictions=[{{"id": "contra-1", "type": "IDENTITY", "state": "UNRESOLVED"}}],
    provider="mock",
    model="mock",
    cfg=cfg,
)
paused = transition_checkpoint(
    created["checkpoint_id"],
    state="PAUSED_PROVIDER",
    pause_reason="provider unavailable",
    cfg=cfg,
)
print(json.dumps(paused, sort_keys=True))
""",
    )
    paused = json.loads(writer.stdout)
    assert paused["sequence"] == 1

    reader = _run(
        repo,
        tmp_path,
        f"""
import json
from backend.app.agent_checkpoints import get_checkpoint, resume_checkpoint
from backend.app.config import Settings
cfg = Settings.load()
before = get_checkpoint("{paused['checkpoint_id']}", cfg)
resumed = resume_checkpoint("{paused['checkpoint_id']}", cfg)
print(json.dumps({{"before": before, "resumed": resumed}}, sort_keys=True))
""",
    )
    payload = json.loads(reader.stdout)
    before = payload["before"]
    resumed = payload["resumed"]

    for field in ("checkpoint_id", "session_id", "run_id", "source_sha", "objective", "completed_steps", "pending_steps", "contradictions"):
        assert before[field] == paused[field]
        assert resumed[field] == paused[field]
    assert before["state"] == "PAUSED_PROVIDER"
    assert before["pause_reason"] == "provider unavailable"
    assert resumed["state"] == "EXECUTING"
    assert resumed["pause_reason"] is None
    assert resumed["sequence"] == paused["sequence"] + 1

    verify = _run(
        repo,
        tmp_path,
        f"""
import json
from backend.app.agent_checkpoints import get_checkpoint
from backend.app.config import Settings
print(json.dumps(get_checkpoint("{paused['checkpoint_id']}", Settings.load()), sort_keys=True))
""",
    )
    persisted = json.loads(verify.stdout)
    assert persisted == resumed


def test_terminal_checkpoint_survives_restart_and_stays_terminal(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    writer = _run(
        repo,
        tmp_path,
        f"""
from backend.app.agent_checkpoints import create_checkpoint, transition_checkpoint
from backend.app.config import Settings
cfg = Settings.load()
created = create_checkpoint(session_id="s-terminal", run_id="r-terminal", objective="Finish", source_sha="{SHA}", cfg=cfg)
done = transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
print(done["checkpoint_id"])
""",
    )
    checkpoint_id = writer.stdout.strip()
    reader = _run(
        repo,
        tmp_path,
        f"""
import json
from backend.app.agent_checkpoints import CheckpointError, get_checkpoint, transition_checkpoint
from backend.app.config import Settings
cfg = Settings.load()
checkpoint = get_checkpoint("{checkpoint_id}", cfg)
try:
    transition_checkpoint("{checkpoint_id}", state="EXECUTING", cfg=cfg)
except CheckpointError as exc:
    print(json.dumps({{"state": checkpoint["state"], "status": exc.status}}))
""",
    )
    result = json.loads(reader.stdout)
    assert result == {"state": "COMPLETED", "status": "ERROR_CHECKPOINT_TERMINAL"}


def test_corrupt_checkpoint_payload_fails_closed_after_restart(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    writer = _run(
        repo,
        tmp_path,
        f"""
from backend.app.agent_checkpoints import create_checkpoint
from backend.app.config import Settings
checkpoint = create_checkpoint(session_id="s-corrupt", run_id="r-corrupt", objective="Corrupt", source_sha="{SHA}", cfg=Settings.load())
print(checkpoint["checkpoint_id"])
""",
    )
    checkpoint_id = writer.stdout.strip()
    db_path = tmp_path / "data" / "processed" / "mucaro_ai.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?",
            ("{not-json", checkpoint_id),
        )

    reader = subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
from backend.app.agent_checkpoints import get_checkpoint
from backend.app.config import Settings
get_checkpoint("{checkpoint_id}", Settings.load())
""",
        ],
        cwd=repo,
        env={**os.environ, "MUCARO_AI_ROOT": str(tmp_path), "PYTHONPATH": str(repo)},
        text=True,
        capture_output=True,
    )
    assert reader.returncode != 0
    assert "JSONDecodeError" in reader.stderr
