#!/usr/bin/env python3
"""Shared ComfyUI image renderer profiles for EvoLab.

FLUX.1 remains the default.  FLUX.2 Klein is an opt-in candidate until the
separate A/B gate records a passing decision.
"""

from __future__ import annotations

import copy
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_FILE = SCRIPT_DIR / "renderer_profiles.json"


class RendererConfigurationError(ValueError):
    """The selected renderer profile or workflow is invalid."""


class RendererExhaustedError(RuntimeError):
    """Every configured image renderer failed."""

    def __init__(self, attempts: list[dict[str, str]]) -> None:
        super().__init__("all configured image renderers failed")
        self.attempts = attempts


@dataclass(frozen=True)
class RenderedImage:
    source: Path
    renderer: str
    generator: str
    seed: int
    duration_seconds: float
    fallback_from: str | None
    reference_conditioning: bool = False


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def renderer_catalog() -> dict[str, Any]:
    catalog = _read_json(PROFILE_FILE)
    profiles = catalog.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise RendererConfigurationError("renderer profile catalog is empty")
    return catalog


def resolve_renderer(renderer_id: str | None) -> tuple[str, dict[str, Any]]:
    catalog = renderer_catalog()
    candidate = (renderer_id or catalog["default_renderer"]).strip().lower()
    aliases = {
        "flux.1": "flux1",
        "flux-1": "flux1",
        "klein": "flux2-klein-4b",
        "flux2": "flux2-klein-4b",
        "flux.2-klein-4b": "flux2-klein-4b",
    }
    candidate = aliases.get(candidate, candidate)
    profile = catalog["profiles"].get(candidate)
    if not isinstance(profile, dict):
        raise RendererConfigurationError(f"unknown image renderer: {candidate}")
    return candidate, profile


def renderer_chain(primary: str | None, fallback: str | None) -> list[str]:
    primary_id, _ = resolve_renderer(primary)
    chain = [primary_id]
    if fallback and fallback.strip().lower() not in {"none", "off", "disabled"}:
        fallback_id, _ = resolve_renderer(fallback)
        if fallback_id not in chain:
            chain.append(fallback_id)
    return chain


def required_models(renderer_id: str) -> list[dict[str, str]]:
    _, profile = resolve_renderer(renderer_id)
    return [
        {"directory": str(item["directory"]), "filename": str(item["filename"])}
        for item in profile.get("required_models", [])
    ]


def _node(workflow: dict[str, Any], role: dict[str, str]) -> dict[str, Any]:
    node_id = str(role["id"])
    try:
        node = workflow[node_id]
        inputs = node["inputs"]
    except (KeyError, TypeError) as exc:
        raise RendererConfigurationError(f"workflow is missing node {node_id}") from exc
    if not isinstance(inputs, dict):
        raise RendererConfigurationError(f"workflow node {node_id} has invalid inputs")
    return inputs


def _workflow_role(role: dict[str, Any], *, reference: bool) -> dict[str, Any]:
    if reference and role.get("reference_id"):
        return {**role, "id": str(role["reference_id"])}
    return role


def build_image_workflow(
    renderer_id: str,
    *,
    prompt: str,
    negative_prompt: str,
    seed: int,
    filename_prefix: str,
    width: int = 1024,
    height: int = 1024,
    remove_negative_terms: tuple[str, ...] = (),
    reference_image: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved_id, profile = resolve_renderer(renderer_id)
    workflow_filename = (
        profile.get("reference_workflow_file")
        if reference_image and profile.get("reference_workflow_file")
        else profile["workflow_file"]
    )
    workflow_path = SCRIPT_DIR / str(workflow_filename)
    workflow = copy.deepcopy(_read_json(workflow_path))
    roles = profile["nodes"]

    final_prompt = prompt.strip()
    if profile.get("negative_mode") == "prompt_suffix" and negative_prompt.strip():
        final_prompt += " Avoid these failures: " + negative_prompt.strip().rstrip(".") + "."
    prompt_inputs = _node(workflow, roles["prompt"])
    prompt_inputs[str(roles["prompt"]["field"])] = final_prompt

    negative_role = roles.get("negative")
    if negative_role:
        negative_inputs = _node(workflow, negative_role)
        field = str(negative_role["field"])
        existing = str(negative_inputs.get(field, "")).strip()
        for term in remove_negative_terms:
            existing = existing.replace(term, "")
        negative_inputs[field] = ", ".join(
            item for item in (negative_prompt.strip(), existing) if item
        )

    seed_role = _workflow_role(roles["seed"], reference=bool(reference_image))
    seed_inputs = _node(workflow, seed_role)
    seed_inputs[str(roles["seed"]["field"])] = int(seed)
    output_role = _workflow_role(roles["output"], reference=bool(reference_image))
    output_inputs = _node(workflow, output_role)
    output_inputs[str(roles["output"]["field"])] = filename_prefix
    if reference_image:
        reference_role = roles.get("reference")
        if not reference_role or not profile.get("reference_workflow_file"):
            raise RendererConfigurationError(
                f"renderer {resolved_id} does not support reference conditioning"
            )
        reference_inputs = _node(workflow, reference_role)
        reference_inputs[str(reference_role["field"])] = reference_image
    for dimension_role in roles.get("dimensions", []):
        selected_role = _workflow_role(
            dimension_role,
            reference=bool(reference_image),
        )
        dimension_inputs = _node(workflow, selected_role)
        dimension_inputs[str(dimension_role["width_field"])] = int(width)
        dimension_inputs[str(dimension_role["height_field"])] = int(height)

    return workflow, {
        "renderer": resolved_id,
        "generator": str(profile["generator"]),
        "workflow_file": workflow_path.name,
        "seed": int(seed),
        "width": int(width),
        "height": int(height),
        "reference_conditioning": bool(reference_image),
    }


SubmitPrompt = Callable[[dict[str, Any], str], str]
WaitForPrompt = Callable[[str, str, int], dict[str, Any]]
LocateOutput = Callable[[dict[str, Any], Path], Path]
LogMessage = Callable[[str], None]
UploadReference = Callable[[Path, str], str]


def render_image_with_fallback(
    renderer_ids: list[str],
    *,
    prompt: str,
    negative_prompt: str,
    seed: int,
    filename_prefix: str,
    destination: Path,
    comfy_url: str,
    comfy_output: Path,
    timeout: int,
    submit_prompt: SubmitPrompt,
    wait_for_prompt: WaitForPrompt,
    locate_output: LocateOutput,
    width: int = 1024,
    height: int = 1024,
    remove_negative_terms: tuple[str, ...] = (),
    reference_image: Path | None = None,
    upload_reference: UploadReference | None = None,
    log: LogMessage | None = None,
) -> RenderedImage:
    attempts: list[dict[str, str]] = []
    primary = renderer_ids[0]
    for renderer_id in renderer_ids:
        started = time.monotonic()
        try:
            _, profile = resolve_renderer(renderer_id)
            reference_name = None
            if reference_image:
                if not profile.get("reference_workflow_file"):
                    raise RendererConfigurationError(
                        f"renderer {renderer_id} cannot preserve the supplied lineage reference"
                    )
                if not reference_image.is_file():
                    raise FileNotFoundError("reference image is missing")
                if upload_reference is None:
                    raise RendererConfigurationError(
                        "reference-capable rendering requires an uploader"
                    )
                reference_name = upload_reference(reference_image, comfy_url)
            workflow, metadata = build_image_workflow(
                renderer_id,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                filename_prefix=filename_prefix,
                width=width,
                height=height,
                remove_negative_terms=remove_negative_terms,
                reference_image=reference_name,
            )
            prompt_id = submit_prompt(workflow, comfy_url)
            outputs = wait_for_prompt(prompt_id, comfy_url, timeout)
            source = locate_output(outputs, comfy_output)
            if not source.is_file():
                raise FileNotFoundError("ComfyUI output file is missing")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            return RenderedImage(
                source=destination,
                renderer=metadata["renderer"],
                generator=metadata["generator"],
                seed=int(seed),
                duration_seconds=round(time.monotonic() - started, 3),
                fallback_from=primary if renderer_id != primary else None,
                reference_conditioning=bool(metadata["reference_conditioning"]),
            )
        except Exception as exc:
            attempts.append({"renderer": renderer_id, "error": type(exc).__name__})
            if log:
                log(f"image renderer {renderer_id} failed ({type(exc).__name__})")
    raise RendererExhaustedError(attempts)
