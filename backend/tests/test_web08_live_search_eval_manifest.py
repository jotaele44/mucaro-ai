import json
from pathlib import Path

P=Path(__file__).resolve().parents[2]/"_architecture"/"web08_live_search_eval_manifest.json"

def test_web08_manifest_closes_denominator_and_preserves_blocked_state():
    data=json.loads(P.read_text(encoding="utf-8"))
    cases=data["cases"]
    assert data["requirement_id"]=="WEB-08"
    assert data["denominator_state"]=="OPEN"
    assert len(cases)==6
    assert len({c["id"] for c in cases})==len(cases)
    by_class={c["class"]:c for c in cases}
    assert by_class["CREDENTIAL_MISSING"]["expected"]=="BLOCKED"
    assert by_class["EMPTY_RESULTS"]["expected"]=="PASS"
    assert "zero_not_source_absence" in by_class["EMPTY_RESULTS"]["requirements"]
    assert any("live credentialed evidence" in x for x in data["invariants"])
