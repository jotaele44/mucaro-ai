"""MOB-F19 receipt logic, against a stub GitHub CLI: the live run in CI uses the real one."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("certify_mob_f19_live", ROOT / "scripts" / "certify_mob_f19_live.py")
script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(script)

PR = {"number": 7, "title": "Fix", "body": "b", "state": "open", "draft": False, "html_url": "https://github.com/o/r/pull/7",
      "user": {"login": "u"}, "head": {"ref": "fix", "sha": "a" * 40}, "base": {"ref": "main"}}


def _stub_gh(tmp_path: Path, monkeypatch, detail: dict) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    (tmp_path / "detail.json").write_text(json.dumps(detail), encoding="utf-8")
    (tmp_path / "list.json").write_text(json.dumps([PR]), encoding="utf-8")
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "path = next(a for a in sys.argv[1:] if a.startswith('repos/'))\n"
        f"name = 'detail.json' if path.endswith('/7') else 'list.json'\n"
        f"print(open({str(tmp_path)!r} + '/' + name).read())\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_mob_f19_receipt_passes_when_detail_matches_its_list_row(tmp_path: Path, monkeypatch) -> None:
    _stub_gh(tmp_path, monkeypatch, PR)
    receipt = script.certify("o/r", "all", tmp_path / "work", head_sha="b" * 40)
    assert receipt["status"] == script.STATUS_OK, receipt["checks"]
    assert receipt["pr_number"] == 7 and receipt["checks"]["unapproved_call_refused"] is True


@pytest.mark.parametrize("field", ["title", "head"])
def test_mob_f19_receipt_fails_when_detail_disagrees_with_the_list(tmp_path: Path, monkeypatch, field: str) -> None:
    changed = {**PR, field: "Other title" if field == "title" else {"ref": "fix", "sha": "c" * 40}}
    _stub_gh(tmp_path, monkeypatch, changed)
    receipt = script.certify("o/r", "all", tmp_path / "work", head_sha="b" * 40)
    assert receipt["status"] == script.STATUS_FAIL
    assert receipt["checks"]["detail_matches_list_row"] is False
