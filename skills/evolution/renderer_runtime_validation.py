#!/usr/bin/env python3
"""Validate three real Klein rounds and a real FLUX.1 fallback on ComfyUI."""

from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import interactive_engine as engine
import renderer_ab


@contextmanager
def environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def first_selection(envelope: dict[str, Any], expected_round: int) -> dict[str, Any]:
    choices = envelope["choices"]
    return {
        "environment_id": choices["environments"][0]["id"],
        "contingency_id": choices["contingencies"][0]["id"],
        "direction_id": choices["directions"][0]["id"],
        "expected_round": expected_round,
    }


def stage_evidence(data_root: Path, envelope: dict[str, Any]) -> dict[str, Any]:
    session = envelope["session"]
    stage = session["current_stage"]
    filename = str(stage["image_url"]).rsplit("/", 1)[-1]
    path = data_root / session["session_id"] / filename
    metrics = renderer_ab.image_metrics(path, 1024, 1024)
    return {
        "round": stage["round"],
        "stage_id": stage["stage_id"],
        "image": str(path),
        "sha256": renderer_ab.sha256(path),
        "metrics": metrics,
        "render_metadata": stage["render_metadata"],
    }


def validate_three_rounds(output_root: Path, timeout: int) -> dict[str, Any]:
    data_root = output_root / "three-round"
    with environment(
        {
            "EVOLAB_IMAGE_RENDERER": "flux2-klein-4b",
            "EVOLAB_IMAGE_FALLBACK": "none",
            "EVOLAB_UNLOAD_OLLAMA": "1",
        }
    ):
        service = engine.InteractiveEvolutionService(data_root, dry_run=False, comfy_timeout=timeout)
        service._planner = service._dry_run_plan
        envelope = service.create_session("tidal_symbiosis")
        evidence: list[dict[str, Any]] = []
        for round_number in range(1, 4):
            envelope = service.evolve(
                envelope["session"]["session_id"],
                first_selection(envelope, round_number),
            )
            evidence.append(stage_evidence(data_root, envelope))
    passed = bool(
        envelope["session"]["status"] == "completed"
        and len(evidence) == 3
        and all(item["metrics"]["technical_pass"] for item in evidence)
        and all(
            item["render_metadata"].get("renderer") == "flux2-klein-4b"
            and not item["render_metadata"].get("fallback_from")
            for item in evidence
        )
    )
    return {
        "passed": passed,
        "session_id": envelope["session"]["session_id"],
        "scenario_id": envelope["session"]["scenario_id"],
        "rounds": evidence,
    }


def validate_fallback(output_root: Path, timeout: int) -> dict[str, Any]:
    data_root = output_root / "fallback"
    original_submit = engine.helper.submit_comfy_prompt

    def fail_klein_only(workflow: dict[str, Any], comfy_url: str) -> str:
        if workflow.get("1", {}).get("class_type") == "UNETLoader":
            raise RuntimeError("injected candidate outage")
        return original_submit(workflow, comfy_url)

    with environment(
        {
            "EVOLAB_IMAGE_RENDERER": "flux2-klein-4b",
            "EVOLAB_IMAGE_FALLBACK": "flux1",
            "EVOLAB_UNLOAD_OLLAMA": "1",
        }
    ):
        service = engine.InteractiveEvolutionService(data_root, dry_run=False, comfy_timeout=timeout)
        service._planner = service._dry_run_plan
        envelope = service.create_session("tidal_symbiosis")
        engine.helper.submit_comfy_prompt = fail_klein_only
        try:
            envelope = service.evolve(
                envelope["session"]["session_id"],
                first_selection(envelope, 1),
            )
        finally:
            engine.helper.submit_comfy_prompt = original_submit
        evidence = stage_evidence(data_root, envelope)
    metadata = evidence["render_metadata"]
    passed = bool(
        evidence["metrics"]["technical_pass"]
        and metadata.get("renderer") == "flux1"
        and metadata.get("fallback_from") == "flux2-klein-4b"
    )
    return {
        "passed": passed,
        "session_id": envelope["session"]["session_id"],
        "injected_failure": "flux2-klein-4b submit",
        "stage": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    result = {
        "contract_version": "1.0.0",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "three_round": validate_three_rounds(output_root, args.timeout),
        "fallback": validate_fallback(output_root, args.timeout),
    }
    result["passed"] = bool(result["three_round"]["passed"] and result["fallback"]["passed"])
    result["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    destination = output_root / "runtime_validation.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(f"VALIDATION:{destination}")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
