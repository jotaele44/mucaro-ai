"""Compare explicit frozen observations; source text never becomes execution authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .artifact_identity import create_artifact_record
from .config import Settings
from .injection_guard import scan_text
from .utils import stable_id
from .web_capture_store import read_verified_capture

MAX_TEXT_CHARS = 200_000
MAX_PASSAGES = 1000
PASSAGE_CHARS = 2000
RESEARCH_POLICY = {
    "capture_count": 2,
    "max_text_chars_per_source": MAX_TEXT_CHARS,
    "max_passages_per_source": MAX_PASSAGES,
    "passage_chars": PASSAGE_CHARS,
    "selected_passages_per_source": 2,
    "source_policy": "indexed_capture_id_and_full_sha256_v1",
    "parser": "strict_charset_visible_html_v1",
    "comparison": "whitespace_collapsed_passage_multiset_v1",
    "retrieval": "lexical_overlap_then_exclusive_then_multiplicity_then_source_order_all_primary_ties_retained_v1",
    "citation_policy": "exact_selected_passage_ids_and_extraction_offsets_v1",
    "automatic_acquisition": False,
    "model_fallback": False,
    "accounting_enforcement": "explicit_nonnegative_integer_multiset_conservation_v1",
    "provider_prompt_budget": "UTF8_byte_upper_estimate_plus_output_and_512_overhead_v1",
    "accepted_http_statuses": "200-299",
    "accepted_mime_types": ["application/json", "application/xhtml+xml", "text/html", "text/markdown", "text/plain"],
    "accepted_encodings": ["utf-8", "utf-8-sig", "ascii", "iso8859-1", "cp1252", "utf-16", "utf-16-le", "utf-16-be"],
    "html_blocked_tags": ["head", "script", "style", "template", "noscript"],
    "html_block_boundaries": ["p", "div", "li", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"],
    "lexical_stopwords": "a an and are as at be by can compare comparison do does for from how in is it of on or selected source sources the these this to was what when where which who with",
}
RESEARCH_ARGUMENTS_SCHEMA = {
    "type": "object",
    "title": "Compare frozen web sources",
    "properties": {
        **{
            f"{side}_id": {
                "type": "string",
                "pattern": "^websnap_[0-9a-f]{16}$",
                "title": f"Source {side.upper()} capture ID",
            }
            for side in ("a", "b")
        },
        **{
            f"{side}_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "title": f"Source {side.upper()} SHA-256"}
            for side in ("a", "b")
        },
        "question": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "pattern": r"\S",
            "default": "Compare these sources.",
            "title": "Research question",
        },
        "synthesis": {
            "type": "string",
            "enum": ["extractive", "mock", "openai_compatible_local", "anthropic"],
            "default": "extractive",
            "title": "Synthesis mode",
        },
    },
    "required": ["a_id", "a_sha256", "b_id", "b_sha256"],
    "additionalProperties": False,
}
LIMITATIONS = [
    "Scope is these two frozen observations, not the current web or an exhaustive search.",
    "Byte identity, extracted-text identity, normalized passage overlap, and real-world identity are distinct.",
    "Whitespace normalization is a comparison view only; raw bytes and exact extracted excerpts are retained.",
    "Lexical ranking is discovery, not a semantic relevance or truth judgment; all boundary ties are recorded.",
    "Differing passages may be additions, omissions, or contradictions. Their truth and canonical identity remain UNRESOLVED.",
    "Citation validation checks references and exact excerpts, not the truth or completeness of model claims.",
    "HTML extraction excludes head, script, style, template, and noscript content; it does not execute JavaScript or infer visual layout.",
]


class ResearchError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def generate_answer(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Provider-native planning imports the skill registry; defer that edge until invocation.
    from .model_router import generate_answer as provider_answer

    return provider_answer(*args, **kwargs)


def _check_prompt_budget(query: str, selected: list[dict[str, Any]], mode: str) -> None:
    if mode == "mock":
        return
    from .model_router import (
        ANTHROPIC_MAX_OUTPUT_TOKENS,
        MODEL_PROVIDER_SPECS,
        build_grounded_prompt,
        local_model_settings,
    )

    context = (
        local_model_settings()["context_tokens"]
        if mode == "openai_compatible_local"
        else MODEL_PROVIDER_SPECS[mode].context_tokens
    )
    output_reserve = 900 if mode == "openai_compatible_local" else ANTHROPIC_MAX_OUTPUT_TOKENS
    system, user = build_grounded_prompt(query, selected, [], [])
    prompt_bytes = len((system + user).encode("utf-8"))
    if prompt_bytes + output_reserve + 512 > context:
        raise ResearchError(
            "ERROR_RESEARCH_CONTEXT_TOO_LARGE",
            "The selected excerpts exceed the provider's conservative context budget. Use smaller captures or increase the configured local context; no model request was sent.",
        )


class _VisibleHTML(HTMLParser):
    """Preserve decoded text nodes and explicit block boundaries, without executing content."""

    blocked = set(RESEARCH_POLICY["html_blocked_tags"])
    blocks = set(RESEARCH_POLICY["html_block_boundaries"])

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.stack.append(tag)
        if not self.stack and tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[: len(self.stack) - self.stack[::-1].index(tag) - 1]
        if not self.stack and tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, text):
        if not self.stack:
            self.parts.append(text)


def _extract(snapshot: dict[str, Any], content: bytes) -> tuple[str, str, str]:
    header = Message()
    header["content-type"] = snapshot["content_type"]
    mime = header.get_content_type()
    if mime not in RESEARCH_POLICY["accepted_mime_types"]:
        raise ResearchError(
            "ERROR_RESEARCH_UNSUPPORTED_FORMAT",
            "Compare HTML, plain text, Markdown, or JSON captures. Process other formats in their document workspace.",
        )
    if not 200 <= snapshot["http_status"] < 300:
        raise ResearchError(
            "ERROR_RESEARCH_HTTP_STATUS",
            "This observation is an HTTP error or redirect, not an accepted source document.",
        )
    encoding = header.get_content_charset() or snapshot.get("charset") or "utf-8"
    # Limit supported decoders to character encodings, not Python transformation codecs.
    import codecs

    try:
        codec = codecs.lookup(encoding)
        if codec.name not in RESEARCH_POLICY["accepted_encodings"]:
            raise LookupError("unsupported character encoding")
        decoded = content.decode(encoding, errors="strict")
    except (UnicodeError, LookupError, TypeError) as exc:
        raise ResearchError(
            "ERROR_RESEARCH_ENCODING_INVALID",
            "The declared text encoding is unsupported or cannot decode these bytes exactly. No replacement characters were inserted.",
        ) from exc
    if len(decoded) > MAX_TEXT_CHARS:
        raise ResearchError(
            "ERROR_RESEARCH_TOO_LARGE",
            "A source exceeds 200,000 decoded characters. Select a smaller explicit capture; comparison was not truncated.",
        )
    if mime in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleHTML()
        parser.feed(decoded)
        parser.close()
        text = "".join(parser.parts)
    else:
        text = decoded
    if len(text) > MAX_TEXT_CHARS:
        raise ResearchError("ERROR_RESEARCH_TOO_LARGE", "Extracted text exceeds the comparison budget.")
    return text, mime, codec.name


def _passages(text: str, snapshot_id: str) -> list[dict[str, Any]]:
    result = []
    # Offsets refer to this exact extracted Unicode string, not HTML byte positions.
    for match in re.finditer(r"[^\r\n]+", text):
        for start in range(match.start(), match.end(), PASSAGE_CHARS):
            end = min(start + PASSAGE_CHARS, match.end())
            raw = text[start:end]
            normalized = " ".join(raw.split())
            if not normalized:
                continue
            result.append(
                {
                    "chunk_id": stable_id("webpassage", snapshot_id, str(start), str(end), raw),
                    "source_id": snapshot_id,
                    "start": start,
                    "end": end,
                    "text": raw,
                    "normalized": normalized,
                }
            )
            if len(result) > MAX_PASSAGES:
                raise ResearchError(
                    "ERROR_RESEARCH_TOO_LARGE", "A source exceeds 1,000 passages. No passages were silently discarded."
                )
    return result


_STOP = set(RESEARCH_POLICY["lexical_stopwords"].split())


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[^\W_]+", text.casefold()) if term not in _STOP}


def _select(passages: list[dict[str, Any]], question: str, difference_priority: dict[str, int]) -> dict[str, Any]:
    terms = _terms(question)
    scored = [(len(terms & _terms(row["text"])), index, row) for index, row in enumerate(passages)]
    candidates = sorted(
        (row for row in scored if row[0] or not terms),
        key=lambda row: (-row[0], -difference_priority.get(row[2]["normalized"], 0), row[1]),
    )
    chosen = candidates[:2]
    cutoff = chosen[-1][0] if chosen else None
    return {
        "selected_ids": [row[2]["chunk_id"] for row in chosen],
        "candidate_ids": [row[2]["chunk_id"] for row in candidates],
        "boundary_tie_ids": [row[2]["chunk_id"] for row in candidates if row[0] == cutoff],
        "selection_truncated": len(candidates) > len(chosen),
        "basis": "LEXICAL_DISCOVERY_WITH_DIFFERENCE_TIEBREAK" if terms else "DIFFERENCE_THEN_SOURCE_ORDER_OVERVIEW",
    }


def _validate_accounting(counts: dict[str, int]) -> None:
    fields = {"a", "b", "intersection", "a_only", "b_only", "union", "symmetric_difference"}
    if (
        set(counts) != fields
        or any(type(value) is not int or value < 0 for value in counts.values())
        or counts["a"] != counts["intersection"] + counts["a_only"]
        or counts["b"] != counts["intersection"] + counts["b_only"]
        or counts["symmetric_difference"] != counts["a_only"] + counts["b_only"]
        or counts["union"] != counts["intersection"] + counts["symmetric_difference"]
    ):
        raise ResearchError(
            "ERROR_RESEARCH_ACCOUNTING_INVALID",
            "Passage accounting does not conserve every occurrence. No synthesis was produced; preserve the selected observations for inspection.",
        )


def _comparison(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    ca, cb = Counter(row["normalized"] for row in a), Counter(row["normalized"] for row in b)
    common, a_only, b_only, union = ca & cb, ca - cb, cb - ca, ca | cb
    counts = {
        "a": sum(ca.values()),
        "b": sum(cb.values()),
        "intersection": sum(common.values()),
        "a_only": sum(a_only.values()),
        "b_only": sum(b_only.values()),
        "union": sum(union.values()),
        "symmetric_difference": sum(a_only.values()) + sum(b_only.values()),
    }
    _validate_accounting(counts)
    return {
        "basis": RESEARCH_POLICY["comparison"],
        "counts": counts,
        "groups": [
            {
                "normalized": value,
                "a_occurrences": [row["chunk_id"] for row in a if row["normalized"] == value],
                "b_occurrences": [row["chunk_id"] for row in b if row["normalized"] == value],
                "intersection_count": common[value],
                "a_only_count": a_only[value],
                "b_only_count": b_only[value],
            }
            for value in sorted(union)
        ],
        "contradiction_status": "UNRESOLVED" if a_only or b_only else "NO_TEXT_DIFFERENCE",
    }


def compare_captures(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    question = arguments.get("question", "Compare these sources.")
    mode = arguments.get("synthesis", "extractive")
    if arguments["a_id"] == arguments["b_id"]:
        raise ResearchError(
            "ERROR_RESEARCH_DUPLICATE_SOURCE",
            "Choose two distinct frozen observations. The same source is not independent corroboration.",
        )
    sources, frozen, all_passages, selections = [], [], [], []
    for side in ("a", "b"):
        raw, snapshot, content = read_verified_capture(arguments[f"{side}_id"], arguments[f"{side}_sha256"], cfg)
        text, mime, encoding = _extract(snapshot, content)
        passages = _passages(text, snapshot["snapshot_id"])
        sources.append(
            {
                "side": side.upper(),
                "snapshot_id": snapshot["snapshot_id"],
                "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
                "content_sha256": snapshot["content_sha256"],
                "content_size_bytes": len(content),
                "requested_url": snapshot["requested_url"],
                "final_url": snapshot["final_url"],
                "retrieval_utc": snapshot["retrieval_utc"],
                "mime_type": mime,
                "encoding": encoding,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "extracted_char_count": len(text),
                "passage_count": len(passages),
                "injection_scan": scan_text(text),
            }
        )
        frozen.append(raw)
        all_passages.append(passages)
    comparison = _comparison(*all_passages)
    difference_priority = {
        group["normalized"]: 2
        if not group["a_occurrences"] or not group["b_occurrences"]
        else 1
        if group["a_only_count"] or group["b_only_count"]
        else 0
        for group in comparison["groups"]
    }
    selections = [_select(passages, question, difference_priority) for passages in all_passages]
    comparison["identity"] = {
        "snapshot_bytes_equal": frozen[0] == frozen[1],
        "http_content_equal": sources[0]["content_sha256"] == sources[1]["content_sha256"],
        "extracted_text_equal": sources[0]["text_sha256"] == sources[1]["text_sha256"],
        "canonical_identity": "UNRESOLVED",
    }
    selected_ids = [item for selection in selections for item in selection["selected_ids"]]
    directory = cfg.export_dir / "web_research"
    if directory.is_symlink():
        raise ResearchError("ERROR_RESEARCH_BOUNDARY", "The research output directory cannot be a symbolic link.")
    selected = [row for passages in all_passages for row in passages if row["chunk_id"] in selected_ids]
    warnings = ["SOURCE_CLAIMS_UNVERIFIED", "DIFFERENCES_REQUIRE_ADJUDICATION"]
    if any(source["injection_scan"]["suspected"] for source in sources):
        warnings.append("INSTRUCTION_BEARING_SOURCE_CONTENT")
    if any(selection["selection_truncated"] for selection in selections):
        warnings.append("SYNTHESIS_USES_SELECTED_PASSAGES_SEE_FULL_COMPARISON")
    if any(not selection["selected_ids"] for selection in selections):
        answer = {
            "status": "INSUFFICIENT_CONTEXT",
            "mode": mode,
            "answer": "No matching passage was found in one or both selected observations. Review the full comparison or revise the question.",
            "cited_chunk_ids": [],
            "citation_status": "NO_CONTEXT",
            "model_invoked": False,
        }
    elif mode == "extractive":
        answer = {
            "status": "EXTRACTIVE_SYNTHESIS",
            "mode": mode,
            "answer": "\n\n".join(
                f"Source {sources[index]['side']} reports:\n"
                + "\n".join(
                    f"{row['text']} (chunk_id={row['chunk_id']})"
                    for row in all_passages[index]
                    if row["chunk_id"] in selection["selected_ids"]
                )
                for index, selection in enumerate(selections)
            ),
            "cited_chunk_ids": selected_ids,
            "citation_status": "EXACT_EXCERPTS_VERIFIED",
            "model_invoked": False,
        }
    else:
        synthesis_query = (
            question
            + "\nCompare the observations, retain disagreements and uncertainty, and do not infer canonical identity. Every substantive source claim needs an exact (chunk_id=...) citation."
        )
        _check_prompt_budget(synthesis_query, selected, mode)
        generated = generate_answer(
            synthesis_query,
            selected,
            model_provider=mode,
            allow_mock_fallback=False,
            cfg=cfg,
        )
        answer_text = generated.get("answer", "")
        cited = list(dict.fromkeys(re.findall(r"\(chunk_id=([^\s()]+)\)", answer_text)))
        valid = (
            bool(cited) and set(cited) <= set(selected_ids) and set(generated.get("cited_chunk_ids", [])) == set(cited)
        )
        answer = {
            **generated,
            "mode": mode,
            "status": "MODEL_SYNTHESIS_REVIEW_REQUIRED" if valid else "MODEL_CITATIONS_REJECTED",
            "cited_chunk_ids": cited,
            "citation_status": "REFERENCES_VALID_CLAIMS_UNVERIFIED" if valid else "CITATION_ERROR",
            "model_invoked": True,
        }
        if not valid:
            warnings.append("MODEL_CITATIONS_REJECTED_DO_NOT_USE_AS_GROUNDED_ANSWER")
    # Register sources only after both inputs and the comparison pass. A provider failure keeps inputs reusable.
    source_artifacts = [
        create_artifact_record(
            processed_dir=cfg.processed_dir,
            artifact_type="SOURCE",
            payload=raw,
            logical_identity=f"frozen_web_observation:{source['snapshot_id']}",
            producer="web_research",
            producer_version="1.0.0",
            source_path_or_uri=str(cfg.export_dir / "web_snapshots" / f"{source['snapshot_id']}.json"),
        )
        for source, raw in zip(sources, frozen)
    ]
    result = {
        "schema_version": "mucaro_web_research_v1",
        "kind": "WEB_RESEARCH",
        "status": "RESEARCH_REVIEW_REQUIRED",
        "question": question,
        "sources": sources,
        "passages": [row for passages in all_passages for row in passages],
        "selections": selections,
        "comparison": comparison,
        "synthesis": answer,
        "citations": [
            dict(
                row,
                offset_basis="EXTRACTED_UNICODE_CODEPOINTS",
                snapshot_sha256=sources[index]["snapshot_sha256"],
                content_sha256=sources[index]["content_sha256"],
                source_url=sources[index]["final_url"],
                retrieval_utc=sources[index]["retrieval_utc"],
            )
            for index, passages in enumerate(all_passages)
            for row in passages
            if row["chunk_id"] in answer["cited_chunk_ids"]
        ],
        "warnings": warnings,
        "limitations": LIMITATIONS,
        "trust": "UNTRUSTED_CONTENT",
        "epistemic_classification": "COMPUTED",
        "acquisition_repeated": False,
        "policy": RESEARCH_POLICY,
    }
    report_bytes = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    directory = cfg.export_dir / "web_research"
    if directory.is_symlink():
        raise ResearchError("ERROR_RESEARCH_BOUNDARY", "The research output directory cannot be a symbolic link.")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="comparison-", suffix=".json", dir=directory, delete=False) as output:
        output.write(report_bytes)
        output.flush()
        os.fsync(output.fileno())
        report_path = Path(output.name)
    report = create_artifact_record(
        processed_dir=cfg.processed_dir,
        artifact_type="DERIVED",
        payload=report_bytes,
        logical_identity=f"web_research:{report_path.name}",
        producer="web_research",
        producer_version="1.0.0",
        parent_artifact_ids=[artifact.artifact_id for artifact in source_artifacts],
        schema_id="mucaro_web_research_v1",
        source_path_or_uri=str(report_path),
    )
    return {
        **result,
        "artifact_ids": [report.artifact_id],
        "report_artifact_id": report.artifact_id,
        "report_sha256": report.sha256,
        "source_artifact_ids": [row.artifact_id for row in source_artifacts],
    }
