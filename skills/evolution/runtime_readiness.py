#!/usr/bin/env python3
"""Dependency readiness probe kept separate from HTTP process liveness."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable


def _flatten_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple)):
        result: set[str] = set()
        for item in value:
            result.update(_flatten_strings(item))
        return result
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(_flatten_strings(item))
        return result
    return set()


def _loader_options(object_info: dict[str, Any], node: str, field: str) -> set[str]:
    try:
        value = object_info[node]["input"]["required"][field]
    except (KeyError, TypeError):
        return set()
    return {item for item in _flatten_strings(value) if item.endswith(('.safetensors', '.ckpt', '.pt'))}


def probe_runtime(
    *,
    dry_run: bool,
    step_endpoint: str,
    step_model: str,
    step_key: str | None,
    comfy_url: str,
    renderer_ids: list[str],
    renderer_catalog: dict[str, Any],
    workflow_root: Path,
    get_json: Callable[[str, int], dict[str, Any]],
    post_json: Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]],
    timeout: int = 8,
) -> dict[str, Any]:
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if dry_run:
        return {
            "ready": True,
            "mode": "fixture",
            "checked_at": checked_at,
            "components": {
                "step": {"ready": True, "mode": "fixture", "strict_json": True},
                "comfyui": {"ready": True, "mode": "fixture", "reachable": True},
                "workflows": {"ready": True, "missing_files": [], "missing_nodes": []},
                "models": {"ready": True, "missing": []},
            },
        }

    components: dict[str, Any] = {}
    if not step_key:
        components["step"] = {
            "ready": False,
            "configured": False,
            "strict_json": False,
            "model": step_model,
        }
    else:
        try:
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["ready"],
                "properties": {"ready": {"type": "boolean"}},
            }
            body = {
                "model": step_model,
                "reasoning_effort": "low",
                "messages": [
                    {"role": "system", "content": "Return the requested readiness JSON only."},
                    {"role": "user", "content": "Return {\"ready\": true}."},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "evolab_readiness", "strict": True, "schema": schema},
                },
            }
            envelope = post_json(
                step_endpoint,
                body,
                {"Authorization": "Bearer " + step_key, "Content-Type": "application/json"},
                timeout,
            )
            payload = json.loads(envelope["choices"][0]["message"]["content"])
            step_ready = envelope.get("model") == step_model and payload == {"ready": True}
            components["step"] = {
                "ready": step_ready,
                "configured": True,
                "strict_json": step_ready,
                "model": step_model,
            }
        except Exception:
            components["step"] = {
                "ready": False,
                "configured": True,
                "strict_json": False,
                "model": step_model,
            }

    try:
        object_info = get_json(comfy_url.rstrip("/") + "/object_info", timeout)
        comfy_reachable = isinstance(object_info, dict) and bool(object_info)
    except Exception:
        object_info = {}
        comfy_reachable = False
    components["comfyui"] = {
        "ready": comfy_reachable,
        "reachable": comfy_reachable,
        "node_count": len(object_info),
    }

    profiles = renderer_catalog.get("profiles", {})
    workflow_files: list[str] = []
    required_models: list[dict[str, str]] = []
    for renderer_id in renderer_ids:
        profile = profiles.get(renderer_id, {})
        for key in ("workflow_file", "reference_workflow_file"):
            filename = profile.get(key)
            if filename and filename not in workflow_files:
                workflow_files.append(str(filename))
        for item in profile.get("required_models", []):
            normalized = {"directory": str(item["directory"]), "filename": str(item["filename"])}
            if normalized not in required_models:
                required_models.append(normalized)

    missing_files: list[str] = []
    required_nodes: set[str] = set()
    for filename in workflow_files:
        path = workflow_root / filename
        if not path.is_file():
            missing_files.append(filename)
            continue
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
            required_nodes.update(
                str(node.get("class_type"))
                for node in workflow.values()
                if isinstance(node, dict) and node.get("class_type")
            )
        except (OSError, json.JSONDecodeError):
            missing_files.append(filename)
    missing_nodes = sorted(node for node in required_nodes if node not in object_info)
    components["workflows"] = {
        "ready": not missing_files and not missing_nodes,
        "checked": workflow_files,
        "missing_files": missing_files,
        "missing_nodes": missing_nodes,
    }

    available_by_directory = {
        "diffusion_models": _loader_options(object_info, "CheckpointLoaderSimple", "ckpt_name")
        | _loader_options(object_info, "UNETLoader", "unet_name"),
        "text_encoders": _loader_options(object_info, "CLIPLoader", "clip_name"),
        "vae": _loader_options(object_info, "VAELoader", "vae_name"),
    }
    missing_models = [
        item["filename"]
        for item in required_models
        if item["filename"] not in available_by_directory.get(item["directory"], set())
    ]
    components["models"] = {
        "ready": not missing_models,
        "checked": [item["filename"] for item in required_models],
        "missing": missing_models,
    }
    ready = all(component.get("ready") is True for component in components.values())
    return {
        "ready": ready,
        "mode": "live",
        "checked_at": checked_at,
        "components": components,
    }
