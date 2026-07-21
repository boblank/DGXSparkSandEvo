#!/usr/bin/env python3
"""Build a browser-safe static bundle from an EvoLab run manifest."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_manifest = args.manifest.resolve()
    source_dir = source_manifest.parent
    output_dir = args.output_dir.resolve()
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    manifest = copy.deepcopy(json.loads(source_manifest.read_text(encoding="utf-8")))
    image_paths: list[str] = []
    for stage in manifest.get("story_stages", []):
        source = Path(stage.get("image_path", ""))
        if not source.is_file():
            source = source_dir / source.name
        if not source.is_file():
            raise FileNotFoundError(f"missing stage image: {source.name}")
        destination = artifacts_dir / source.name
        shutil.copy2(source, destination)
        relative = f"artifacts/{destination.name}"
        stage["image_path"] = relative
        image_paths.append(relative)

    storyboard = Path(manifest.get("storyboard_path", ""))
    if not storyboard.is_file():
        storyboard = source_dir / storyboard.name
    if not storyboard.is_file():
        raise FileNotFoundError("missing evolution_storyboard.png")
    storyboard_destination = artifacts_dir / storyboard.name
    shutil.copy2(storyboard, storyboard_destination)
    manifest["image_paths"] = image_paths
    manifest["storyboard_path"] = f"artifacts/{storyboard_destination.name}"

    output_manifest = output_dir / "evolution_manifest.normal.json"
    output_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
