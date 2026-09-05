import json
import subprocess
import zipfile
from pathlib import Path

import backend.app.cli as cli
from backend.app.cli import main
from backend.app.config import Settings
from backend.app.release_receipts import write_release_receipt


def make_settings(root: Path, *, operator_token: str | None = None) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
        operator_token=operator_token,
    )


def test_cli_ingest_and_ask(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    ingest_output = capsys.readouterr().out
    assert "INGEST_FOLDER_OK" in ingest_output

    assert main(["ask", "What does source lineage require?", "--strategy", "persistent", "--format", "text"], cfg=cfg) == 0
    ask_output = capsys.readouterr().out
    assert "CITATION_OK" in ask_output
    assert "chunk_id=" in ask_output


def test_cli_plan(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["plan", "Add", "CLI", "capability", "--input", "mucaro-ai"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "codebase_build" in output


def test_cli_plan_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["plan", "Add", "CLI", "capability", "--input", "mucaro-ai", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "OK" in output
    assert "active_vector=codebase_build" in output
    assert "recommended_mode=CLAUDE_CODE_MODE" in output
    assert "readiness_percent=" in output


def test_cli_help_lists_serve(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "certify" in output
    assert "serve" in output


def test_cli_version(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert output == "mucaro 0.1.23\n"


def test_cli_validate_text_output(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["validate", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "JSON_OK" in output
    assert "checked=0" in output


def test_cli_doctor_reports_warning_state_without_sources(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["doctor", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCTOR_WARN"
    assert payload["summary"]["source_count"] == 0
    assert payload["summary"]["index_status"] == "INDEX_MISSING"
    assert payload["summary"]["semantic_store_status"] == "SEMANTIC_STORE_MISSING"
    assert payload["summary"]["tool_count"] >= 1
    assert any(check["name"] == "source_library" and check["status"] == "WARN" for check in payload["checks"])
    assert any(check["name"] == "tool_runtime" and check["status"] == "PASS" for check in payload["checks"])

    assert main(["doctor", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "DOCTOR_WARN" in strict_output
    assert "recommendation=" in strict_output


def test_cli_doctor_reports_ready_state_after_local_setup(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path, operator_token="local-token")
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["doctor", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DOCTOR_OK"
    assert payload["summary"]["source_count"] == 1
    assert payload["summary"]["index_status"] == "INDEX_OK"
    assert payload["summary"]["semantic_store_status"] == "SEMANTIC_STORE_OK"
    assert payload["summary"]["auth_required"] is True
    assert payload["summary"]["memory_count"] == 0


def test_cli_product_audit_reports_open_goal_gaps(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["audit", "product", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PRODUCT_AUDIT_OPEN"
    assert payload["readiness_percent"] == 94.1
    assert payload["counts"] == {"pass": 16, "open": 1, "blocked": 0, "total": 17}
    by_id = {row["requirement_id"]: row for row in payload["requirements"]}
    assert by_id["permission_gated_user_directory"]["status"] == "PASS"
    assert by_id["source_lineage_citations"]["status"] == "PASS"
    assert by_id["semantic_vector_store"]["status"] == "PASS"
    assert by_id["tool_use_and_actions"]["status"] == "PASS"
    assert by_id["multimodal_interface"]["status"] == "PASS"
    assert by_id["personalization_memory"]["status"] == "PASS"
    assert by_id["production_security"]["status"] == "PASS"
    assert by_id["source_artifact_packaging"]["status"] == "PASS"
    assert by_id["github_release_governance"]["status"] == "OPEN"
    assert payload["next_recommended_vector"] == "github_release_governance"

    assert main(["audit", "product", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "PRODUCT_AUDIT_OPEN" in strict_output
    assert "PASS source_artifact_packaging" in strict_output
    assert "OPEN github_release_governance" in strict_output


def test_cli_eval_retrieval_reports_quality_metrics(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["eval", "retrieval", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_OK"
    assert payload["counts"] == {
        "chunks": 25,
        "queries": 31,
        "positive_queries": 25,
        "no_context_queries": 6,
        "expected_bindings": 28,
        "source_ids": 25,
        "multi_binding_queries": 3,
        "query_categories": 20,
        "strategies": 6,
    }
    assert payload["fixture_validation"]["status"] == "RETRIEVAL_FIXTURE_OK"
    assert {strategy["strategy"] for strategy in payload["strategies"]} == {
        "lexical",
        "vector",
        "bm25",
        "hybrid",
        "persistent",
        "semantic",
    }
    hybrid = next(strategy for strategy in payload["strategies"] if strategy["strategy"] == "hybrid")
    assert hybrid["expected_binding_recall_at_k"] == 1.0
    assert hybrid["context_precision_at_k"] == 0.2857
    assert hybrid["full_binding_hit_rate"] == 1.0
    assert hybrid["multi_binding_full_hit_rate"] == 1.0
    assert hybrid["positive_retrieved_chunks"] == 98
    assert hybrid["relevant_retrieved_chunks"] == 28

    assert main(["eval", "retrieval", "--format", "text"], cfg=cfg) == 0
    text_output = capsys.readouterr().out
    assert "fixture=backend/tests/fixtures/retrieval_eval.json" in text_output
    assert "positive_queries=25" in text_output
    assert "no_context_queries=6" in text_output
    assert "source_ids=25" in text_output
    assert "multi_binding_queries=3" in text_output
    assert "query_categories=20" in text_output
    assert "fixture_validation=RETRIEVAL_FIXTURE_OK" in text_output
    assert "min_no_context_accuracy=1.0" in text_output
    assert "min_hybrid_precision=0.2" in text_output
    assert "min_expected_binding_recall=0.95" in text_output
    assert "binding_recall=1.00" in text_output
    assert "precision=0.29" in text_output
    assert "multi_full=1.00" in text_output
    assert "hybrid recall@5=" in text_output
    assert "semantic recall@5=" in text_output
    assert "RETRIEVAL_EVAL_OK" in text_output


def test_cli_eval_retrieval_fails_when_any_strategy_returns_unrelated_context(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-no-context-accuracy",
                "1.1",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_no_context_accuracy"] == 1.1


def test_cli_eval_retrieval_fails_when_thresholds_are_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["eval", "retrieval", "--min-hybrid-recall", "1.1", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_hybrid_recall"] == 1.1


def test_cli_eval_retrieval_fails_when_precision_threshold_is_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-hybrid-precision",
                "0.9",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    hybrid = next(strategy for strategy in payload["strategies"] if strategy["strategy"] == "hybrid")
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_hybrid_precision"] == 0.9
    assert hybrid["context_precision_at_k"] == 0.236


def test_cli_eval_retrieval_fails_when_binding_threshold_is_not_met(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert (
        main(
            [
                "eval",
                "retrieval",
                "--fixture",
                "backend/tests/fixtures/retrieval_eval_large.json",
                "--min-expected-binding-recall",
                "1.0",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    bm25 = next(strategy for strategy in payload["strategies"] if strategy["strategy"] == "bm25")
    assert payload["status"] == "RETRIEVAL_EVAL_FAILED"
    assert payload["min_expected_binding_recall"] == 1.0
    assert bm25["expected_binding_recall_at_k"] == 0.9667


def test_cli_eval_retrieval_fails_on_invalid_fixture(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    fixture = tmp_path / "invalid_retrieval_eval.json"
    fixture.write_text(
        json.dumps(
            {
                "chunks": [{"chunk_id": "known", "source_id": "source", "chunk_index": 0, "text": "known text"}],
                "queries": [{"query": "unknown binding", "expected_chunk_ids": ["missing"]}],
            }
        ),
        encoding="utf-8",
    )

    assert main(["eval", "retrieval", "--fixture", str(fixture), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_INVALID"
    checks = {check["name"]: check for check in payload["fixture_validation"]["checks"]}
    assert checks["fixture_size"]["status"] == "FAIL"
    assert checks["expected_chunk_bindings"]["status"] == "FAIL"
    assert checks["negative_cases"]["status"] == "FAIL"


def test_cli_eval_retrieval_fails_on_non_object_fixture(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    fixture = tmp_path / "array_retrieval_eval.json"
    fixture.write_text("[]\n", encoding="utf-8")

    assert main(["eval", "retrieval", "--fixture", str(fixture), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RETRIEVAL_EVAL_INVALID"
    checks = {check["name"]: check for check in payload["fixture_validation"]["checks"]}
    assert checks["fixture_shape"]["status"] == "FAIL"
    assert checks["fixture_shape"]["detail"] == "fixture_type=list chunks_type=missing queries_type=missing"


def write_package_fixture(root: Path, *, frontend_version: str = "0.1.23", lock_version: str | None = None) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """
[project]
name = "mucaro-ai"
version = "0.1.23"

[project.scripts]
mucaro = "backend.app.cli:main"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Mucaro AI\n", encoding="utf-8")
    (root / "docs" / "PACKAGING_POLICY.md").write_text("# Packaging Policy\n", encoding="utf-8")
    for path in [
        "scripts/mucaro.py",
        "scripts/launch_app.py",
        "scripts/bootstrap_macos.sh",
        "scripts/bootstrap_windows.ps1",
        "scripts/certify_release.py",
        "scripts/certify_fresh_clone.py",
        "scripts/package_desktop.py",
        "scripts/certify_source_artifact.py",
        "scripts/certify_python_distribution.py",
    ]:
        (root / path).write_text("# fixture\n", encoding="utf-8")
    (root / "frontend" / "package.json").write_text(
        json.dumps({"name": "mucaro-ai-frontend", "version": frontend_version}),
        encoding="utf-8",
    )
    (root / "frontend" / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "mucaro-ai-frontend",
                "version": lock_version or frontend_version,
                "packages": {
                    "": {
                        "name": "mucaro-ai-frontend",
                        "version": lock_version or frontend_version,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_cli_package_status_reports_packaging_readiness(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)

    assert main(["package", "status", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_WARN"
    assert payload["python_package"]["name"] == "mucaro-ai"
    assert payload["python_package"]["entrypoint"] == "backend.app.cli:main"
    assert payload["frontend_package"]["version"] == "0.1.23"
    assert checks["required_package_inputs"]["status"] == "PASS"
    assert checks["version_consistency"]["status"] == "PASS"
    assert checks["frontend_lockfile"]["status"] == "PASS"
    assert checks["generated_outputs"]["status"] == "WARN"
    assert checks["release_artifact_policy"]["status"] == "PASS"
    assert checks["distribution_policy"]["status"] == "PASS"
    assert checks["hosted_release_gate"]["status"] == "WARN"

    assert main(["package", "status", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "PACKAGE_WARN" in text_output
    assert "PASS cli_entrypoint backend.app.cli:main" in text_output
    assert "WARN hosted_release_gate" in text_output


def test_cli_package_status_fails_on_version_mismatch(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path, frontend_version="0.1.99")

    assert main(["package", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_FAIL"
    assert checks["version_consistency"]["status"] == "FAIL"
    assert checks["version_consistency"]["detail"] == "pyproject=0.1.23 frontend=0.1.99"


def test_cli_package_status_fails_on_stale_frontend_lockfile(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path, lock_version="0.1.22")

    assert main(["package", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_FAIL"
    assert checks["frontend_lockfile"]["status"] == "FAIL"
    assert checks["frontend_lockfile"]["detail"] == (
        "package=mucaro-ai-frontend:0.1.22 "
        "root=mucaro-ai-frontend:0.1.22 "
        "expected=mucaro-ai-frontend:0.1.23"
    )


def commit_fixture_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=mucaro@example.test",
            "-c",
            "user.name=Mucaro Test",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_cli_package_build_writes_source_artifact_manifest(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "PACKAGE_ARTIFACT_READY"
    artifact = Path(payload["artifact"]["path"])
    manifest = Path(payload["manifest"]["path"])
    assert artifact.exists()
    assert manifest.exists()
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["artifact_format"] == "source_zip_with_json_manifest_v1"
    assert manifest_payload["head_sha"]
    assert manifest_payload["member_count"] >= 1
    assert {member["path"] for member in manifest_payload["members"]} >= {"pyproject.toml", "README.md"}

    assert main(["package", "verify", str(manifest), "--require-current-head", "--format", "json"], cfg=cfg) == 0
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFIED"
    assert verify_payload["manifest"]["head_sha"] == manifest_payload["head_sha"]
    assert verify_checks["current_head_binding"]["status"] == "PASS"
    assert verify_checks["archive_member_hashes"]["status"] == "PASS"


def test_cli_package_verify_fails_on_archive_drift(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    artifact = Path(payload["artifact"]["path"])
    manifest = Path(payload["manifest"]["path"])

    with zipfile.ZipFile(artifact, "a") as archive:
        archive.writestr("mucaro-ai-0.1.23/untracked.txt", "unexpected")

    assert main(["package", "verify", str(manifest), "--format", "json"], cfg=cfg) == 2
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFY_FAILED"
    assert verify_checks["archive_sha256"]["status"] == "FAIL"
    assert verify_checks["archive_member_set"]["status"] == "FAIL"


def test_cli_package_verify_fails_on_malformed_manifest_members(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    manifest = Path(payload["manifest"]["path"])
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["members"] = {}
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")

    assert main(["package", "verify", str(manifest), "--format", "json"], cfg=cfg) == 2
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFY_FAILED"
    assert verify_checks["manifest_members"]["status"] == "FAIL"


def test_cli_package_verify_fails_on_duplicate_archive_members(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    output_dir = tmp_path / "artifacts"

    assert main(["package", "build", "--output-dir", str(output_dir), "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    artifact = Path(payload["artifact"]["path"])
    manifest = Path(payload["manifest"]["path"])

    with zipfile.ZipFile(artifact, "a") as archive:
        archive.writestr("mucaro-ai-0.1.23/README.md", "# Duplicate member\n")

    assert main(["package", "verify", str(manifest), "--format", "json"], cfg=cfg) == 2
    verify_payload = json.loads(capsys.readouterr().out)
    verify_checks = {check["name"]: check for check in verify_payload["checks"]}
    assert verify_payload["status"] == "PACKAGE_ARTIFACT_VERIFY_FAILED"
    assert verify_checks["archive_member_duplicates"]["status"] == "FAIL"
    assert verify_checks["archive_member_set"]["status"] == "FAIL"


def test_cli_package_build_blocks_dirty_worktree(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    commit_fixture_repo(tmp_path)
    (tmp_path / "README.md").write_text("# Dirty Mucaro AI\n", encoding="utf-8")

    assert main(["package", "build", "--output-dir", str(tmp_path / "artifacts"), "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "PACKAGE_ARTIFACT_BLOCKED"
    assert checks["worktree_clean"]["status"] == "FAIL"


def test_cli_certify_status_reports_release_readiness(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["certify", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "CERTIFICATION_FAIL"
    assert payload["version"] == "0.1.23"
    assert payload["project_root"] == str(tmp_path)
    assert payload["product_audit_status"] == "PRODUCT_AUDIT_OPEN"
    assert payload["json_status"] == "JSON_OK"
    assert payload["github"] == {"enabled": False}
    assert payload["git"]["head_sha"] is None
    gates = {gate["name"]: gate for gate in payload["gates"]}
    assert gates["product_audit"]["status"] == "FAIL"
    assert gates["product_audit"]["detail"] == "PRODUCT_AUDIT_OPEN"
    assert gates["json_validation"]["status"] == "PASS"
    assert gates["git_repository"]["status"] == "WARN"
    assert gates["baseline_tag"]["status"] == "WARN"
    assert payload["next_commands"] == [
        "python3 scripts/certify_release.py",
        "python3 scripts/certify_fresh_clone.py --ref main",
    ]

    assert main(["certify", "status", "--strict", "--format", "text"], cfg=cfg) == 2
    strict_output = capsys.readouterr().out
    assert "CERTIFICATION_FAIL" in strict_output
    assert "FAIL product_audit PRODUCT_AUDIT_OPEN" in strict_output
    assert "WARN git_repository git metadata unavailable" in strict_output
    assert "command=python3 scripts/certify_release.py" in strict_output


def test_cli_certify_status_includes_github_governance(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40
    tag_name = "v0.1.23-governance-status"

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, tag_name
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args[:3] == ["git", "ls-remote", "origin"]:
            return True, f"{'b' * 40}\trefs/tags/{tag_name}\n{head_sha}\trefs/tags/{tag_name}^{{}}"
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, json.dumps({"protected": False, "protection": {"required_status_checks": {"contexts": []}}})
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, json.dumps({"message": "Upgrade to GitHub Pro or make this repository public", "status": "403"})
        if args[:7] == ["gh", "run", "list", "--repo", "jotaele44/mucaro-ai", "--workflow", "CI"]:
            return True, json.dumps(
                [
                    {
                        "databaseId": 1,
                        "status": "completed",
                        "conclusion": "success",
                        "headSha": head_sha,
                        "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/1",
                        "name": "CI",
                        "event": "push",
                        "createdAt": "2026-09-02T00:00:00Z",
                        "updatedAt": "2026-09-02T00:00:30Z",
                    }
                ]
            )
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["certify", "status", "--include-github", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    gates = {gate["name"]: gate for gate in payload["gates"]}
    assert payload["status"] == "CERTIFICATION_FAIL"
    assert payload["product_audit_status"] == "PRODUCT_AUDIT_OPEN"
    assert gates["product_audit"]["status"] == "FAIL"
    assert payload["github"]["enabled"] is True
    assert payload["github"]["repo"] == "jotaele44/mucaro-ai"
    assert payload["github"]["workflow"] == "CI"
    assert payload["github"]["branch_protected"] is False
    assert payload["github"]["latest_ci"]["head_sha"] == head_sha
    assert gates["github_repo"]["status"] == "PASS"
    assert gates["github_branch_protection"]["status"] == "WARN"
    assert gates["github_branch_protection_api"]["status"] == "WARN"
    assert gates["github_latest_ci"]["status"] == "PASS"
    assert gates["github_tag_binding"]["status"] == "PASS"

    assert main(["certify", "status", "--include-github", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "github_repo=jotaele44/mucaro-ai" in text_output
    assert "github_branch_protected=False" in text_output
    assert "github_workflow=CI" in text_output
    assert f"PASS github_latest_ci success {head_sha}" in text_output


def test_cli_release_state_reports_github_governance_open(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    head_sha = "c" * 40

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, ""
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, json.dumps({"protected": False, "protection": {"required_status_checks": {"contexts": []}}})
        if args[:3] == ["gh", "api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, json.dumps({"message": "Upgrade to GitHub Pro or make this repository public", "status": "403"})
        if args[:7] == ["gh", "run", "list", "--repo", "jotaele44/mucaro-ai", "--workflow", "CI"]:
            return True, json.dumps(
                [
                    {
                        "databaseId": 7,
                        "status": "completed",
                        "conclusion": "startup_failure",
                        "headSha": head_sha,
                        "url": "https://github.com/jotaele44/mucaro-ai/actions/runs/7",
                        "name": "CI",
                        "event": "workflow_dispatch",
                        "createdAt": "2026-09-05T00:00:00Z",
                        "updatedAt": "2026-09-05T00:00:01Z",
                    }
                ]
            )
        if args == ["gh", "api", "repos/jotaele44/mucaro-ai/actions/runs/7/jobs"]:
            return True, json.dumps({"total_count": 0, "jobs": []})
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "state", "--include-github", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "GITHUB_GOVERNANCE_OPEN"
    assert payload["highest_local_state"] == "CANONICAL_LOCAL"
    assert payload["product_audit_status"] == "PRODUCT_AUDIT_OPEN"
    assert payload["package_status"] == "PACKAGE_WARN"
    assert states["CANONICAL_LOCAL"]["status"] == "PASS"
    assert states["LOCAL_RELEASE_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["FRESH_CLONE_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["SOURCE_ARTIFACT_PACKAGING"]["status"] == "PASS"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["PYTHON_DIST_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GITHUB_GOVERNANCE_OPEN"]["status"] == "OPEN"
    assert "github_hosted_runner_startup=FAIL" in states["GITHUB_GOVERNANCE_OPEN"]["evidence"]
    assert "github_branch_protection_api=WARN:HTTP 403: Upgrade to GitHub Pro" in states["GITHUB_GOVERNANCE_OPEN"]["evidence"]
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    assert main(["release", "state", "--include-github", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "RELEASE_STATE_OPEN" in text_output
    assert "current_state=GITHUB_GOVERNANCE_OPEN" in text_output
    assert "OPEN GITHUB_GOVERNANCE_OPEN" in text_output


def test_cli_release_state_uses_exact_head_certification_receipts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    head_sha = "d" * 40

    write_release_receipt(
        tmp_path,
        receipt_type="local_release",
        status="CERTIFY_RELEASE_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_release.py"],
        payload={"gates": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="fresh_clone",
        status="FRESH_CLONE_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_fresh_clone.py", "--ref", head_sha],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="source_artifact",
        status="SOURCE_ARTIFACT_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_source_artifact.py", "manifest.json"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="python_dist",
        status="PYTHON_DIST_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_python_distribution.py"],
        payload={"checks": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="fresh_clone",
        status="FRESH_CLONE_CERT_OK",
        head_sha="e" * 40,
        command=["python3", "scripts/certify_fresh_clone.py", "--ref", "e" * 40],
        payload={"checks": ["stale"]},
    )

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, ""
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "state", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert states["LOCAL_RELEASE_CERTIFIED"]["status"] == "PASS"
    assert states["FRESH_CLONE_CERTIFIED"]["status"] == "PASS"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "PASS"
    assert states["PYTHON_DIST_CERTIFIED"]["status"] == "PASS"
    assert payload["receipts"]["local_release"]["head_sha"] == head_sha
    assert payload["receipts"]["fresh_clone"]["head_sha"] == head_sha
    assert payload["receipts"]["source_artifact"]["head_sha"] == head_sha
    assert payload["receipts"]["python_dist"]["head_sha"] == head_sha


def test_cli_release_governance_report_summarizes_current_head_handoff(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    write_package_fixture(tmp_path)
    head_sha = "9" * 40
    tag_name = "v0.1.23-structured-startup-blocker"

    for receipt_type, status in [
        ("local_release", "CERTIFY_RELEASE_OK"),
        ("fresh_clone", "FRESH_CLONE_CERT_OK"),
        ("source_artifact", "SOURCE_ARTIFACT_CERT_OK"),
        ("python_dist", "PYTHON_DIST_CERT_OK"),
    ]:
        write_release_receipt(
            tmp_path,
            receipt_type=receipt_type,
            status=status,
            head_sha=head_sha,
            command=["mucaro", "test"],
            payload={"checks": []},
        )

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "status", "--short"]:
            return True, ""
        if args == ["git", "tag", "--points-at", "HEAD"]:
            return True, tag_name
        if args == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return True, "origin/main"
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        if args[:3] == ["git", "ls-remote", "origin"]:
            return True, f"{'a' * 40}\trefs/tags/{tag_name}\n{head_sha}\trefs/tags/{tag_name}^{{}}"
        return False, f"unexpected command: {args}"

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, {"protected": False, "protection": {"required_status_checks": {"contexts": []}}}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, None, "HTTP 403: Upgrade to GitHub Pro or make this repository public"
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions"]:
            return True, {"enabled": True, "allowed_actions": "all"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions/workflow"]:
            return True, {"default_workflow_permissions": "read"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runners"]:
            return True, {"total_count": 0, "runners": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/workflows"]:
            return True, {
                "workflows": [
                    {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
                    {"id": 2, "name": "CI Startup Probe", "path": ".github/workflows/ci-startup-probe.yml", "state": "active"},
                ]
            }, ""
        if args[:2] == ["run", "list"] and "CI Startup Probe" in args:
            return True, [{"databaseId": 22, "status": "completed", "conclusion": "startup_failure", "headSha": head_sha}], ""
        if args[:2] == ["run", "list"] and "CI" in args:
            return True, [{"databaseId": 11, "status": "completed", "conclusion": "startup_failure", "headSha": head_sha}], ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/11/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/22/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "users/jotaele44/settings/billing/actions"]:
            return False, None, "HTTP 404: Not Found"
        return False, None, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)

    assert main(["release", "governance-report", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "GOVERNANCE_REPORT_OPEN"
    assert payload["current_state"] == "GITHUB_GOVERNANCE_OPEN"
    assert payload["head_sha"] == head_sha
    assert payload["tags_at_head"] == [tag_name]
    assert payload["current_head_receipts_summary"]["total"] == 4
    assert payload["current_head_receipts_summary"]["valid"] == 4
    assert payload["startup_blocker"]["status"] == "FAIL"
    assert payload["startup_blocker"]["isolated_before_jobs"] is True
    assert "private_branch_protection_plan_gate" in payload["startup_blocker"]["suspected_causes"]
    assert "github_branch_protection_api=WARN:HTTP 403" in payload["github_governance_evidence"]

    assert main(["release", "governance-report", "--strict", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "GOVERNANCE_REPORT_OPEN" in text_output
    assert f"head_sha={head_sha}" in text_output
    assert "startup_blocker_status=FAIL" in text_output
    assert "startup_blocker_causes=" in text_output
    assert "receipt_count=4" in text_output
    assert "receipt_valid=4" in text_output
    assert "next_action=Upgrade GitHub plan or make the repository public" in text_output


def test_cli_release_state_requires_source_artifact_receipt_for_governed_ready(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    def fake_product_audit(_cfg: Settings):
        return {
            "status": "PRODUCT_AUDIT_READY",
            "requirements": [
                {"requirement_id": "source_artifact_packaging", "status": "PASS"},
                {"requirement_id": "github_release_governance", "status": "PASS"},
            ],
        }

    def fake_package_status(_root: Path):
        return {"status": "PACKAGE_READY"}

    def fake_certification_status(_cfg: Settings, *, include_github: bool = False, github_workflow: str = "CI"):
        del include_github, github_workflow
        return {
            "status": "CERTIFICATION_READY",
            "git": {"head_sha": head_sha},
            "gates": [
                {"name": "git_repository", "status": "PASS", "detail": "ok"},
                {"name": "worktree_clean", "status": "PASS", "detail": "clean"},
                {"name": "upstream", "status": "PASS", "detail": "origin/main"},
                {"name": "github_latest_ci", "status": "PASS", "detail": "success"},
                {"name": "github_branch_protection", "status": "PASS", "detail": "protected"},
                {"name": "github_tag_binding", "status": "PASS", "detail": "bound"},
            ],
        }

    monkeypatch.setattr(cli, "build_product_audit", fake_product_audit)
    monkeypatch.setattr(cli, "build_package_status", fake_package_status)
    monkeypatch.setattr(cli, "build_certification_status", fake_certification_status)

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_OPEN"
    assert payload["current_state"] == "SOURCE_ARTIFACT_CERTIFICATION_OPEN"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "UNVERIFIED"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "OPEN"

    write_release_receipt(
        tmp_path,
        receipt_type="source_artifact",
        status="SOURCE_ARTIFACT_CERT_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_source_artifact.py", "manifest.json"],
        payload={"checks": []},
    )

    assert main(["release", "state", "--include-github", "--strict", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    states = {row["state"]: row for row in payload["states"]}
    assert payload["status"] == "RELEASE_STATE_READY"
    assert payload["current_state"] == "GOVERNED_RELEASE_READY"
    assert states["SOURCE_ARTIFACT_CERTIFIED"]["status"] == "PASS"
    assert states["GOVERNED_RELEASE_READY"]["status"] == "PASS"


def test_cli_release_receipts_lists_and_shows_exact_head_receipts(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "f" * 40
    stale_sha = "1" * 40
    receipt_dir = tmp_path / "data" / "exports" / "release_receipts"

    current_receipt = write_release_receipt(
        tmp_path,
        receipt_type="local_release",
        status="CERTIFY_RELEASE_OK",
        head_sha=head_sha,
        command=["python3", "scripts/certify_release.py"],
        payload={"gates": []},
    )
    write_release_receipt(
        tmp_path,
        receipt_type="fresh_clone",
        status="FRESH_CLONE_CERT_OK",
        head_sha=stale_sha,
        command=["python3", "scripts/certify_fresh_clone.py", "--ref", stale_sha],
        payload={"checks": ["stale"]},
    )
    (receipt_dir / "broken.json").write_text("{not-json\n", encoding="utf-8")

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        return False, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)

    assert main(["release", "receipts", "--current-head", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_RECEIPTS_OK"
    assert payload["current_head"] == head_sha
    assert payload["count"] == 1
    assert payload["receipts"][0]["receipt_id"] == current_receipt.stem
    assert payload["receipts"][0]["valid"] is True
    assert payload["receipts"][0]["file_sha256"]
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["valid"] == 1
    assert payload["summary"]["invalid"] == 0

    assert main(["release", "receipt", current_receipt.stem, "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "RELEASE_RECEIPT_OK"
    assert payload["summary"]["receipt_id"] == current_receipt.stem
    assert payload["summary"]["valid"] is True
    assert payload["receipt"]["head_sha"] == head_sha

    assert main(["release", "receipt", "missing-receipt", "--format", "text"], cfg=cfg) == 2
    assert "ERROR_RELEASE_RECEIPT_NOT_FOUND" in capsys.readouterr().err

    assert main(["release", "receipt", "../outside", "--format", "text"], cfg=cfg) == 2
    assert "ERROR_RELEASE_RECEIPT_NOT_FOUND" in capsys.readouterr().err

    assert main(["release", "receipts", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["valid"] == 2
    assert payload["summary"]["invalid"] == 1
    broken = next(row for row in payload["receipts"] if row["receipt_id"] == "broken")
    assert broken["valid"] is False
    assert broken["validation_error"].startswith("json_invalid:")


def test_github_governance_fails_when_latest_ci_is_for_different_head(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40
    stale_sha = "c" * 40

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args[:2] == ["api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, {"protected": True, "protection": {"required_status_checks": {"contexts": ["validate"]}}}, ""
        if args[:2] == ["run", "list"]:
            return True, [{"status": "completed", "conclusion": "success", "headSha": stale_sha}], ""
        return False, None, f"unexpected command: {args}"

    def fake_find_remote_tag_commit(_cfg: Settings, tag_name: str, expected_head: str | None):
        assert tag_name == "v0.1.23-governance-status"
        assert expected_head == head_sha
        return True, f"{tag_name} -> {head_sha}"

    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)
    monkeypatch.setattr(cli, "_find_remote_tag_commit", fake_find_remote_tag_commit)

    _github, gates = cli.build_github_governance_status(
        cfg,
        repo="jotaele44/mucaro-ai",
        branch="main",
        head_sha=head_sha,
        tags_at_head=["v0.1.23-governance-status"],
        workflow_name="CI",
    )
    by_name = {gate["name"]: gate for gate in gates}
    assert by_name["github_branch_protection"]["status"] == "PASS"
    assert by_name["github_latest_ci"]["status"] == "FAIL"
    assert f"does not match HEAD {head_sha}" in by_name["github_latest_ci"]["detail"]


def test_github_governance_fails_when_current_head_ci_fails(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args[:2] == ["api", "repos/jotaele44/mucaro-ai/branches/main"]:
            return True, {"protected": True, "protection": {"required_status_checks": {"contexts": ["validate"]}}}, ""
        if args[:2] == ["run", "list"]:
            return True, [{"databaseId": 9, "status": "completed", "conclusion": "startup_failure", "headSha": head_sha}], ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/9/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        return False, None, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)

    github, gates = cli.build_github_governance_status(
        cfg,
        repo="jotaele44/mucaro-ai",
        branch="main",
        head_sha=head_sha,
        tags_at_head=[],
        workflow_name="CI",
    )
    by_name = {gate["name"]: gate for gate in gates}
    assert by_name["github_latest_ci"]["status"] == "FAIL"
    assert f"completed startup_failure {head_sha}" in by_name["github_latest_ci"]["detail"]
    assert by_name["github_hosted_runner_startup"]["status"] == "FAIL"
    assert github["latest_ci"]["jobs_count"] == 0
    assert github["latest_ci"]["startup_layer"] == "before_jobs"


def test_cli_certify_github_actions_isolates_startup_layer(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path)
    head_sha = "a" * 40

    def fake_command_output(_cfg: Settings, args: list[str]) -> tuple[bool, str]:
        if args == ["git", "branch", "--show-current"]:
            return True, "main"
        if args == ["git", "rev-parse", "HEAD"]:
            return True, head_sha
        if args == ["git", "remote", "get-url", "origin"]:
            return True, "https://github.com/jotaele44/mucaro-ai.git"
        return False, f"unexpected command: {args}"

    def fake_gh_json(_cfg: Settings, args: list[str]):
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions"]:
            return True, {"enabled": True, "allowed_actions": "all"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/permissions/workflow"]:
            return True, {"default_workflow_permissions": "read"}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/branches/main/protection"]:
            return False, None, "HTTP 403: Upgrade to GitHub Pro or make this repository public"
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runners"]:
            return True, {"total_count": 0, "runners": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/workflows"]:
            return True, {
                "workflows": [
                    {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
                    {
                        "id": 2,
                        "name": "CI Startup Probe",
                        "path": ".github/workflows/ci-startup-probe.yml",
                        "state": "active",
                    },
                ]
            }, ""
        if args[:2] == ["run", "list"] and "CI Startup Probe" in args:
            return True, [{"databaseId": 22, "status": "completed", "conclusion": "startup_failure", "headSha": head_sha}], ""
        if args[:2] == ["run", "list"] and "CI" in args:
            return True, [{"databaseId": 11, "status": "completed", "conclusion": "startup_failure", "headSha": head_sha}], ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/11/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "repos/jotaele44/mucaro-ai/actions/runs/22/jobs"]:
            return True, {"total_count": 0, "jobs": []}, ""
        if args == ["api", "users/jotaele44/settings/billing/actions"]:
            return False, None, "HTTP 404: requires user scope"
        return False, None, f"unexpected command: {args}"

    monkeypatch.setattr(cli, "_command_output", fake_command_output)
    monkeypatch.setattr(cli, "_gh_json", fake_gh_json)

    assert main(["certify", "github-actions", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "GITHUB_ACTIONS_DIAGNOSTIC_FAIL"
    assert payload["runs"]["ci"]["jobs_count"] == 0
    assert payload["runs"]["startup_probe"]["jobs_count"] == 0
    assert payload["startup_blocker"]["status"] == "FAIL"
    assert payload["startup_blocker"]["isolated_before_jobs"] is True
    assert "private_branch_protection_plan_gate" in payload["startup_blocker"]["suspected_causes"]
    assert checks["latest_ci"]["status"] == "FAIL"
    assert checks["startup_probe"]["status"] == "FAIL"
    assert checks["startup_layer_isolated"]["status"] == "FAIL"
    assert checks["branch_protection_api"]["status"] == "WARN"
    assert checks["self_hosted_runner_inventory"]["status"] == "PASS"
    assert checks["actions_billing"]["status"] == "WARN"
    assert payload["branch_protection"]["available"] is False
    assert payload["runner_inventory"]["self_hosted_total_count"] == 0

    assert main(["certify", "github-actions", "--format", "text"], cfg=cfg) == 2
    text_output = capsys.readouterr().out
    assert "GITHUB_ACTIONS_DIAGNOSTIC_FAIL" in text_output
    assert "startup_blocker_status=FAIL" in text_output
    assert "private_branch_protection_plan_gate" in text_output
    assert "WARN branch_protection_api HTTP 403: Upgrade to GitHub Pro" in text_output
    assert "PASS self_hosted_runner_inventory self_hosted_total_count=0" in text_output
    assert "FAIL startup_layer_isolated" in text_output
    assert "next_action=Upgrade GitHub plan or make the repository public" in text_output
    assert "next_action=Run `gh auth refresh -h github.com -s user`" in text_output


def test_cli_index_status_reports_missing_index(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["index", "status", "--format", "json"], cfg=cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INDEX_MISSING"
    assert payload["chunk_count"] == 0
    assert payload["stale"] is False
    assert payload["semantic_store"]["status"] == "SEMANTIC_STORE_MISSING"


def test_cli_sources_list_reindex_and_delete(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Local assistants need approved files and rebuildable indexes.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["sources", "list", "--format", "json"], cfg=cfg) == 0
    sources_payload = json.loads(capsys.readouterr().out)
    assert sources_payload["status"] == "SOURCES_OK"
    assert sources_payload["index"]["status"] == "INDEX_OK"
    assert sources_payload["index"]["semantic_store"]["status"] == "SEMANTIC_STORE_OK"
    source_id = sources_payload["sources"][0]["source_id"]

    assert main(["sources", "reindex", "--format", "text"], cfg=cfg) == 0
    reindex_output = capsys.readouterr().out
    assert "REINDEX_OK" in reindex_output
    assert "index_status=INDEX_OK" in reindex_output
    assert "semantic_store_status=SEMANTIC_STORE_OK" in reindex_output

    assert main(["sources", "delete", source_id, "--format", "json"], cfg=cfg) == 0
    delete_payload = json.loads(capsys.readouterr().out)
    assert delete_payload["status"] == "SOURCE_DELETE_OK"
    assert delete_payload["remaining_sources"] == 0

    assert main(["sources", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "SOURCES_OK" in list_output
    assert "sources=0" in list_output


def test_cli_conversations_ask_and_runs(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)
    input_dir = tmp_path / "data" / "input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.md"
    sample.write_text("Source lineage requires cited chunk IDs and source IDs.", encoding="utf-8")

    assert main(["ingest", "data/input", "--format", "json"], cfg=cfg) == 0
    capsys.readouterr()

    assert main(["conversations", "create", "--title", "Lineage", "--format", "json"], cfg=cfg) == 0
    created = json.loads(capsys.readouterr().out)
    conversation_id = created["conversation_id"]
    assert created["title"] == "Lineage"

    assert main(["conversations", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "CONVERSATIONS_OK" in list_output
    assert conversation_id in list_output

    assert (
        main(
            [
                "conversations",
                "ask",
                conversation_id,
                "What",
                "does",
                "source",
                "lineage",
                "require?",
                "--strategy",
                "persistent",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    asked = json.loads(capsys.readouterr().out)
    assert asked["answer"]["citation_status"] == "CITATION_OK"
    assert asked["conversation"]["conversation_id"] == conversation_id
    assert len(asked["conversation"]["turns"]) == 2
    run_id = asked["answer"]["run_id"]

    assert main(["runs", "summary", "--format", "json"], cfg=cfg) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "RUN_HISTORY_OK"
    assert summary["run_count"] == 1
    assert summary["latest_run_id"] == run_id

    assert main(["runs", "show", run_id, "--format", "text"], cfg=cfg) == 0
    run_output = capsys.readouterr().out
    assert "CITATION_OK" in run_output
    assert "persistent_tfidf_v1" in run_output

    assert main(["conversations", "rename", conversation_id, "Renamed", "Lineage", "--format", "json"], cfg=cfg) == 0
    renamed = json.loads(capsys.readouterr().out)
    assert renamed["title"] == "Renamed Lineage"

    assert main(["conversations", "delete", conversation_id, "--format", "json"], cfg=cfg) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["status"] == "CONVERSATION_DELETE_OK"

    assert main(["runs", "show", run_id, "--format", "json"], cfg=cfg) == 0
    preserved = json.loads(capsys.readouterr().out)
    assert preserved["run_id"] == run_id


def test_cli_conversation_and_run_missing_ids(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["conversations", "show", "conv_missing", "--format", "json"], cfg=cfg) == 2
    missing_conversation = json.loads(capsys.readouterr().out)
    assert missing_conversation["status"] == "ERROR_CONVERSATION_NOT_FOUND"

    assert main(["runs", "show", "run_missing", "--format", "json"], cfg=cfg) == 2
    missing_run = json.loads(capsys.readouterr().out)
    assert missing_run["status"] == "ERROR_RUN_NOT_FOUND"


def test_cli_tools_require_approval_and_audit_execution(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["tools", "list", "--format", "text"], cfg=cfg) == 0
    tools_output = capsys.readouterr().out
    assert "TOOLS_OK" in tools_output
    assert "source_library.status" in tools_output
    assert "memory.list" in tools_output

    assert (
        main(
            [
                "tools",
                "request",
                "source_library.status",
                "--reason",
                "readiness check",
                "--format",
                "json",
            ],
            cfg=cfg,
        )
        == 0
    )
    requested = json.loads(capsys.readouterr().out)
    tool_call_id = requested["tool_call"]["tool_call_id"]
    assert requested["tool_call"]["status"] == "PENDING_APPROVAL"

    assert main(["tools", "run", tool_call_id, "--format", "json"], cfg=cfg) == 2
    denied_run = json.loads(capsys.readouterr().err)
    assert denied_run["status"] == "ERROR_TOOL_APPROVAL_REQUIRED"

    assert main(["tools", "approve", tool_call_id, "--format", "json"], cfg=cfg) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["tool_call"]["status"] == "APPROVED"

    assert main(["tools", "run", tool_call_id, "--format", "json"], cfg=cfg) == 0
    executed = json.loads(capsys.readouterr().out)
    assert executed["status"] == "TOOL_CALL_EXECUTED"
    assert executed["result"]["status"] == "SOURCE_LIBRARY_STATUS_OK"

    assert main(["tools", "audit", "--format", "json"], cfg=cfg) == 0
    audit = json.loads(capsys.readouterr().out)
    assert [event["event"] for event in audit["events"]] == [
        "TOOL_CALL_REQUESTED",
        "TOOL_CALL_APPROVED",
        "TOOL_CALL_EXECUTED",
    ]


def test_cli_memory_controls_and_export(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["memory", "remember", "Prefer", "concise", "answers.", "--kind", "preference", "--format", "json"], cfg=cfg) == 0
    saved = json.loads(capsys.readouterr().out)
    memory_id = saved["memory"]["memory_id"]
    assert saved["status"] == "MEMORY_SAVED"
    assert saved["memory"]["enabled"] is True

    assert main(["memory", "list", "--format", "text"], cfg=cfg) == 0
    list_output = capsys.readouterr().out
    assert "MEMORY_OK" in list_output
    assert "enabled=1" in list_output
    assert memory_id in list_output

    assert main(["memory", "disable", memory_id, "--format", "json"], cfg=cfg) == 0
    disabled = json.loads(capsys.readouterr().out)
    assert disabled["memory"]["enabled"] is False

    assert main(["memory", "list", "--active-only", "--format", "json"], cfg=cfg) == 0
    active_only = json.loads(capsys.readouterr().out)
    assert active_only["memory_count"] == 0

    assert main(["memory", "enable", memory_id, "--format", "json"], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["memory"]["enabled"] is True

    assert main(["memory", "export", "--format", "json"], cfg=cfg) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["status"] == "MEMORY_EXPORT_OK"
    assert exported["memory_count"] == 1

    assert main(["memory", "delete", memory_id, "--format", "json"], cfg=cfg) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["status"] == "MEMORY_DELETE_OK"
    assert deleted["remaining_memories"] == 0


def test_cli_security_status_and_serve_public_bind_guard(tmp_path: Path, capsys, monkeypatch):
    cfg = make_settings(tmp_path, operator_token="x" * 32)

    assert main(["security", "status", "--host", "127.0.0.1", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "SECURITY_OK"

    assert main(["security", "status", "--host", "0.0.0.0", "--format", "text"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "SECURITY_FAIL" in output
    assert "bind_host" in output

    called = False

    def fake_run(*args, **kwargs) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["serve", "--host", "0.0.0.0"], cfg=cfg) == 2
    assert called is False


def test_cli_serve_passes_host_port_and_reload(tmp_path: Path, monkeypatch):
    cfg = make_settings(tmp_path)
    captured = {}

    def fake_run(app: str, *, host: str, port: int, reload: bool) -> None:
        captured.update({"app": app, "host": host, "port": port, "reload": reload})

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert main(["serve", "--host", "127.0.0.1", "--port", "8123", "--reload"], cfg=cfg) == 0
    assert captured == {
        "app": "backend.app.main:app",
        "host": "127.0.0.1",
        "port": 8123,
        "reload": True,
    }


def test_cli_serve_rejects_invalid_port(capsys):
    try:
        main(["serve", "--port", "70000"])
    except SystemExit as exc:
        assert exc.code == 2
    error_output = capsys.readouterr().err
    assert "PORT_RANGE:1-65535" in error_output


def test_cli_ingest_missing_path_returns_structured_error(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ingest", "missing.md", "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_INPUT_PATH_NOT_FOUND" in error_output
    assert "missing.md" in error_output


def test_cli_ingest_external_path_requires_permission(tmp_path: Path, capsys):
    project = tmp_path / "project"
    cfg = make_settings(project)
    external = tmp_path / "external"
    external.mkdir()
    sample = external / "sample.md"
    sample.write_text("External directory access is permission-gated.", encoding="utf-8")

    assert main(["ingest", str(external), "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_DIRECTORY_PERMISSION_REQUIRED" in error_output

    assert main(["permissions", "grant", str(external), "--yes", "--format", "json"], cfg=cfg) == 0
    grant_output = capsys.readouterr().out
    assert "DIRECTORY_PERMISSION_GRANTED" in grant_output

    assert main(["ingest", str(external), "--format", "json"], cfg=cfg) == 0
    ingest_output = capsys.readouterr().out
    assert "INGEST_FOLDER_OK" in ingest_output
    assert "PERMISSION_OK" in ingest_output


def test_cli_permission_grant_requires_confirmation_when_not_yes(tmp_path: Path, capsys):
    project = tmp_path / "project"
    cfg = make_settings(project)
    external = tmp_path / "external"
    external.mkdir()

    assert main(["permissions", "grant", str(external), "--format", "json"], cfg=cfg) == 2
    error_output = capsys.readouterr().err
    assert "ERROR_PERMISSION_CONFIRMATION_REQUIRED" in error_output


def test_cli_ask_no_sources_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    assert main(["ask", "What is source lineage?", "--format", "json", "--no-persist"], cfg=cfg) == 2
    output = capsys.readouterr().out
    assert "ERROR_NO_SOURCES" in output


def test_cli_ask_invalid_top_k_returns_error_status(tmp_path: Path, capsys):
    cfg = make_settings(tmp_path)

    try:
        main(["ask", "What is source lineage?", "--top-k", "0", "--format", "json"], cfg=cfg)
    except SystemExit as exc:
        assert exc.code == 2
    error_output = capsys.readouterr().err
    assert "TOP_K_RANGE:1-20" in error_output
