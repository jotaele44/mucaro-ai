"""CLI remains inert, offline, and subject to the existing directory-grant boundary."""
import hashlib
import json
from pathlib import Path

from backend.app.plugin_cli import main
from backend.tests.test_plugin_intake import package, settings


def test_cli_stages_then_lists_disabled_catalog(tmp_path, capsys):
    cfg = settings(tmp_path)
    archive = tmp_path / 'candidate.zip'
    archive.write_bytes(package())
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert main(['stage', str(archive), '--expected-sha256', sha], cfg=cfg) == 0
    stage = json.loads(capsys.readouterr().out)
    assert stage['status'] == 'PLUGIN_STAGED_DISABLED' and stage['plugin']['archive_sha256'] == sha
    assert main(['list'], cfg=cfg) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog['count'] == 1 and catalog['active_count'] == 0


def test_cli_external_path_requires_grant_before_file_read(tmp_path, capsys):
    cfg = settings(tmp_path / 'project')
    ungranted = tmp_path / 'outside.zip'
    ungranted.write_bytes(package())
    assert main(['stage', str(ungranted)], cfg=cfg) == 2
    error = json.loads(capsys.readouterr().err)
    assert error['status'] == 'ERROR_DIRECTORY_PERMISSION_REQUIRED'
    assert main(['list'], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)['count'] == 0


def test_cli_invalid_hash_does_not_stage(tmp_path, capsys):
    cfg = settings(tmp_path)
    source = tmp_path / 'candidate.zip'
    source.write_bytes(package())
    assert main(['stage', str(source), '--expected-sha256', '0' * 64], cfg=cfg) == 2
    assert json.loads(capsys.readouterr().err)['status'] == 'ERROR_PLUGIN_SOURCE_HASH_MISMATCH'


def test_cli_missing_path_structured_failure(tmp_path, capsys):
    cfg = settings(tmp_path)
    assert main(['stage', str(tmp_path / 'absent.zip')], cfg=cfg) == 2
    assert json.loads(capsys.readouterr().err)['status'] == 'ERROR_PLUGIN_INPUT_IO'
