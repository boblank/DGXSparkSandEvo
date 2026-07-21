from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EVOLUTION_DIR = ROOT / "skills" / "evolution"
sys.path.insert(0, str(EVOLUTION_DIR))
MODULE_PATH = EVOLUTION_DIR / "renderer_ab.py"
SPEC = importlib.util.spec_from_file_location("evolab_renderer_ab_tests", MODULE_PATH)
assert SPEC and SPEC.loader
renderer_ab = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renderer_ab
SPEC.loader.exec_module(renderer_ab)


def fixtures() -> tuple[dict, dict, dict]:
    case_ids = [f"case_{index}" for index in range(8)]
    manifest = {
        "results": [
            {
                "case_id": case_id,
                "renderer": renderer,
                "status": "generated",
                "metrics": {"technical_pass": True},
            }
            for case_id in case_ids
            for renderer in ("flux1", "flux2-klein-4b")
        ]
    }
    key = {
        "cases": [
            {
                "case_id": case_id,
                "labels": {"A": "flux2-klein-4b", "B": "flux1"},
            }
            for case_id in case_ids
        ]
    }
    review = {
        "cases": [
            {
                "case_id": case_id,
                "preferred": "A",
                "blocking_issues": {
                    "A": [],
                    "B": ["Reference-only issue must not block the candidate."],
                },
            }
            for case_id in case_ids
        ],
        "three_round": {"passed": True},
        "fallback": {"passed": True},
    }
    return manifest, key, review


class RendererABGateTests(unittest.TestCase):
    def test_reference_only_blockers_do_not_fail_candidate_gate(self) -> None:
        manifest, key, review = fixtures()
        gate = renderer_ab.evaluate_gate(manifest, key, review)
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["klein_not_worse"], 8)
        self.assertEqual(gate["blockers"], [])

    def test_candidate_specific_blocker_fails_gate(self) -> None:
        manifest, key, review = fixtures()
        review["cases"][0]["blocking_issues"]["A"] = ["Candidate anatomy is invalid."]
        gate = renderer_ab.evaluate_gate(manifest, key, review)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["decision"], "keep_flux1_default")
        self.assertEqual(gate["blockers"][0]["label"], "A")


if __name__ == "__main__":
    unittest.main()
