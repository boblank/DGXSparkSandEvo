from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
