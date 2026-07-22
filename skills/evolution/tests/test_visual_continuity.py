from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "visual_continuity.py"
SPEC = importlib.util.spec_from_file_location("evolab_visual_continuity_tests", MODULE_PATH)
assert SPEC and SPEC.loader
visual = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = visual
SPEC.loader.exec_module(visual)


class VisualContinuityTests(unittest.TestCase):
    def test_step_visual_schema_avoids_unsupported_unique_items(self) -> None:
        self.assertNotIn("uniqueItems", json.dumps(visual.visual_review_schema(["成对附肢"])))

    def test_technical_gate_reports_structure_not_semantic_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = Image.new("RGB", (128, 128), "#a7bec0")
            draw = ImageDraw.Draw(parent)
            draw.ellipse((24, 42, 104, 86), fill="#416d72")
            draw.polygon([(24, 64), (8, 48), (8, 80)], fill="#416d72")
            parent_path = root / "parent.png"
            parent.save(parent_path)

            child = parent.copy()
            draw = ImageDraw.Draw(child)
            draw.ellipse((62, 78, 82, 102), fill="#355f64")
            child_path = root / "child.png"
            child.save(child_path)

            result = visual.technical_visual_gate(parent_path, child_path)
            self.assertTrue(result["passed"])
            self.assertEqual(result["mode"], "technical_structure_only")
            self.assertEqual(result["semantic_identity"], "not_assessed")
            self.assertGreater(result["edge_energy_parent"], 0)

    def test_blank_or_structureless_output_fails_even_if_rms_is_in_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent.png"
            child = root / "child.png"
            Image.new("RGB", (64, 64), (0, 0, 0)).save(parent)
            Image.new("RGB", (64, 64), (30, 30, 30)).save(child)
            result = visual.technical_visual_gate(parent, child)
            self.assertFalse(result["passed"])
            self.assertIn("STRUCTURELESS_IMAGE", result["issue_codes"])

    def test_step_visual_adapter_sends_two_images_and_returns_strict_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent.png"
            child = root / "child.png"
            Image.new("RGB", (32, 32), "navy").save(parent)
            Image.new("RGB", (32, 32), "teal").save(child)
            captured: dict = {}

            def post_json(url, body, headers, timeout):
                captured.update(url=url, body=body, headers=headers, timeout=timeout)
                return {
                    "model": "step-3.7-flash",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "verdict": "pass",
                                        "identity_continuity": True,
                                        "protected_traits": [
                                            {"trait": "成对附肢", "status": "present"}
                                        ],
                                        "forbidden_findings": [],
                                        "summary": "主体仍可辨认，关键附肢得到保留。",
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ],
                }

            decision = visual.review_with_step(
                parent=parent,
                descendant=child,
                protected_traits=["成对附肢"],
                forbidden_traits=["翅膀"],
                endpoint="https://api.stepfun.com/step_plan/v1/chat/completions",
                model="step-3.7-flash",
                api_key="test-key",
                timeout=30,
                post_json=post_json,
                validate_schema=lambda *_args: [],
            )
            content = captured["body"]["messages"][1]["content"]
            self.assertEqual([part["type"] for part in content], ["image_url", "image_url", "text"])
            self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertEqual(decision["verdict"], "pass")
            self.assertTrue(decision["identity_continuity"])

    def test_absence_notes_are_not_treated_as_observed_forbidden_features(self) -> None:
        decision = visual._normalize_visual_decision(
            {
                "verdict": "block",
                "identity_continuity": True,
                "protected_traits": [
                    {"trait": "成对附肢", "status": "present"},
                ],
                "forbidden_findings": [
                    "separate digits: absent",
                    "未发现现代两栖类足部结构",
                ],
                "summary": "关键性状仍然可见，禁画项均未出现。",
            },
            ["成对附肢"],
        )
        self.assertEqual(decision["verdict"], "pass")
        self.assertEqual(decision["forbidden_findings"], [])

    def test_observed_forbidden_feature_still_blocks(self) -> None:
        decision = visual._normalize_visual_decision(
            {
                "verdict": "pass",
                "identity_continuity": True,
                "protected_traits": [
                    {"trait": "成对附肢", "status": "present"},
                ],
                "forbidden_findings": ["separate digits are clearly visible"],
                "summary": "候选图提前出现了分离的指骨。",
            },
            ["成对附肢"],
        )
        self.assertEqual(decision["verdict"], "block")
        self.assertEqual(
            decision["forbidden_findings"],
            ["separate digits are clearly visible"],
        )


if __name__ == "__main__":
    unittest.main()
