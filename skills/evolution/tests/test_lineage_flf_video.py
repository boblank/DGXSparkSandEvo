from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "lineage_flf_video.py"
SPEC = importlib.util.spec_from_file_location("lineage_flf_video_tests", MODULE_PATH)
assert SPEC and SPEC.loader
video = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = video
SPEC.loader.exec_module(video)


class LineageFirstLastFrameVideoTests(unittest.TestCase):
    def test_workflow_binds_both_endpoints(self) -> None:
        workflow = video.build_workflow(
            start_image="lineage/start.png",
            end_image="lineage/end.png",
            prompt="同一条鱼的成对肉质鳍逐步变得更能承重。",
            seed=73,
            filename_prefix="evolab/test",
        )
        self.assertEqual(workflow["1"]["inputs"]["unet_name"], video.MODEL_ASSETS[0]["filename"])
        self.assertEqual(workflow["5"]["inputs"]["image"], "lineage/start.png")
        self.assertEqual(workflow["6"]["inputs"]["image"], "lineage/end.png")
        self.assertEqual(workflow["11"]["inputs"]["start_image"], ["5", 0])
        self.assertEqual(workflow["11"]["inputs"]["end_image"], ["6", 0])
        self.assertEqual(workflow["11"]["inputs"]["width"], 1280)
        self.assertEqual(workflow["11"]["inputs"]["height"], 720)
        self.assertEqual(workflow["13"]["inputs"]["seed"], 73)
        self.assertEqual(workflow["15"]["inputs"]["fps"], 16.0)

    def test_workflow_rejects_unsafe_inputs_and_lengths(self) -> None:
        with self.assertRaises(ValueError):
            video.build_workflow(
                start_image="../start.png",
                end_image="end.png",
                prompt="变化",
                seed=1,
                filename_prefix="test",
            )
        with self.assertRaises(ValueError):
            video.build_workflow(
                start_image="start.png",
                end_image="end.png",
                prompt="变化",
                seed=1,
                filename_prefix="test",
                length=32,
            )

    def test_plan_uses_three_adjacent_stage_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history = []
            for round_no in range(4):
                image = root / f"stage_{round_no:02d}.png"
                image.write_bytes(b"png-fixture")
                history.append(
                    {
                        "round": round_no,
                        "image_url": image.name,
                        "organism_name": f"阶段 {round_no}",
                        "selection": None
                        if round_no == 0
                        else {
                            "direction": {
                                "title": f"第 {round_no} 次变化",
                                "description": "只改变本轮指定性状。",
                            }
                        },
                    }
                )
            session = {"status": "completed", "history": history}
            plan = video.plan_segments(session, root)
            self.assertEqual(len(plan), 3)
            self.assertEqual(
                [(item["start_round"], item["end_round"]) for item in plan],
                [(0, 1), (1, 2), (2, 3)],
            )
            self.assertIn("同一条谱系", plan[1]["prompt"])

    def test_preflight_reports_missing_flf_model(self) -> None:
        object_info = {name: {} for name in video.REQUIRED_NODES}
        object_info["UNETLoader"] = {"models": [item["filename"] for item in video.MODEL_ASSETS[1:]]}
        result = video.preflight(object_info)
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_models"], [video.MODEL_ASSETS[0]["filename"]])

    def test_merge_keeps_first_frame_and_trims_later_seams(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segments = [root / f"segment_{index}.mp4" for index in range(3)]
            for segment in segments:
                segment.write_bytes(b"video-fixture")
            output = root / "merged.mp4"
            captured: dict[str, object] = {}

            def fake_run(command: list[str], **kwargs: object) -> None:
                captured["command"] = command
                output.write_bytes(b"merged-video-fixture")

            original_run = subprocess.run
            original_sha256 = video.shared.sha256
            subprocess.run = fake_run
            video.shared.sha256 = lambda _path: "fixture-sha"
            try:
                result = video.merge_segments(segments, output, 16.0)
            finally:
                subprocess.run = original_run
                video.shared.sha256 = original_sha256

            command = captured["command"]
            self.assertIsInstance(command, list)
            filter_complex = command[command.index("-filter_complex") + 1]
            self.assertIn("[0:v]setpts=PTS-STARTPTS[v0]", filter_complex)
            self.assertNotIn("[0:v],", filter_complex)
            self.assertIn(
                "[1:v]trim=start_frame=1,setpts=PTS-STARTPTS[v1]",
                filter_complex,
            )
            self.assertEqual(result["sha256"], "fixture-sha")


if __name__ == "__main__":
    unittest.main()
