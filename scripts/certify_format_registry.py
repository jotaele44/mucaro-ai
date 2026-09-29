from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from backend.app.format_registry import DEFAULT_FORMAT_REGISTRY, query_format_registry, validate_format_registry
from backend.app.parser import ParseError, parse_file

STATUS_OK = "FORMAT_REGISTRY_CERT_OK"
STATUS_FAILED = "FORMAT_REGISTRY_CERT_FAILED"


def _unsupported_probe(suffix: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="mucaro-format-probe-") as temp_dir:
        path = Path(temp_dir) / f"probe{suffix}"
        path.write_text("plain text bytes must not bypass an undeclared suffix", encoding="utf-8")
        try:
            parse_file(path)
        except ParseError as exc:
            return exc.status == "ERROR_UNSUPPORTED_FORMAT"
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the declared Mucaro format registry and parser boundary.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    validation = validate_format_registry(DEFAULT_FORMAT_REGISTRY)
    json_unspecified = query_format_registry(".json")
    json_core = query_format_registry(".json", surface="core_ingestion")
    json_gis = query_format_registry(".json", surface="gis_vector")
    unsupported_xlsx = query_format_registry(".xlsx", surface="core_ingestion")
    unknown_abc = query_format_registry(".abc", surface="core_ingestion")
    invariants = {
        "registry_validation_ok": validation["status"] == "FORMAT_REGISTRY_OK",
        "registry_invariants_closed": all(validation.get("invariants", {}).values()),
        "json_requires_surface_disambiguation": json_unspecified["status"] == "FORMAT_REGISTRY_AMBIGUOUS",
        "json_core_supported": json_core.get("state") == "SUPPORTED",
        "json_gis_supported": json_gis.get("state") == "SUPPORTED",
        "xlsx_declared_unsupported": unsupported_xlsx.get("state") == "UNSUPPORTED",
        "unknown_extension_unsupported": unknown_abc.get("status") == "FORMAT_UNSUPPORTED",
        "xlsx_parser_fails_closed": _unsupported_probe(".xlsx"),
        "unknown_parser_fails_closed": _unsupported_probe(".abc"),
    }
    result = {
        "schema_version": "mucaro-format-registry-cert/1.0",
        "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "registry_sha256": validation.get("registry_sha256"),
        "counts": validation.get("counts"),
        "source_denominators": validation.get("source_denominators"),
        "queries": {
            "json_unspecified": json_unspecified,
            "json_core": json_core,
            "json_gis": json_gis,
            "xlsx_core": unsupported_xlsx,
            "abc_core": unknown_abc,
        },
        "invariants": invariants,
        "failures": validation.get("failures", []),
        "limitations": [
            "The registry is surface-specific; equal suffix names do not establish cross-surface capability identity.",
            "SUPPORTED means the bounded capability listed in the registry, not arbitrary semantics for every file carrying that suffix.",
            "PARTIALLY_SUPPORTED rows retain their declared limitations and must not be promoted by parser success alone.",
            "Rasterio may technically open formats not declared in this registry; undeclared capability is not release support.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(result["status"])
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
