#!/usr/bin/env python3
"""Download a large model asset in verified HTTP ranges.

The final path appears only after every range, the byte count, and SHA-256
match the caller-provided contract. Partial downloads remain resumable.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import shutil
import subprocess
from pathlib import Path


def plan_ranges(size: int, workers: int) -> list[tuple[int, int]]:
    if size <= 0 or workers <= 0:
        raise ValueError("size and workers must be positive")
    chunk = (size + workers - 1) // workers
    return [(start, min(size - 1, start + chunk - 1)) for start in range(0, size, chunk)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_range(url: str, destination: Path, start: int, end: int) -> Path:
    expected = end - start + 1
    if destination.is_file() and destination.stat().st_size == expected:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    subprocess.run(
        [
            "curl",
            "-fL",
            "--silent",
            "--show-error",
            "--retry",
            "20",
            "--retry-all-errors",
            "--connect-timeout",
            "20",
            "--speed-limit",
            "262144",
            "--speed-time",
            "60",
            "--range",
            f"{start}-{end}",
            "--output",
            str(temporary),
            url,
        ],
        check=True,
    )
    if temporary.stat().st_size != expected:
        raise RuntimeError(f"range {start}-{end} has the wrong byte count")
    temporary.replace(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    expected_hash = args.sha256.strip().lower()
    if output.is_file() and output.stat().st_size == args.size and sha256(output) == expected_hash:
        print(f"VERIFIED:{output}")
        return 0

    ranges = plan_ranges(args.size, args.workers)
    parts_dir = output.parent / ".download-parts" / output.name
    jobs = [
        (args.url, parts_dir / f"part-{index:03d}", start, end)
        for index, (start, end) in enumerate(ranges)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        parts = list(pool.map(lambda job: download_range(*job), jobs))

    assembling = output.with_suffix(output.suffix + ".assembling")
    digest = hashlib.sha256()
    written = 0
    with assembling.open("wb") as target:
        for part in parts:
            with part.open("rb") as source:
                while block := source.read(8 * 1024 * 1024):
                    target.write(block)
                    digest.update(block)
                    written += len(block)
    actual_hash = digest.hexdigest()
    if written != args.size or actual_hash != expected_hash:
        assembling.unlink(missing_ok=True)
        raise RuntimeError(
            f"assembled asset failed validation: bytes={written}, sha256={actual_hash}"
        )
    assembling.replace(output)
    shutil.rmtree(parts_dir)
    print(f"VERIFIED:{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
