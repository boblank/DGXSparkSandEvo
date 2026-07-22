from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SERVER_PATH = ROOT / "demo-ui" / "server.py"
SPEC = importlib.util.spec_from_file_location("interactive_server", SERVER_PATH)
assert SPEC and SPEC.loader
server_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server_module)


class InteractiveServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        service = server_module.engine.InteractiveEvolutionService(
            Path(self.temporary.name),
            dry_run=True,
        )
        self.service = service

        class QuietHandler(server_module.EvoLabHandler):
            def log_message(self, format_string: str, *args) -> None:
                return

        QuietHandler.service = service
        handler = functools.partial(QuietHandler, directory=str(ROOT))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _json_request(self, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST" if payload is not None else "GET",
            headers={"Content-Type": "application/json"} if payload is not None else {},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_health_session_evolve_and_asset_contract(self) -> None:
        status, health = self._json_request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["mode"], "fixture")
        self.assertTrue(health["strict_schema"])
        self.assertEqual(health["readiness"]["url"], "/api/readiness")

        status, readiness = self._json_request("/api/readiness")
        self.assertEqual(status, 200)
        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["components"]["step"]["ready"])

        status, envelope = self._json_request("/api/sessions", {})
        self.assertEqual(status, 201)
        session_id = envelope["session"]["session_id"]
        choices = envelope["choices"]
        payload = {
            "environment_id": choices["environments"][0]["id"],
            "contingency_id": choices["contingencies"][0]["id"],
            "direction_id": choices["directions"][0]["id"],
            "expected_round": 1,
        }
        status, evolved = self._json_request(f"/api/sessions/{session_id}/evolve", payload)
        self.assertEqual(status, 200)
        self.assertEqual(evolved["session"]["round_index"], 1)
        asset_url = evolved["session"]["current_stage"]["image_url"]
        with urllib.request.urlopen(self.base_url + asset_url, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
            self.assertTrue(response.read().startswith(b"<svg"))

    def test_public_trace_has_a_real_scientific_review_checkpoint(self) -> None:
        index = (ROOT / "demo-ui" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "demo-ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="trace-review"', index)
        self.assertIn("科学审查", index)
        self.assertIn("stage.review_summary", app)
        self.assertNotIn("chain of thought", index.lower())

    def test_readiness_returns_503_when_a_dependency_is_not_ready(self) -> None:
        self.service.readiness = lambda **_kwargs: {
            "ready": False,
            "mode": "live",
            "components": {"models": {"ready": False, "missing": ["model.safetensors"]}},
        }
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.base_url + "/api/readiness", timeout=5)
        self.assertEqual(context.exception.code, 503)
        payload = json.loads(context.exception.read())
        self.assertFalse(payload["ready"])

    def test_health_response_survives_closed_log_stream(self) -> None:
        class ClosedLogStream:
            def write(self, _message: str) -> int:
                raise BrokenPipeError("log consumer closed")

            def flush(self) -> None:
                return

        handler = functools.partial(server_module.EvoLabHandler, directory=str(ROOT))
        server_module.EvoLabHandler.service = self.service
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.object(server_module.sys, "stderr", ClosedLogStream()):
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/health",
                    timeout=5,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read())["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_environment_choice_endpoint_recomputes_candidates(self) -> None:
        status, envelope = self._json_request(
            "/api/sessions",
            {"scenario_id": "devonian_estuary"},
        )
        self.assertEqual(status, 201)
        session_id = envelope["session"]["session_id"]
        status, first = self._json_request(
            f"/api/sessions/{session_id}/choices",
            {"expected_round": 1, "environment_id": "oxygen_poor_pool"},
        )
        self.assertEqual(status, 200)
        status, second = self._json_request(
            f"/api/sessions/{session_id}/choices",
            {"expected_round": 1, "environment_id": "weedy_shallows"},
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(
            [item["id"] for item in first["directions"]],
            [item["id"] for item in second["directions"]],
        )
        self.assertTrue(all(item.get("context_reason") for item in second["directions"]))

    def test_svg_origin_scenario_produces_a_lineage_video(self) -> None:
        status, envelope = self._json_request(
            "/api/sessions",
            {"scenario_id": "devonian_estuary"},
        )
        self.assertEqual(status, 201)
        session_id = envelope["session"]["session_id"]
        for expected_round in range(1, 4):
            choices = envelope["choices"]
            status, envelope = self._json_request(
                f"/api/sessions/{session_id}/evolve",
                {
                    "environment_id": choices["environments"][0]["id"],
                    "contingency_id": choices["contingencies"][0]["id"],
                    "direction_id": choices["directions"][0]["id"],
                    "expected_round": expected_round,
                },
            )
            self.assertEqual(status, 200)
        self.assertEqual(envelope["session"]["status"], "completed")

        with urllib.request.urlopen(
            self.base_url + f"/api/sessions/{session_id}/lineage-video",
            timeout=30,
        ) as response:
            payload = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "video/mp4")
            self.assertIn(b"ftyp", payload[:32])

    def test_scenario_registry_and_scene_selection_contract(self) -> None:
        status, registry = self._json_request("/api/scenarios")
        self.assertEqual(status, 200)
        self.assertEqual(len(registry["scenarios"]), 7)
        self.assertEqual(registry["default_scenario_id"], "tidal_symbiosis")

        status, envelope = self._json_request(
            "/api/sessions",
            {"scenario_id": "ediacaran_seafloor"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(envelope["session"]["scenario_id"], "ediacaran_seafloor")
        self.assertEqual(
            envelope["choices"]["chapter"],
            "海床不再安静",
        )

    def test_static_server_does_not_expose_environment_or_source_files(self) -> None:
        for path in ("/.env", "/skills/evolution/interactive_engine.py", "/demo-ui/../.env"):
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(self.base_url + path, timeout=5)
            self.assertEqual(context.exception.code, 404)

    def test_final_round_announces_video_before_and_during_generation(self) -> None:
        index = (ROOT / "demo-ui" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "demo-ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("最后一轮之后还有一段回放", index)
        self.assertIn("选完这一轮，继续等到视频出现", index)
        self.assertIn("第 2 / 2 步 · 正在制作回放", index)
        self.assertIn("约 10 秒视频", index)
        self.assertIn("生成最终阶段并制作回放", app)
        self.assertIn("第 1 / 2 步：最终阶段正在生成。", app)
        self.assertIn("先别离开，四阶段回放还在生成", app)
        self.assertIn("约 10 秒视频", app)
        self.assertIn("视频已经生成", app)
        self.assertIn("watchEndingVideoReady(session.session_id, Date.now() + 30000)", app)
        self.assertIn('["loadeddata", "canplay"]', app)

    def test_retained_hunyuan_video_matches_public_manifest(self) -> None:
        asset_root = ROOT / "demo-assets" / "video"
        manifest = json.loads(
            (asset_root / "tidal-symbiosis-dgx.json").read_text(encoding="utf-8")
        )
        video = asset_root / manifest["asset"]
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        self.assertEqual(digest, manifest["output"]["sha256"])
        request = urllib.request.Request(
            self.base_url + "/demo-assets/video/" + manifest["asset"],
            method="HEAD",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "video/mp4")
            self.assertEqual(int(response.headers["Content-Length"]), video.stat().st_size)
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")

        range_request = urllib.request.Request(
            self.base_url + "/demo-assets/video/" + manifest["asset"],
            headers={"Range": "bytes=0-1023"},
        )
        with urllib.request.urlopen(range_request, timeout=5) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"], f"bytes 0-1023/{video.stat().st_size}")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.read(), video.read_bytes()[:1024])

        invalid_request = urllib.request.Request(
            self.base_url + "/demo-assets/video/" + manifest["asset"],
            headers={"Range": f"bytes={video.stat().st_size}-"},
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(invalid_request, timeout=5)
        self.assertEqual(context.exception.code, 416)
        self.assertEqual(
            context.exception.headers["Content-Range"],
            f"bytes */{video.stat().st_size}",
        )

    def test_session_lineage_video_endpoint_supports_browser_range_requests(self) -> None:
        video = ROOT / "demo-assets" / "video" / "tidal-symbiosis-dgx.mp4"
        session_id = "20260722T014150-22f27057"
        self.service.lineage_video_path = lambda requested: video if requested == session_id else None
        request = urllib.request.Request(
            self.base_url + f"/api/sessions/{session_id}/lineage-video",
            headers={"Range": "bytes=32-1055"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers.get_content_type(), "video/mp4")
            self.assertEqual(
                response.headers["Content-Range"],
                f"bytes 32-1055/{video.stat().st_size}",
            )
            self.assertEqual(response.read(), video.read_bytes()[32:1056])


if __name__ == "__main__":
    unittest.main()
