#!/usr/bin/env python3
"""Bounded scientific-review contracts for one EvoLab evolution round."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


VERDICTS = {"pass", "revise", "block"}
ISSUE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
SENSITIVE_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|authorization\s*:|bearer\s+[a-z0-9._-]+|sk-[a-z0-9_-]{8,})"
)

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict",
        "issue_codes",
        "summary",
        "transition_ids",
        "pressure_ids",
        "source_ids",
    ],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise", "block"]},
        "issue_codes": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 3, "maxLength": 64},
        },
        "summary": {"type": "string", "minLength": 4, "maxLength": 240},
        "transition_ids": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 96},
        },
        "pressure_ids": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 96},
        },
        "source_ids": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 96},
        },
    },
}


def input_summary_hash(
    draft: dict[str, Any],
    previous: dict[str, Any],
    selection: dict[str, Any],
    spec: dict[str, Any],
    evidence_pack: dict[str, Any],
) -> str:
    """Hash the bounded review input without retaining model prompts or reasoning."""
    payload = {
        "draft": draft,
        "parent_traits": previous.get("traits", []),
        "protected_traits": previous.get("protected_traits", []),
        "selection_ids": {
            key: (selection.get(key) or {}).get("id")
            for key in ("environment", "contingency", "direction")
        },
        "world_id": (spec.get("world") or {}).get("id"),
        "evidence": evidence_pack,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _string_list(value: Any, *, maximum: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        candidate = str(item).strip()
        if candidate and candidate not in result:
            result.append(candidate[:96])
    return result[:maximum]


def normalize_decision(
    raw: Any,
    *,
    evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    """Return an allowlisted decision and fail closed on invented evidence."""
    if not isinstance(raw, dict):
        raise ValueError("review decision must be an object")
    verdict = str(raw.get("verdict", "block")).strip().lower()
    if verdict not in VERDICTS:
        raise ValueError("invalid review verdict")
    issues = [
        item
        for item in _string_list(raw.get("issue_codes"), maximum=8)
        if ISSUE_PATTERN.fullmatch(item)
    ]
    summary = str(raw.get("summary", "")).strip()
    if not summary or len(summary) > 240:
        raise ValueError("invalid review summary")
    if SENSITIVE_PATTERN.search(summary):
        verdict = "block"
        issues.append("TRACE_SENSITIVE_CONTENT")
        summary = "审查记录含有不应保存的内容，当前结果未放行。"

    allowed = {
        key: set(_string_list(evidence_pack.get(key), maximum=32))
        for key in ("transition_ids", "pressure_ids", "source_ids")
    }
    identifiers: dict[str, list[str]] = {}
    for key, maximum in (("transition_ids", 8), ("pressure_ids", 8), ("source_ids", 12)):
        requested = _string_list(raw.get(key), maximum=maximum)
        if any(item not in allowed[key] for item in requested):
            verdict = "block"
            issues.append("UNVERIFIED_SOURCE")
        identifiers[key] = [item for item in requested if item in allowed[key]]

    if verdict != "pass" and not issues:
        issues.append("REVIEW_NOT_PASSED")
    return {
        "verdict": verdict,
        "issue_codes": list(dict.fromkeys(issues))[:8],
        "summary": summary,
        **identifiers,
    }


def deterministic_review(
    *,
    draft: dict[str, Any],
    previous: dict[str, Any],
    selection: dict[str, Any],
    spec: dict[str, Any],
    evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    """Apply auditable continuity/evidence rules; this is not represented as an Agent."""
    issues: list[str] = []
    direction = selection.get("direction") if isinstance(selection.get("direction"), dict) else {}
    constraint_mode = str((spec.get("world") or {}).get("constraint_mode", ""))
    draft_text = " ".join(
        str(item)
        for field in ("organism_name", "lineage_summary", "change_summary", "traits")
        for item in (draft.get(field, []) if isinstance(draft.get(field), list) else [draft.get(field, "")])
    )

    if constraint_mode == "historical_reconstruction":
        transformations = {
            str(item.get("from")): str(item.get("to"))
            for item in direction.get("trait_transformations", [])
            if isinstance(item, dict) and item.get("from") and item.get("to")
        }
        for trait in previous.get("protected_traits", []):
            expected = transformations.get(str(trait), str(trait))
            if expected and expected not in draft_text:
                issues.append("PROTECTED_TRAIT_MISSING")
                break
        allowed_parents = direction.get("allowed_parent_direction_ids")
        previous_direction = (
            ((previous.get("selection") or {}).get("direction") or {}).get("id")
            if isinstance(previous.get("selection"), dict)
            else None
        )
        if allowed_parents and previous_direction not in allowed_parents:
            issues.append("HISTORICAL_PREREQUISITE_SKIPPED")

        if evidence_pack.get("historical_match_status") == "historical_reference":
            external_anchors = _string_list(
                evidence_pack.get("historical_required_external_traits"), maximum=8
            )
            internal_anchors = _string_list(
                evidence_pack.get("historical_required_internal_traits"), maximum=8
            )
            if (
                not external_anchors
                or not internal_anchors
                or not any(anchor in draft_text for anchor in external_anchors)
                or not any(anchor in draft_text for anchor in internal_anchors)
            ):
                issues.append("HISTORICAL_ANALOG_DIVERGENCE")
        elif evidence_pack.get("historical_match_status") in {
            "partial_reference",
            "bounded_inference",
        }:
            candidate_names = _string_list(
                evidence_pack.get("historical_candidate_names"), maximum=12
            )
            organism_name = str(draft.get("organism_name", "")).strip().casefold()
            named_as_candidate = any(
                organism_name == candidate.casefold() for candidate in candidate_names
            )
            asserted_candidate = any(
                candidate in draft_text
                and any(term in draft_text for term in ("就是", "已经确认", "已知物种"))
                for candidate in candidate_names
            )
            if named_as_candidate or asserted_candidate:
                issues.append("UNSUPPORTED_TAXON_CLAIM")

    if constraint_mode == "future_scenario":
        short_term = any(term in draft_text for term in ("短期适应", "个体适应", "即时适应", "一次飞行", "宇航员个体"))
        hereditary = any(term in draft_text for term in ("遗传", "后代", "跨世代", "演化"))
        if short_term and hereditary:
            issues.append("ACCLIMATIZATION_AS_HEREDITY")

    if evidence_pack.get("status") == "no_match" and evidence_pack.get("claimed_source_ids"):
        issues.append("UNVERIFIED_SOURCE")

    verdict = "revise" if issues else "pass"
    summary = (
        "规划需要修正证据边界或谱系连续性。"
        if issues
        else "规则门禁未发现证据边界或谱系连续性冲突。"
    )
    return {
        "verdict": verdict,
        "issue_codes": list(dict.fromkeys(issues)),
        "summary": summary,
        "transition_ids": _string_list(evidence_pack.get("transition_ids"), maximum=8),
        "pressure_ids": _string_list(evidence_pack.get("pressure_ids"), maximum=8),
        "source_ids": _string_list(evidence_pack.get("source_ids"), maximum=12),
    }
