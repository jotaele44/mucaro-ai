#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ocr_corpus_eval import (
    DEFAULT_OCR_CORPUS_MANIFEST,
    OCR_CORPUS_READY,
    build_ocr_corpus_evaluation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the frozen bounded Mucaro OCR corpus.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OCR_CORPUS_MANIFEST, help="Frozen OCR corpus denominator manifest.")
    parser.add_argument("--tesseract-path", help="Explicit path to the local tesseract executable.")
    parser.add_argument("--renderer-path", help="Explicit path to the local pdftoppm executable.")
    parser.add_argument("--strict-quality", action="store_true", help="Fail unless no provisional quality residue remains.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format.")
    args = parser.parse_args()
    command = [sys.executable, "scripts/certify_ocr_corpus.py"]
    if args.manifest != DEFAULT_OCR_CORPUS_MANIFEST:
        command.extend(["--manifest", str(args.manifest)])
    if args.tesseract_path:
        command.extend(["--tesseract-path", args.tesseract_path])
    if args.renderer_path:
        command.extend(["--renderer-path", args.renderer_path])
    if args.strict_quality:
        command.append("--strict-quality")
    report = build_ocr_corpus_evaluation(
        ROOT,
        manifest_path=args.manifest,
        tesseract_path=args.tesseract_path,
        renderer_path=args.renderer_path,
        command=command,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(report["status"])
        print(f"head_sha={report.get('head_sha')}")
        print(f"quality_state={report.get('quality_state')}")
        print(f"accuracy_certified={report.get('accuracy_certified')}")
        print(f"case_count={report.get('metrics', {}).get('case_count')}")
        print(f"token_recall_mean={report.get('metrics', {}).get('required_token_recall_mean')}")
        print(f"receipt={report.get('receipt')}")
        print(f"report={report.get('report_path')}")
        print(f"evidence={report.get('evidence_path')}")
        for failure in report.get("quality_failures", []):
            print(f"QUALITY_RESIDUE {failure.get('id')} flags={','.join(failure.get('flags', []))}")
    if report["status"] != OCR_CORPUS_READY:
        return 2
    return 2 if args.strict_quality and report.get("accuracy_certified") is not True else 0


if __name__ == "__main__":
    raise SystemExit(main())
