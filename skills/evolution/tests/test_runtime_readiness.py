from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "skills" / "evolution" / "runtime_readiness.py"
SPEC = importlib.util.spec_from_file_location("evolab_runtime_readiness_tests", MODULE_PATH)
assert SPEC and SPEC.loader
readiness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = readiness
SPEC.loader.exec_module(readiness)


class RuntimeReadinessTests(unittest.TestCase):
    def test_live_probe_checks_step_comfy_nodes_workflows_and_models(self) -> None:
        calls: list[str] = []

        def get_json(url: str, timeout: int):
            calls.append(f"GET {url} {timeout}")
            return {
                "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["flux1-dev-fp8.safetensors"]]}}},
                "UNETLoader": {"input": {"required": {"unet_name": [["flux-2-klein-4b-fp8.safetensors"]]}}},
                "CLIPLoader": {"input": {"required": {"clip_name": [["qwen_3_4b.safetensors"]]}}},
                "VAELoader": {"input": {"required": {"vae_name": [["flux2-vae.safetensors"]]}}},
                **{name: {} for name in [
                    "CLIPTextEncode", "FluxGuidance", "EmptySD3LatentImage", "KSampler",
                    "VAEDecode", "SaveImage", "LoadImage", "ImageScale", "VAEEncode",
                    "ImageScaleToTotalPixels",
                    "ConditioningZeroOut", "CFGGuider", "RandomNoise", "KSamplerSelect",
                    "Flux2Scheduler", "EmptyFlux2LatentImage", "SamplerCustomAdvanced", "ReferenceLatent",
                ]},
            }

        def post_json(url: str, body: dict, headers: dict, timeout: int):
            del headers
            calls.append(f"POST {url} {timeout}")
            return {
                "model": body["model"],
                "choices": [{"message": {"content": json.dumps({"ready": True})}}],
            }

        result = readiness.probe_runtime(
            dry_run=False,
            step_endpoint="https://api.stepfun.com/step_plan/v1/chat/completions",
            step_model="step-3.7-flash",
            step_key="secret",
            comfy_url="http://127.0.0.1:7000",
            renderer_ids=["flux2-klein-4b", "flux1"],
            renderer_catalog={
                "profiles": {
                    "flux1": {
                        "workflow_file": "creature_workflow.json",
                        "reference_workflow_file": "creature_workflow_flux1_reference.json",
                        "required_models": [{"directory": "diffusion_models", "filename": "flux1-dev-fp8.safetensors"}],
                    },
                    "flux2-klein-4b": {
                        "workflow_file": "creature_workflow_flux2_klein.json",
                        "reference_workflow_file": "creature_workflow_flux2_klein_edit.json",
                        "required_models": [
                            {"directory": "diffusion_models", "filename": "flux-2-klein-4b-fp8.safetensors"},
                            {"directory": "text_encoders", "filename": "qwen_3_4b.safetensors"},
                            {"directory": "vae", "filename": "flux2-vae.safetensors"},
                        ],
                    },
                }
            },
            workflow_root=ROOT / "skills" / "evolution",
            get_json=get_json,
            post_json=post_json,
            timeout=8,
        )
        self.assertTrue(result["ready"])
        self.assertTrue(result["components"]["step"]["strict_json"])
        self.assertTrue(result["components"]["comfyui"]["reachable"])
        self.assertEqual(result["components"]["models"]["missing"], [])
        self.assertEqual(result["components"]["workflows"]["missing_nodes"], [])
        self.assertEqual(len(calls), 2)

    def test_missing_model_makes_readiness_fail_without_leaking_configuration(self) -> None:
        result = readiness.probe_runtime(
            dry_run=False,
            step_endpoint="https://example.invalid/chat/completions",
            step_model="step-3.7-flash",
            step_key="secret-value",
            comfy_url="http://127.0.0.1:7000",
            renderer_ids=["flux1"],
            renderer_catalog={
                "profiles": {
                    "flux1": {
                        "workflow_file": "creature_workflow.json",
                        "reference_workflow_file": "creature_workflow_flux1_reference.json",
                        "required_models": [{"directory": "diffusion_models", "filename": "missing.safetensors"}],
                    }
                }
            },
            workflow_root=ROOT / "skills" / "evolution",
            get_json=lambda *_args: {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["other.safetensors"]]}}}},
            post_json=lambda _url, body, *_args: {"model": body["model"], "choices": [{"message": {"content": "{\"ready\": true}"}}]},
            timeout=8,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["components"]["models"]["missing"], ["missing.safetensors"])
        encoded = json.dumps(result)
        self.assertNotIn("secret-value", encoded)
        self.assertNotIn("example.invalid", encoded)


if __name__ == "__main__":
    unittest.main()
