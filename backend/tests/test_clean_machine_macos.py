from pathlib import Path
import subprocess

import pytest

import backend.app.clean_machine_macos as clean
from backend.app.clean_machine_macos import CleanMachineMacOSError, certify_clean_machine_macos


def test_clean_machine_requires_macos(tmp_path: Path) -> None:
    with pytest.raises(CleanMachineMacOSError) as exc:
        certify_clean_machine_macos(tmp_path/"current.app",tmp_path/"previous.app",expected_head="a"*40,install_root=tmp_path/"install",platform_name="linux")
    assert exc.value.status == "ERROR_CLEAN_MACHINE_PLATFORM"


def test_distinct_version_flow_emits_all_required_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current=tmp_path/"current.app"; previous=tmp_path/"previous.app"
    for app,head in ((current,"a"*40),(previous,"b"*40)):
        (app/"Contents/Resources").mkdir(parents=True)
        (app/"Contents/Resources/portable-manifest.json").write_text('{"head_sha":"'+head+'"}',encoding="utf-8")
    monkeypatch.setattr(clean,"verify_portable_macos_app",lambda app,expected_head=None:{"status":"PORTABLE_APP_VERIFIED"})
    calls=[]
    def install(app,*,install_root):
        head="a"*40 if Path(app)==current else "b"*40
        installed=Path(install_root)/"Mucaro AI.app"
        (installed/"Contents/MacOS").mkdir(parents=True,exist_ok=True)
        (installed/"Contents/Resources/app/scripts").mkdir(parents=True,exist_ok=True)
        (installed/"Contents/Resources/runtime/bin").mkdir(parents=True,exist_ok=True)
        calls.append(("install",head))
        return {"status":"PORTABLE_INSTALL_OK","installation":{"app_path":str(installed),"head_sha":head}}
    monkeypatch.setattr(clean,"install_portable_macos_app",install)
    monkeypatch.setattr(clean,"rollback_portable_macos_app",lambda *,install_root:{"status":"PORTABLE_ROLLBACK_OK","installation":{"head_sha":"b"*40}})
    runner_calls=[]
    def runner(command,cwd,env):
        runner_calls.append((command,cwd,env))
        return subprocess.CompletedProcess(command,0,stdout="ok")
    receipt=certify_clean_machine_macos(current,previous,expected_head="a"*40,install_root=tmp_path/"install",platform_name="darwin",runner=runner)
    assert receipt["status"] == "CLEAN_MACHINE_MACOS_VERIFIED"
    assert all(receipt["evidence"].values())
    assert calls == [("install","b"*40),("install","a"*40),("install","a"*40)]
    workflow_command, _, workflow_env = runner_calls[1]
    assert workflow_command[1:3] == ["-s", "-P"]
    assert workflow_env["PYTHONNOUSERSITE"] == "1"
    assert workflow_env["PYTHONSAFEPATH"] == "1"
    assert all(not key.startswith("PYTHON") or key in {"PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONSAFEPATH", "PYTHONDONTWRITEBYTECODE"} for key in workflow_env)


def test_same_head_previous_version_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current=tmp_path/"current.app"; previous=tmp_path/"previous.app"
    for app in (current,previous):
        (app/"Contents/Resources").mkdir(parents=True)
        (app/"Contents/Resources/portable-manifest.json").write_text('{"head_sha":"'+("a"*40)+'"}',encoding="utf-8")
    monkeypatch.setattr(clean,"verify_portable_macos_app",lambda app,expected_head=None:{"status":"PORTABLE_APP_VERIFIED"})
    with pytest.raises(CleanMachineMacOSError) as exc:
        certify_clean_machine_macos(current,previous,expected_head="a"*40,install_root=tmp_path/"install",platform_name="darwin")
    assert exc.value.status == "ERROR_CLEAN_MACHINE_PREVIOUS_HEAD"
