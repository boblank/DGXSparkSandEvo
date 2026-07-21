from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EVOLUTION_DIR = ROOT / "skills" / "evolution"
sys.path.insert(0, str(EVOLUTION_DIR))
MODULE_PATH = EVOLUTION_DIR / "install_hunyuan_video_models.py"
SPEC = importlib.util.spec_from_file_location("evolab_hunyuan_installer_tests", MODULE_PATH)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


def contract(payload: bytes) -> dict:
    return {
        "directory": "diffusion_models",
        "filename": "model.safetensors",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


class HunyuanModelInstallerTests(unittest.TestCase):
    def test_verified_asset_is_installed_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            models = root / "models"
            staging.mkdir()
            payload = b"verified-model"
            source = staging / "model.safetensors"
            source.write_bytes(payload)
            records = installer.install_assets([contract(payload)], staging, models)
            destination = models / "diffusion_models" / "model.safetensors"
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(source.read_bytes(), payload)
            self.assertEqual(records[0]["status"], "installed")
            self.assertEqual(source.stat().st_ino, destination.stat().st_ino)

    def test_existing_mismatched_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            destination = root / "models" / "diffusion_models" / "model.safetensors"
            staging.mkdir()
            destination.parent.mkdir(parents=True)
            payload = b"verified-model"
            (staging / "model.safetensors").write_bytes(payload)
            destination.write_bytes(b"do-not-overwrite")
            with self.assertRaises(installer.ModelInstallError):
                installer.install_assets([contract(payload)], staging, root / "models")
            self.assertEqual(destination.read_bytes(), b"do-not-overwrite")

    def test_staged_hash_mismatch_fails_before_destination_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir()
            (staging / "model.safetensors").write_bytes(b"corrupt")
            with self.assertRaises(installer.ModelInstallError):
                installer.install_assets(
                    [contract(b"correct")], staging, root / "models", mode="copy"
                )
            self.assertFalse((root / "models" / "diffusion_models" / "model.safetensors").exists())


if __name__ == "__main__":
    unittest.main()
