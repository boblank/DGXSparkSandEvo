from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "video_generation.py"
SPEC = importlib.util.spec_from_file_location("evolab_video_generation_tests", MODULE_PATH)
assert SPEC and SPEC.loader
video = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = video
SPEC.loader.exec_module(video)


class HunyuanVideoWorkflowTests(unittest.TestCase):
    def test_workflow_matches_step_distilled_i2v_contract(self) -> None:
        workflow = video.build_workflow(
            input_image="stage_03.png",
            prompt="Subtle microbial movement only.",
            seed=73,
            filename_prefix="evolab/test",
        )
        self.assertEqual(
            workflow["1"]["inputs"]["unet_name"],
            "hunyuanvideo1.5_480p_i2v_step_distilled_fp8_scaled.safetensors",
        )
        self.assertEqual(workflow["2"]["inputs"]["type"], "hunyuan_video_15")
        self.assertEqual(workflow["5"]["inputs"]["image"], "stage_03.png")
        self.assertEqual(workflow["7"]["inputs"]["text"], "Subtle microbial movement only.")
        self.assertEqual(workflow["9"]["inputs"]["width"], 848)
        self.assertEqual(workflow["9"]["inputs"]["height"], 480)
        self.assertEqual(workflow["9"]["inputs"]["length"], 81)
        self.assertEqual(workflow["10"]["inputs"]["shift"], 7.0)
        self.assertEqual(workflow["11"]["inputs"]["cfg"], 1.0)
        self.assertEqual(workflow["12"]["inputs"]["noise_seed"], 73)
        self.assertEqual(workflow["14"]["inputs"]["steps"], 8)
        self.assertEqual(workflow["17"]["inputs"]["fps"], 24.0)
        self.assertEqual(workflow["18"]["inputs"]["codec"], "h264")

    def test_workflow_rejects_unsafe_or_unsupported_parameters(self) -> None:
        with self.assertRaises(ValueError):
            video.build_workflow(
                input_image="../stage.png",
                prompt="motion",
                seed=1,
                filename_prefix="test",
            )
        nested = video.build_workflow(
            input_image="evolab/stage.png",
            prompt="motion",
            seed=1,
            filename_prefix="test",
        )
        self.assertEqual(nested["5"]["inputs"]["image"], "evolab/stage.png")
        with self.assertRaises(ValueError):
            video.build_workflow(
                input_image="stage.png",
                prompt="motion",
                seed=1,
                filename_prefix="test",
                width=850,
            )
        with self.assertRaises(ValueError):
            video.build_workflow(
                input_image="stage.png",
                prompt="motion",
                seed=1,
                filename_prefix="test",
                steps=4,
            )

    def test_preflight_requires_every_model_node_and_codec(self) -> None:
        object_info = {node: {} for node in video.REQUIRED_NODES}
        object_info["UNETLoader"] = {
            "models": [asset["filename"] for asset in video.MODEL_ASSETS]
        }
        object_info["SaveVideo"] = {
            "input": {
                "required": {
                    "format": [["auto", "mp4"]],
                    "codec": [["auto", "h264"]],
                }
            }
        }
        result = video.preflight(object_info)
        self.assertTrue(result["passed"])
        del object_info["CLIPVisionEncode"]
        result = video.preflight(object_info)
        self.assertFalse(result["passed"])
        self.assertIn("CLIPVisionEncode", result["missing_nodes"])

    def test_locate_video_stays_inside_comfy_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "video"
            folder.mkdir()
            expected = folder / "result.mp4"
            expected.write_bytes(b"video")
            outputs = {"18": {"videos": [{"filename": "result.mp4", "subfolder": "video"}]}}
            self.assertEqual(video.locate_video(outputs, root), expected.resolve())
            with self.assertRaises(video.VideoGenerationError):
                video.locate_video(
                    {"18": {"videos": [{"filename": "escape.mp4", "subfolder": "../"}]}},
                    root,
                )

    def test_model_asset_contract_is_complete_and_unique(self) -> None:
        filenames = [asset["filename"] for asset in video.MODEL_ASSETS]
        self.assertEqual(len(filenames), 5)
        self.assertEqual(len(filenames), len(set(filenames)))
        self.assertTrue(all(asset["bytes"] > 100_000_000 for asset in video.MODEL_ASSETS))
        self.assertTrue(all(len(asset["sha256"]) == 64 for asset in video.MODEL_ASSETS))


if __name__ == "__main__":
    unittest.main()
