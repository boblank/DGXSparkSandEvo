#!/usr/bin/env python3
"""Install only fully verified HunyuanVideo assets into ComfyUI model folders."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from video_generation import MODEL_ASSETS, sha256


class ModelInstallError(RuntimeError):
    """A staged or existing model did not match its immutable contract."""


def verify(path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        raise ModelInstallError(f"missing model asset: {contract['filename']}")
    actual_bytes = path.stat().st_size
    if actual_bytes != int(contract["bytes"]):
        raise ModelInstallError(
            f"wrong byte count for {contract['filename']}: {actual_bytes}"
        )
    actual_hash = sha256(path)
    if actual_hash != str(contract["sha256"]):
        raise ModelInstallError(f"SHA-256 mismatch for {contract['filename']}")
    return {"bytes": actual_bytes, "sha256": actual_hash}


def install_assets(
    contracts: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    staging_root: Path,
    comfy_models_root: Path,
    *,
    mode: str = "hardlink",
) -> list[dict[str, Any]]:
    if mode not in {"hardlink", "copy"}:
        raise ValueError("mode must be hardlink or copy")
    records: list[dict[str, Any]] = []
    for contract in contracts:
        source = staging_root / str(contract["filename"])
        verified = verify(source, contract)
        destination = comfy_models_root / str(contract["directory"]) / str(contract["filename"])
        if destination.exists():
            existing = verify(destination, contract)
            records.append(
                {
                    "filename": contract["filename"],
                    "destination": str(destination),
                    "status": "already_installed",
                    **existing,
                }
            )
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".installing")
        temporary.unlink(missing_ok=True)
        if mode == "hardlink":
            os.link(source, temporary)
        else:
            shutil.copy2(source, temporary)
        installed = verify(temporary, contract)
        temporary.replace(destination)
        records.append(
            {
                "filename": contract["filename"],
                "destination": str(destination),
                "status": "installed",
                **installed,
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--comfy-models-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("hardlink", "copy"), default="hardlink")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    staging_root = args.staging_root.expanduser().resolve()
    comfy_models_root = args.comfy_models_root.expanduser().resolve()
    records = install_assets(
        MODEL_ASSETS,
        staging_root,
        comfy_models_root,
        mode=args.mode,
    )
    result = {
        "contract_version": "1.0.0",
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "staging_root": str(staging_root),
        "comfy_models_root": str(comfy_models_root),
        "mode": args.mode,
        "passed": len(records) == len(MODEL_ASSETS),
        "assets": records,
    }
    manifest = args.manifest or staging_root / "install_manifest.json"
    temporary = manifest.with_suffix(manifest.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    print(f"MODEL_INSTALL:{manifest}")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
