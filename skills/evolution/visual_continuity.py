#!/usr/bin/env python3
"""Technical and image-understanding continuity gates for EvoLab renders."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable


def technical_visual_gate(parent: Path, descendant: Path) -> dict[str, Any]:
    """Reject copies/blank frames without pretending to understand anatomy."""
    from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

    with Image.open(parent) as parent_source, Image.open(descendant) as child_source:
        parent_image = ImageOps.fit(parent_source.convert("RGB"), (256, 256))
        child_image = ImageOps.fit(child_source.convert("RGB"), (256, 256))
    difference = ImageChops.difference(parent_image, child_image)
    channel_rms = ImageStat.Stat(difference).rms
    rms_change = (sum(value * value for value in channel_rms) / 3) ** 0.5 / 255

    def structure(image: Any) -> tuple[float, float]:
        grayscale = image.convert("L")
        edge = grayscale.filter(ImageFilter.FIND_EDGES).crop((4, 4, 252, 252))
        edge_energy = ImageStat.Stat(edge).mean[0] / 255
        detail = ImageStat.Stat(grayscale).stddev[0] / 255
        return edge_energy, detail

    edge_parent, detail_parent = structure(parent_image)
    edge_child, detail_child = structure(child_image)
    edge_ratio = min(edge_parent, edge_child) / max(edge_parent, edge_child, 1e-9)
    issues: list[str] = []
    if rms_change < 0.015:
        issues.append("CHANGE_TOO_SMALL")
    if rms_change > 0.60:
        issues.append("CHANGE_TOO_LARGE")
    if min(edge_parent, edge_child) < 0.001 or min(detail_parent, detail_child) < 0.02:
        issues.append("STRUCTURELESS_IMAGE")
    if edge_ratio < 0.20:
        issues.append("STRUCTURE_DENSITY_JUMP")
    return {
        "passed": not issues,
        "mode": "technical_structure_only",
        "semantic_identity": "not_assessed",
        "issue_codes": issues,
        "pixel_rms_change": round(rms_change, 4),
        "edge_energy_parent": round(edge_parent, 4),
        "edge_energy_descendant": round(edge_child, 4),
        "edge_energy_ratio": round(edge_ratio, 4),
        "detail_parent": round(detail_parent, 4),
        "detail_descendant": round(detail_child, 4),
    }


def visual_review_schema(protected_traits: list[str]) -> dict[str, Any]:
    trait_schema: dict[str, Any] = {"type": "string", "minLength": 1, "maxLength": 120}
    if protected_traits:
        trait_schema["enum"] = protected_traits
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "verdict",
            "identity_continuity",
            "protected_traits",
            "forbidden_findings",
            "summary",
        ],
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "block"]},
            "identity_continuity": {"type": "boolean"},
            "protected_traits": {
                "type": "array",
                "maxItems": max(1, len(protected_traits)),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["trait", "status"],
                    "properties": {
                        "trait": trait_schema,
                        "status": {
                            "type": "string",
                            "enum": ["present", "transformed", "missing", "uncertain"],
                        },
                    },
                },
            },
            "forbidden_findings": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 120},
            },
            "summary": {"type": "string", "minLength": 4, "maxLength": 240},
        },
    }


def _data_url(path: Path) -> str:
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{media_type};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _normalize_visual_decision(
    raw: dict[str, Any],
    protected_traits: list[str],
) -> dict[str, Any]:
    reported = {
        str(item.get("trait")): str(item.get("status"))
        for item in raw.get("protected_traits", [])
        if isinstance(item, dict)
    }
    normalized_traits = [
        {"trait": trait, "status": reported.get(trait, "uncertain")}
        for trait in protected_traits
    ]
    absence_markers = (
        "absent",
        "not present",
        "not visible",
        "not detected",
        "none detected",
        "no evidence of",
        "without ",
        "未发现",
        "未见",
        "没有发现",
        "没有出现",
        "并未出现",
        "不存在",
        "无可见",
        "均未出现",
    )
    forbidden = [
        finding
        for item in raw.get("forbidden_findings", [])
        if (finding := str(item).strip()[:120])
        and not any(marker in finding.lower() for marker in absence_markers)
    ]
    identity = raw.get("identity_continuity") is True
    # The atomic findings are authoritative. A redundant top-level verdict can
    # contradict them, as when a reviewer lists "digits: absent" and then blocks.
    semantic_pass = (
        identity
        and not forbidden
        and all(item["status"] in {"present", "transformed"} for item in normalized_traits)
    )
    return {
        "verdict": "pass" if semantic_pass else "block",
        "identity_continuity": identity,
        "protected_traits": normalized_traits,
        "forbidden_findings": forbidden,
        "summary": str(raw.get("summary", ""))[:240],
    }


def review_with_step(
    *,
    parent: Path,
    descendant: Path,
    protected_traits: list[str],
    forbidden_traits: list[str],
    endpoint: str,
    model: str,
    api_key: str,
    timeout: int,
    post_json: Callable[..., dict[str, Any]],
    validate_schema: Callable[[Any, dict[str, Any]], list[str]],
) -> dict[str, Any]:
    """Ask the configured multimodal Step Adapter to compare parent and descendant."""
    schema = visual_review_schema(protected_traits)
    instructions = (
        "图 1 是父代，图 2 是候选后代。判断是否仍是同一谱系的渐进变化。"
        "逐项核对必须保留或按本轮声明转化的可见性状："
        + ("；".join(protected_traits) if protected_traits else "无额外登记项")
        + "。禁止出现的结构或画面问题："
        + ("；".join(forbidden_traits) if forbidden_traits else "文字、水印或无关物种")
        + "。看不清时使用 uncertain 并 block；不要根据文字提示假装看见器官。"
        "forbidden_findings 只列候选图中实际看见的禁画项；未出现、未发现或 absent 的项目不要写入该数组，"
        "全部未出现时必须返回空数组。最终 verdict 必须与身份、性状状态和实际禁画项一致。"
        "只返回符合 JSON Schema 的裁决，不输出思维过程。"
    )
    body = {
        "model": model,
        "reasoning_effort": "high",
        "messages": [
            {
                "role": "system",
                "content": "你是 EvoLab 的图像连续性审查员，只比较两张实际图片中的可见结构。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _data_url(parent), "detail": "high"}},
                    {"type": "image_url", "image_url": {"url": _data_url(descendant), "detail": "high"}},
                    {"type": "text", "text": instructions},
                ],
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "evolab_visual_continuity_review",
                "strict": True,
                "schema": schema,
            },
        },
    }
    envelope = post_json(
        endpoint,
        body,
        {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        timeout,
    )
    if envelope.get("model") != model:
        raise ValueError("unexpected visual review model")
    raw = json.loads(envelope["choices"][0]["message"]["content"])
    errors = validate_schema(raw, schema)
    if errors:
        raise ValueError("invalid visual review response")
    return _normalize_visual_decision(raw, protected_traits)
