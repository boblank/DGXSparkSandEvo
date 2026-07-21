from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "download_verified_asset.py"
SPEC = importlib.util.spec_from_file_location("evolab_verified_download_tests", MODULE_PATH)
assert SPEC and SPEC.loader
downloader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = downloader
SPEC.loader.exec_module(downloader)


class VerifiedDownloadTests(unittest.TestCase):
    def test_ranges_cover_every_byte_once(self) -> None:
        ranges = downloader.plan_ranges(101, 8)
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], 100)
        self.assertEqual(sum(end - start + 1 for start, end in ranges), 101)
        for previous, current in zip(ranges, ranges[1:]):
            self.assertEqual(previous[1] + 1, current[0])

    def test_hash_matches_known_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "asset.bin"
            path.write_bytes(b"evolab")
            self.assertEqual(
                downloader.sha256(path),
                "4559cb0ce4ede12666ecb87dd0a28121fc57bb022e31835f5b0a6e078e3d861c",
            )

    def test_invalid_range_contract_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            downloader.plan_ranges(0, 8)
        with self.assertRaises(ValueError):
            downloader.plan_ranges(100, 0)

    def test_partial_range_resumes_without_discarding_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = bytes(range(100))
            source = root / "source.bin"
            source.write_bytes(payload)
            destination = root / "part-000"
            partial = destination.with_suffix(".tmp")
            partial.write_bytes(payload[20:35])
            result = downloader.download_range(
                source.as_uri(),
                destination,
                20,
                69,
                1,
            )
            self.assertEqual(result.read_bytes(), payload[20:70])

    def test_failed_transfer_absorbs_progress_before_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = bytes(range(100))
            destination = Path(temporary) / "part-000"
            requested: list[tuple[int, int]] = []
            original_run = downloader.subprocess.run

            def fake_run(command: list[str], check: bool) -> SimpleNamespace:
                del check
                start, end = map(int, command[command.index("--range") + 1].split("-"))
                requested.append((start, end))
                output = Path(command[command.index("--output") + 1])
                if len(requested) == 1:
                    end = start + 24
                    return_code = 28
                else:
                    return_code = 0
                output.write_bytes(payload[start : end + 1])
                return SimpleNamespace(returncode=return_code)

            downloader.subprocess.run = fake_run
            try:
                result = downloader.download_range("https://example.invalid/model", destination, 20, 69, 1)
            finally:
                downloader.subprocess.run = original_run
            self.assertEqual(requested, [(20, 69), (45, 69)])
            self.assertEqual(result.read_bytes(), payload[20:70])


if __name__ == "__main__":
    unittest.main()
