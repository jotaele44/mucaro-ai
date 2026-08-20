from pathlib import Path

from backend.app.validation import validate_schema_file


def test_all_schema_files_valid():
    root = Path(__file__).resolve().parents[2]
    for schema in (root / "schemas").glob("*.schema.json"):
        assert validate_schema_file(schema)["status"] == "SCHEMA_OK"
