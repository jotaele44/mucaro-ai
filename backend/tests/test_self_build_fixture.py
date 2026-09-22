from pathlib import Path
from backend.app.self_build_fixture import governed_fixture_cycle

def test_repair_fixture_keeps_strict_improvement(tmp_path: Path):
    p=tmp_path/"probe.py"; p.write_text("BROKEN\n")
    r=governed_fixture_cycle(p,b"FIXED\n",before_failures={"TEST_FAILURE"},after_failures=set(),targeted_pass=True,full_pass=True)
    assert r["decision"]=="KEEP"
    assert p.read_text()=="FIXED\n"

def test_harmful_repair_rolls_back_exact_preimage(tmp_path: Path):
    p=tmp_path/"probe.py"; p.write_bytes(b"ORIGINAL\n")
    r=governed_fixture_cycle(p,b"HARMFUL\n",before_failures={"TEST_FAILURE"},after_failures={"TEST_FAILURE","TYPE_FAILURE"},targeted_pass=True,full_pass=False)
    assert r["decision"]=="ROLLBACK_REGRESSION"
    assert r["restored"] is True
    assert r["postimage_sha256"]==r["preimage_sha256"]
    assert p.read_bytes()==b"ORIGINAL\n"
