from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "rendering.py"
SPEC = importlib.util.spec_from_file_location("evolab_rendering_tests", MODULE_PATH)
assert SPEC and SPEC.loader
rendering = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rendering
SPEC.loader.exec_module(rendering)


class RendererProfileTests(unittest.TestCase):
    def test_flux1_remains_the_default_profile(self) -> None:
        catalog = rendering.renderer_catalog()
        self.assertEqual(catalog["default_renderer"], "flux1")
        self.assertEqual(rendering.renderer_chain(None, "flux1"), ["flux1"])

    def test_flux1_workflow_contract_is_preserved(self) -> None:
        workflow, metadata = rendering.build_image_workflow(
            "flux1",
            prompt="same prompt",
            negative_prompt="scenario failure",
            seed=17,
            filename_prefix="ab/flux1",
        )
        self.assertEqual(workflow["1"]["inputs"]["ckpt_name"], "flux1-dev-fp8.safetensors")
        self.assertEqual(workflow["2"]["inputs"]["text"], "same prompt")
        self.assertIn("scenario failure", workflow["3"]["inputs"]["text"])
        self.assertEqual(workflow["6"]["inputs"]["seed"], 17)
        self.assertEqual(workflow["8"]["inputs"]["filename_prefix"], "ab/flux1")
        self.assertEqual(metadata["renderer"], "flux1")

    def test_klein_workflow_uses_official_distilled_contract(self) -> None:
        workflow, metadata = rendering.build_image_workflow(
            "flux2-klein-4b",
            prompt="same prompt",
            negative_prompt="text, watermark",
            seed=23,
            filename_prefix="ab/klein",
            width=768,
            height=1024,
        )
        self.assertEqual(
            workflow["1"]["inputs"]["unet_name"],
            "flux-2-klein-4b-fp8.safetensors",
        )
        self.assertEqual(workflow["2"]["inputs"]["type"], "flux2")
        self.assertEqual(workflow["6"]["inputs"]["cfg"], 1.0)
        self.assertEqual(workflow["9"]["inputs"]["steps"], 4)
        self.assertEqual(workflow["9"]["inputs"]["width"], 768)
        self.assertEqual(workflow["10"]["inputs"]["height"], 1024)
        self.assertEqual(workflow["7"]["inputs"]["noise_seed"], 23)
        self.assertIn("Avoid these failures", workflow["4"]["inputs"]["text"])
        self.assertEqual(metadata["renderer"], "flux2-klein-4b")

    def test_candidate_failure_falls_back_without_changing_flux1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "comfy.png"
            source.write_bytes(b"stable-flux1-output")
            destination = root / "stage.png"
            submitted: list[str] = []

            def submit(workflow: dict, _url: str) -> str:
                if workflow.get("1", {}).get("class_type") == "UNETLoader":
                    submitted.append("flux2-klein-4b")
                    raise RuntimeError("injected candidate failure")
                submitted.append("flux1")
                return "prompt-flux1"

            rendered = rendering.render_image_with_fallback(
                ["flux2-klein-4b", "flux1"],
                prompt="same prompt",
                negative_prompt="same negative",
                seed=31,
                filename_prefix="fallback",
                destination=destination,
                comfy_url="http://127.0.0.1:7000",
                comfy_output=root,
                timeout=5,
                submit_prompt=submit,
                wait_for_prompt=lambda *_args: {"ok": True},
                locate_output=lambda *_args: source,
            )
            self.assertEqual(submitted, ["flux2-klein-4b", "flux1"])
            self.assertEqual(rendered.renderer, "flux1")
            self.assertEqual(rendered.fallback_from, "flux2-klein-4b")
            self.assertEqual(destination.read_bytes(), b"stable-flux1-output")

    def test_unknown_renderer_fails_closed(self) -> None:
        with self.assertRaises(rendering.RendererConfigurationError):
            rendering.renderer_chain("unknown", "flux1")


if __name__ == "__main__":
    unittest.main()
