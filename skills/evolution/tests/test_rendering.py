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

    def test_klein_reference_workflow_conditions_on_previous_stage(self) -> None:
        workflow, metadata = rendering.build_image_workflow(
            "flux2-klein-4b",
            prompt="preserve the parent body plan",
            negative_prompt="missing appendages",
            seed=29,
            filename_prefix="lineage/stage_02",
            reference_image="evolab_parent.png",
        )
        self.assertEqual(workflow["14"]["inputs"]["image"], "evolab_parent.png")
        self.assertEqual(workflow["16"]["class_type"], "VAEEncode")
        self.assertEqual(workflow["17"]["class_type"], "ReferenceLatent")
        self.assertEqual(workflow["18"]["class_type"], "ReferenceLatent")
        self.assertEqual(workflow["6"]["inputs"]["positive"], ["17", 0])
        self.assertTrue(metadata["reference_conditioning"])
        self.assertEqual(
            metadata["workflow_file"], "creature_workflow_flux2_klein_edit.json"
        )

    def test_klein_renderer_uploads_parent_for_reference_conditioning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "stage_01.png"
            parent.write_bytes(b"parent-image")
            source = root / "comfy.png"
            source.write_bytes(b"edited-descendant")
            destination = root / "stage_02.png"
            uploads: list[tuple[Path, str]] = []

            def upload(path: Path, url: str) -> str:
                uploads.append((path, url))
                return "evolab_parent.png"

            def submit(workflow: dict, _url: str) -> str:
                self.assertEqual(
                    workflow["14"]["inputs"]["image"], "evolab_parent.png"
                )
                return "prompt-edit"

            rendered = rendering.render_image_with_fallback(
                ["flux2-klein-4b"],
                prompt="same lineage, gradual change",
                negative_prompt="missing appendages",
                seed=37,
                filename_prefix="reference-edit",
                destination=destination,
                comfy_url="http://127.0.0.1:7000",
                comfy_output=root,
                timeout=5,
                submit_prompt=submit,
                wait_for_prompt=lambda *_args: {"ok": True},
                locate_output=lambda *_args: source,
                reference_image=parent,
                upload_reference=upload,
            )
            self.assertEqual(uploads, [(parent, "http://127.0.0.1:7000")])
            self.assertTrue(rendered.reference_conditioning)
            self.assertEqual(destination.read_bytes(), b"edited-descendant")

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

    def test_reference_render_never_drops_to_an_unconditioned_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "stage_01.png"
            parent.write_bytes(b"parent-image")
            with self.assertRaises(rendering.RendererExhaustedError) as raised:
                rendering.render_image_with_fallback(
                    ["flux1"],
                    prompt="preserve the same lineage",
                    negative_prompt="unrelated species",
                    seed=41,
                    filename_prefix="reference-required",
                    destination=root / "stage_02.png",
                    comfy_url="http://127.0.0.1:7000",
                    comfy_output=root,
                    timeout=5,
                    submit_prompt=lambda *_args: "must-not-submit",
                    wait_for_prompt=lambda *_args: {},
                    locate_output=lambda *_args: root / "missing.png",
                    reference_image=parent,
                    upload_reference=lambda *_args: "parent.png",
                )
            self.assertEqual(
                raised.exception.attempts,
                [{"renderer": "flux1", "error": "RendererConfigurationError"}],
            )

    def test_unknown_renderer_fails_closed(self) -> None:
        with self.assertRaises(rendering.RendererConfigurationError):
            rendering.renderer_chain("unknown", "flux1")


if __name__ == "__main__":
    unittest.main()
