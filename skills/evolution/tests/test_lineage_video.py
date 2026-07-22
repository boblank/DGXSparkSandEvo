from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "lineage_video.py"
SPEC = importlib.util.spec_from_file_location("lineage_video", MODULE_PATH)
assert SPEC and SPEC.loader
lineage_video = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lineage_video)


class LineageVideoTests(unittest.TestCase):
    def test_plan_uses_four_stage_images_and_saved_chinese_choices(self) -> None:
        try:
            from PIL import Image, ImageChops, ImageDraw
        except ImportError as exc:  # pragma: no cover - the encoder runtime ships Pillow
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "stage_00_origin.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"></svg>',
                encoding="utf-8",
            )
            for round_no in range(1, 4):
                image = Image.new("RGB", (48, 32), (20 * round_no, 60, 80))
                draw = ImageDraw.Draw(image)
                draw.ellipse((8 + round_no, 5, 35, 27), fill=(180, 130, 70))
                image.save(root / f"stage_{round_no:02d}.png")

            history = [
                {
                    "round": 0,
                    "scenario_id": "devonian_estuary",
                    "organism_name": "泥盆纪河口的肉鳍鱼",
                    "change_summary": "路线从河口浅水开始。",
                    "image_url": "/api/assets/example/stage_00_origin.svg",
                }
            ]
            for round_no in range(1, 4):
                history.append(
                    {
                        "round": round_no,
                        "scenario_id": "devonian_estuary",
                        "organism_name": "English model output that must not reach the recap",
                        "change_summary": "English change summary",
                        "image_url": f"/api/assets/example/stage_{round_no:02d}.png",
                        "selection": {
                            "environment": {"title": f"第{round_no}轮浅水环境"},
                            "contingency": {"title": f"第{round_no}轮偶发扰动"},
                            "direction": {
                                "title": f"第{round_no}轮用户选择",
                                "description": f"第{round_no}轮留下的变化与代价。",
                            },
                        },
                    }
                )
            session = {
                "status": "completed",
                "session_id": "20260722T014150-22f27057",
                "scenario_id": "devonian_estuary",
                "updated_at": "2026-07-22T01:50:00Z",
                "scenario": {"title": "泥盆纪河口", "habitat": "水陆边缘"},
                "history": history,
            }

            with mock.patch.object(lineage_video.shutil, "which", return_value=None):
                plan = lineage_video.build_recap_plan(session, root)
            self.assertEqual(plan["input_stage_count"], 4)
            self.assertEqual([stage["round"] for stage in plan["stages"]], [0, 1, 2, 3])
            self.assertEqual(plan["stages"][0]["source_kind"], "vector_unavailable")
            self.assertEqual(plan["stages"][3]["source_kind"], "raster")
            self.assertEqual(plan["stages"][2]["choice"], "第2轮用户选择")
            self.assertEqual(plan["stages"][2]["change"], "第2轮留下的变化与代价。")
            self.assertEqual(
                plan["stages"][0]["visible_copy"],
                {"eyebrow": "起点", "headline": "泥盆纪河口的肉鳍鱼"},
            )
            self.assertEqual(
                plan["stages"][2]["visible_copy"],
                {"eyebrow": "第 2 次改变", "headline": "第2轮用户选择"},
            )
            self.assertEqual(len(plan["stages"][2]["visible_copy"]), 2)
            self.assertNotIn("English", str(plan["stages"]))
            card = lineage_video.render_stage_card(plan["stages"][2])
            self.assertEqual(card.size, (1280, 720))
            moving_card = lineage_video.render_stage_card(plan["stages"][2], progress=0.8)
            self.assertIsNotNone(ImageChops.difference(card, moving_card).getbbox())
            self.assertEqual(lineage_video.expected_frame_count(4), 234)

    def test_svg_renders_without_a_system_rasterizer(self) -> None:
        stage = {
            "image": str(ROOT / "demo-assets" / "interactive" / "origin-devonian.svg"),
            "round": 0,
            "chapter": "起点",
            "title": "泥盆纪河口",
            "visible_copy": {"eyebrow": "起点", "headline": "泥盆纪河口"},
        }
        with mock.patch.object(lineage_video.shutil, "which", return_value=None):
            card = lineage_video.render_stage_card(stage)
        self.assertEqual(card.size, (1280, 720))


if __name__ == "__main__":
    unittest.main()
