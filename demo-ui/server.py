#!/usr/bin/env python3
"""Serve the EvoLab UI and its stateful evolution API with Python stdlib only."""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import mimetypes
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = REPO_ROOT / "skills" / "evolution" / "interactive_engine.py"


def _load_engine() -> Any:
    spec = importlib.util.spec_from_file_location("evolab_interactive_engine", ENGINE_PATH)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load interactive engine")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = _load_engine()


class EvoLabHandler(SimpleHTTPRequestHandler):
    """Same-origin static and JSON handler."""

    protocol_version = "HTTP/1.1"
    server_version = "EvoLab/0.6"
    service: Any

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_api_error(self, exc: Exception) -> None:
        if isinstance(exc, engine.InteractiveError):
            self._send_json(
                exc.http_status,
                {
                    "error": {
                        "code": exc.code,
                        "message": exc.public_message,
                        "retryable": exc.retryable,
                    }
                },
            )
            return
        self._send_json(
            500,
            {
                "error": {
                    "code": "internal_error",
                    "message": "服务刚才打了个趔趄，请稍后再试。",
                    "retryable": True,
                }
            },
        )

    def _read_json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise engine.InteractiveError("invalid_request", "请求格式不正确。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise engine.InteractiveError("invalid_request", "请求格式不正确。") from exc
        if length <= 0 or length > 64 * 1024:
            raise engine.InteractiveError("invalid_request", "请求内容为空或过大。")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise engine.InteractiveError("invalid_request", "请求内容不是有效的 JSON。") from exc
        if not isinstance(payload, dict):
            raise engine.InteractiveError("invalid_request", "请求内容必须是一个对象。")
        return payload

    @staticmethod
    def _public_static_path(path: str) -> bool:
        """Expose only UI assets; never expose .env, source, runs, or internal docs."""
        parts = [part for part in path.split("/") if part]
        if not parts or parts[0] not in {"demo-ui", "demo-assets"}:
            return False
        return all(part not in {".", ".."} and not part.startswith(".") for part in parts)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
        if urlparse(self.path).path.startswith("/api/"):
            self.send_response(204)
            self.send_header("Allow", "GET, POST, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/health":
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "contract_version": self.service.contract_version,
                        "mode": "fixture" if self.service.dry_run else "live",
                        "planner": "fixture" if self.service.dry_run else engine.STEP_MODEL,
                        "reasoning_effort": "not_applicable" if self.service.dry_run else "high",
                        "strict_schema": True,
                    },
                )
                return
            match = re.fullmatch(r"/api/sessions/([0-9]{8}T[0-9]{6}-[a-f0-9]{8})", path)
            if match:
                self._send_json(200, self.service.get_session(match.group(1)))
                return
            asset = re.fullmatch(
                r"/api/assets/([0-9]{8}T[0-9]{6}-[a-f0-9]{8})/([^/]+)", path
            )
            if asset:
                self._send_asset(self.service.asset_path(asset.group(1), asset.group(2)))
                return
            if path.startswith("/api/"):
                raise engine.InteractiveError("not_found", "没有找到这个接口。", http_status=404)
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/demo-ui/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not self._public_static_path(path):
                self.send_error(404)
                return
            super().do_GET()
        except Exception as exc:
            self._send_api_error(exc)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        path = unquote(urlparse(self.path).path)
        if not self._public_static_path(path):
            self.send_error(404)
            return
        super().do_HEAD()

    def _send_asset(self, path: Path) -> None:
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/sessions":
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length:
                    payload = self._read_json_body()
                    if payload:
                        raise engine.InteractiveError(
                            "invalid_request",
                            "新建谱系不需要额外参数。",
                        )
                self._send_json(201, self.service.create_session())
                return
            match = re.fullmatch(
                r"/api/sessions/([0-9]{8}T[0-9]{6}-[a-f0-9]{8})/evolve", path
            )
            if match:
                payload = self._read_json_body()
                self._send_json(200, self.service.evolve(match.group(1), payload))
                return
            raise engine.InteractiveError("not_found", "没有找到这个接口。", http_status=404)
        except Exception as exc:
            self._send_api_error(exc)

    def log_message(self, format_string: str, *args: Any) -> None:
        # Do not log request bodies, headers, environment variables, or downstream errors.
        sys.stderr.write("[evolab-http] " + (format_string % args) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the interactive EvoLab demo")
    parser.add_argument("--host", default=os.environ.get("EVOLAB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("EVOLAB_PORT", "8088")))
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(os.environ.get("EVOLAB_SESSION_ROOT", str(REPO_ROOT / "runs" / "interactive"))),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("EVOLAB_DRY_RUN", "0") == "1",
    )
    parser.add_argument("--step-timeout", type=int, default=240)
    parser.add_argument("--comfy-timeout", type=int, default=900)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    service = engine.InteractiveEvolutionService(
        args.data_root,
        dry_run=args.dry_run,
        step_timeout=args.step_timeout,
        comfy_timeout=args.comfy_timeout,
    )
    handler = functools.partial(EvoLabHandler, directory=str(REPO_ROOT))
    EvoLabHandler.service = service
    server = ThreadingHTTPServer((args.host, args.port), handler)
    mode = "fixture" if args.dry_run else "live"
    print(f"EvoLab {mode} server: http://{args.host}:{args.port}/demo-ui/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
