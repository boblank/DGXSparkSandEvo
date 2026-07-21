#!/usr/bin/env python3
"""Run and gate a fixed FLUX.1 vs FLUX.2 Klein A/B suite on ComfyUI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import evolution_helper as helper
import rendering


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SUITE = SCRIPT_DIR / "renderer_ab_suite.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_metrics(path: Path, expected_width: int, expected_height: int) -> dict[str, Any]:
    try:
        from PIL import Image, ImageStat
    except ImportError as exc:
        raise RuntimeError("Pillow is required for renderer A/B validation") from exc
    with Image.open(path) as image:
        image.load()
        rgb = image.convert("RGB")
        grayscale = rgb.convert("L")
        extrema = grayscale.getextrema()
        entropy = round(float(grayscale.entropy()), 4)
        standard_deviation = round(float(ImageStat.Stat(grayscale).stddev[0]), 4)
        metrics = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "bytes": path.stat().st_size,
            "grayscale_range": int(extrema[1] - extrema[0]),
            "entropy": entropy,
            "standard_deviation": standard_deviation,
        }
    metrics["technical_pass"] = bool(
        metrics["format"] == "PNG"
        and metrics["width"] == expected_width
        and metrics["height"] == expected_height
        and metrics["bytes"] >= 50_000
        and metrics["grayscale_range"] >= 32
        and metrics["entropy"] >= 4.0
        and metrics["standard_deviation"] >= 12.0
    )
    return metrics


def available_model_names(object_info: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and value.endswith(".safetensors"):
            names.add(value)

    for node_name in ("CheckpointLoaderSimple", "UNETLoader", "CLIPLoader", "VAELoader"):
        walk(object_info.get(node_name, {}).get("input", {}))
    return names


def preflight(comfy_url: str, profiles: list[str]) -> dict[str, Any]:
    object_info = helper.get_json(f"{comfy_url.rstrip('/')}/object_info", 30)
    available = available_model_names(object_info)
    missing: dict[str, list[str]] = {}
    for profile in profiles:
        filenames = [item["filename"] for item in rendering.required_models(profile)]
        absent = [name for name in filenames if name not in available]
        if absent:
            missing[profile] = absent
    required_nodes = {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "ConditioningZeroOut",
        "CFGGuider",
        "Flux2Scheduler",
        "EmptyFlux2LatentImage",
        "SamplerCustomAdvanced",
        "SaveImage",
    }
    absent_nodes = sorted(required_nodes - set(object_info))
    return {
        "passed": not missing and not absent_nodes,
        "missing_models": missing,
        "missing_nodes": absent_nodes,
        "available_model_count": len(available),
    }


def blind_order(case_id: str, profiles: list[str]) -> list[str]:
    value = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:2], 16)
    return profiles if value % 2 == 0 else list(reversed(profiles))


def build_blind_assets(
    output_root: Path,
    cases: list[dict[str, Any]],
    profiles: list[str],
) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Pillow is required for blind A/B sheets") from exc
    blind_dir = output_root / "blind"
    blind_dir.mkdir(parents=True, exist_ok=True)
    key: dict[str, Any] = {"cases": []}
    rows: list[tuple[str, Path, Path]] = []
    for case in cases:
        case_id = str(case["case_id"])
        order = blind_order(case_id, profiles)
        labels: dict[str, str] = {}
        copied: list[Path] = []
        for label, profile in zip(("A", "B"), order):
            source = output_root / "raw" / case_id / f"{profile}.png"
            destination = blind_dir / f"{case_id}_{label}.png"
            shutil.copy2(source, destination)
            labels[label] = profile
            copied.append(destination)
        key["cases"].append({"case_id": case_id, "labels": labels})
        rows.append((case_id, copied[0], copied[1]))

    font = ImageFont.load_default()
    for sheet_index in range(0, len(rows), 4):
        selected = rows[sheet_index : sheet_index + 4]
        sheet = Image.new("RGB", (1056, len(selected) * 548), (6, 23, 29))
        draw = ImageDraw.Draw(sheet)
        for row_index, (case_id, left_path, right_path) in enumerate(selected):
            y = row_index * 548
            draw.text((16, y + 8), f"{case_id}   A", fill=(233, 226, 208), font=font)
            draw.text((544, y + 8), "B", fill=(233, 226, 208), font=font)
            for x, path in ((16, left_path), (544, right_path)):
                with Image.open(path) as image:
                    preview = image.convert("RGB")
                    preview.thumbnail((496, 496))
                    sheet.paste(preview, (x, y + 36))
        sheet.save(blind_dir / f"sheet_{sheet_index // 4 + 1}.jpg", quality=92)
    write_json(output_root / "blind_key.json", key)
    return key


def evaluate_gate(
    manifest: dict[str, Any],
    blind_key: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    key_by_case = {item["case_id"]: item["labels"] for item in blind_key["cases"]}
    reviewed = {item["case_id"]: item for item in review.get("cases", [])}
    technical_pass = all(
        result.get("status") == "generated"
        and result.get("metrics", {}).get("technical_pass") is True
        for result in manifest.get("results", [])
    )
    klein_not_worse = 0
    blockers: list[dict[str, Any]] = []
    for case_id, labels in key_by_case.items():
        item = reviewed.get(case_id, {})
        preferred = str(item.get("preferred", "")).upper()
        if preferred == "TIE" or labels.get(preferred) == "flux2-klein-4b":
            klein_not_worse += 1
        for blocker in item.get("blockers", []):
            blockers.append({"case_id": case_id, "blocker": str(blocker)})
    three_round_pass = review.get("three_round", {}).get("passed") is True
    fallback_pass = review.get("fallback", {}).get("passed") is True
    all_cases_reviewed = len(reviewed) == len(key_by_case) and all(
        str(item.get("preferred", "")).upper() in {"A", "B", "TIE"}
        for item in reviewed.values()
    )
    passed = bool(
        technical_pass
        and all_cases_reviewed
        and klein_not_worse >= 6
        and not blockers
        and three_round_pass
        and fallback_pass
    )
    return {
        "passed": passed,
        "technical_pass": technical_pass,
        "all_cases_reviewed": all_cases_reviewed,
        "klein_not_worse": klein_not_worse,
        "required_klein_not_worse": 6,
        "blockers": blockers,
        "three_round_pass": three_round_pass,
        "fallback_pass": fallback_pass,
        "decision": "enable_klein_candidate" if passed else "keep_flux1_default",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the EvoLab FLUX renderer A/B gate")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--comfy-url", default=os.environ.get("COMFYUI_URL", "http://127.0.0.1:7000"))
    parser.add_argument("--comfy-output", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--review-file", type=Path)
    parser.add_argument("--evaluate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    suite = read_json(args.suite.resolve())
    profiles = ["flux1", "flux2-klein-4b"]
    manifest_path = output_root / "ab_manifest.json"
    blind_key_path = output_root / "blind_key.json"

    if args.evaluate_only:
        if not args.review_file:
            raise SystemExit("--review-file is required with --evaluate-only")
        manifest = read_json(manifest_path)
        blind_key = read_json(blind_key_path)
        gate = evaluate_gate(manifest, blind_key, read_json(args.review_file.resolve()))
        manifest["gate"] = gate
        write_json(manifest_path, manifest)
        print(json.dumps(gate, ensure_ascii=False))
        return 0 if gate["passed"] else 2

    preflight_result = preflight(args.comfy_url, profiles)
    if not preflight_result["passed"]:
        write_json(manifest_path, {"status": "preflight_failed", "preflight": preflight_result})
        print(json.dumps(preflight_result, ensure_ascii=False), file=sys.stderr)
        return 3

    comfy_output = args.comfy_output or Path(
        os.environ.get(
            "COMFY_OUTPUT_DIR",
            "/home/Developer/build_a_claw_workshop-bundle/comfyui-app/ComfyUI/output",
        )
    )
    helper.unload_ollama()
    manifest: dict[str, Any] = {
        "contract_version": "1.0.0",
        "status": "running",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "suite": str(args.suite.resolve()),
        "profiles": profiles,
        "preflight": preflight_result,
        "results": [],
        "gate": {"passed": False, "decision": "keep_flux1_default", "reason": "awaiting_review"},
    }
    write_json(manifest_path, manifest)
    failures = 0
    for case in suite["cases"]:
        for profile in profiles:
            case_id = str(case["case_id"])
            destination = output_root / "raw" / case_id / f"{profile}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            record: dict[str, Any] = {
                "case_id": case_id,
                "scenario_id": case["scenario_id"],
                "renderer": profile,
                "seed": int(case["seed"]),
                "prompt_sha256": hashlib.sha256(case["prompt"].encode("utf-8")).hexdigest(),
                "status": "running",
            }
            try:
                rendered = rendering.render_image_with_fallback(
                    [profile],
                    prompt=case["prompt"],
                    negative_prompt=case["negative_prompt"],
                    seed=int(case["seed"]),
                    filename_prefix=f"evolab_ab/{case_id}_{profile}",
                    destination=destination,
                    comfy_url=args.comfy_url,
                    comfy_output=comfy_output,
                    timeout=args.timeout,
                    submit_prompt=helper.submit_comfy_prompt,
                    wait_for_prompt=helper.wait_for_comfy,
                    locate_output=helper.comfy_output_path,
                )
                record.update(
                    {
                        "status": "generated",
                        "duration_seconds": rendered.duration_seconds,
                        "sha256": sha256(destination),
                        "metrics": image_metrics(destination, int(suite["width"]), int(suite["height"])),
                    }
                )
            except Exception as exc:
                failures += 1
                record.update({"status": "failed", "error_type": type(exc).__name__})
            manifest["results"].append(record)
            write_json(manifest_path, manifest)

    manifest["status"] = "generated" if failures == 0 else "failed"
    manifest["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if failures == 0:
        build_blind_assets(output_root, suite["cases"], profiles)
    write_json(manifest_path, manifest)
    print(f"MANIFEST:{manifest_path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
