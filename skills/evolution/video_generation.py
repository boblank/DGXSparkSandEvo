#!/usr/bin/env python3
"""Generate and strongly validate one EvoLab HunyuanVideo-1.5 I2V asset.

The video is an offline demo artifact. It never sits on the critical path of
the three-round interactive evolution session.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_FILE = SCRIPT_DIR / "hunyuan_video_workflow.json"
DEFAULT_COMFY_OUTPUT = Path(
    "/home/Developer/build_a_claw_workshop-bundle/comfyui-app/ComfyUI/output"
)
DEFAULT_PROMPT = (
    "A slow cinematic push-in through an ancient shallow tidal pool. "
    "The translucent microbial colony remains attached to the mineral surface; "
    "its existing outer membranes gently undulate, amber symbiotic partners shift slowly "
    "within the existing cell bodies, and suspended particles move with a mild current. "
    "Preserve the exact organisms, anatomy, scale, composition, lighting, and colors "
    "from the source frame. Subtle physically plausible motion only; no new organisms, "
    "no new organs, no transformation, no predation, no text, no watermark."
)

MODEL_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "directory": "diffusion_models",
        "filename": "hunyuanvideo1.5_480p_i2v_step_distilled_fp8_scaled.safetensors",
        "bytes": 8_335_127_098,
        "sha256": "302636263ad01e2659a18b78e96e95f44433b92def7cae1dab29b5105eeb63b1",
        "source": "https://huggingface.co/Comfy-Org/HunyuanVideo_1.5_repackaged/resolve/main/split_files/diffusion_models/hunyuanvideo1.5_480p_i2v_step_distilled_fp8_scaled.safetensors",
    },
    {
        "directory": "text_encoders",
        "filename": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "bytes": 9_384_670_680,
        "sha256": "cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4",
        "source": "https://huggingface.co/Comfy-Org/HunyuanVideo_1.5_repackaged/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
    },
    {
        "directory": "text_encoders",
        "filename": "byt5_small_glyphxl_fp16.safetensors",
        "bytes": 438_643_184,
        "sha256": "516910bb4c9b225370290e40585d1b0e6c8cd3583690f7eec2f7fb593990fb48",
        "source": "https://huggingface.co/Comfy-Org/HunyuanVideo_1.5_repackaged/resolve/main/split_files/text_encoders/byt5_small_glyphxl_fp16.safetensors",
    },
    {
        "directory": "vae",
        "filename": "hunyuanvideo15_vae_fp16.safetensors",
        "bytes": 2_521_292_758,
        "sha256": "e7c3091949c27e2d55ae6d5df917b99dadfebbf308e5a50d0ade0d16c90297ae",
        "source": "https://huggingface.co/Comfy-Org/HunyuanVideo_1.5_repackaged/resolve/main/split_files/vae/hunyuanvideo15_vae_fp16.safetensors",
    },
    {
        "directory": "clip_vision",
        "filename": "sigclip_vision_patch14_384.safetensors",
        "bytes": 856_505_640,
        "sha256": "1fee501deabac72f0ed17610307d7131e3e9d1e838d0363aa3c2b97a6e03fb33",
        "source": "https://huggingface.co/Comfy-Org/sigclip_vision_384/resolve/main/sigclip_vision_patch14_384.safetensors",
    },
)

REQUIRED_NODES = {
    "UNETLoader",
    "DualCLIPLoader",
    "VAELoader",
    "CLIPVisionLoader",
    "LoadImage",
    "CLIPVisionEncode",
    "CLIPTextEncode",
    "HunyuanVideo15ImageToVideo",
    "ModelSamplingSD3",
    "CFGGuider",
    "RandomNoise",
    "KSamplerSelect",
    "BasicScheduler",
    "SamplerCustomAdvanced",
    "VAEDecode",
    "CreateVideo",
    "SaveVideo",
}


class VideoGenerationError(RuntimeError):
    """The video workflow could not produce a validated artifact."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VideoGenerationError(f"request failed: {url}") from exc
    if not payload:
        return {}
    result = json.loads(payload.decode("utf-8"))
    if not isinstance(result, dict):
        raise VideoGenerationError("service response was not a JSON object")
    return result


def post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    return request_json(
        url,
        method="POST",
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )


def available_model_names(object_info: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str) and value.endswith(".safetensors"):
            names.add(value)

    walk(object_info)
    return names


def preflight(object_info: dict[str, Any]) -> dict[str, Any]:
    available = available_model_names(object_info)
    missing_models = [
        asset["filename"] for asset in MODEL_ASSETS if asset["filename"] not in available
    ]
    missing_nodes = sorted(REQUIRED_NODES - set(object_info))
    save_video_options = (
        object_info.get("SaveVideo", {})
        .get("input", {})
        .get("required", {})
    )
    has_mp4 = "mp4" in str(save_video_options.get("format", ""))
    has_h264 = "h264" in str(save_video_options.get("codec", ""))
    return {
        "passed": not missing_models and not missing_nodes and has_mp4 and has_h264,
        "missing_models": missing_models,
        "missing_nodes": missing_nodes,
        "mp4_supported": has_mp4,
        "h264_supported": has_h264,
    }


def build_workflow(
    *,
    input_image: str,
    prompt: str,
    seed: int,
    filename_prefix: str,
    width: int = 848,
    height: int = 480,
    length: int = 81,
    steps: int = 8,
    fps: float = 24.0,
) -> dict[str, Any]:
    relative_input = PurePosixPath(input_image)
    if (
        not input_image
        or relative_input.is_absolute()
        or ".." in relative_input.parts
        or "\\" in input_image
    ):
        raise ValueError("input_image must be a safe ComfyUI input path")
    if width % 16 or height % 16 or min(width, height) < 16:
        raise ValueError("video dimensions must be positive multiples of 16")
    if length < 1 or (length - 1) % 4:
        raise ValueError("video length must be 1 plus a multiple of 4")
    if steps not in {8, 12}:
        raise ValueError("the Step-Distilled workflow accepts 8 or 12 steps")
    if not prompt.strip():
        raise ValueError("motion prompt cannot be empty")
    workflow = copy.deepcopy(read_json(WORKFLOW_FILE))
    workflow["5"]["inputs"]["image"] = input_image
    workflow["7"]["inputs"]["text"] = prompt.strip()
    workflow["9"]["inputs"].update(
        {"width": int(width), "height": int(height), "length": int(length)}
    )
    workflow["12"]["inputs"]["noise_seed"] = int(seed)
    workflow["14"]["inputs"]["steps"] = int(steps)
    workflow["17"]["inputs"]["fps"] = float(fps)
    workflow["18"]["inputs"]["filename_prefix"] = filename_prefix
    return workflow


def upload_image(path: Path, comfy_url: str) -> str:
    clean_name = "".join(
        character if character.isalnum() or character in {".", "-", "_"} else "_"
        for character in path.name
    )
    safe_name = f"evolab_{sha256(path)[:12]}_{clean_name}"
    boundary = f"----EvoLab{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def text_field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )

    text_field("type", "input")
    text_field("overwrite", "true")
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'
            ).encode(),
            b"Content-Type: image/png\r\n\r\n",
            path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    response = request_json(
        f"{comfy_url.rstrip('/')}/upload/image",
        method="POST",
        body=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=120,
    )
    name = response.get("name")
    subfolder = response.get("subfolder") or ""
    if not isinstance(name, str) or not name:
        raise VideoGenerationError("ComfyUI did not return the uploaded image name")
    if Path(name).name != name or ".." in Path(subfolder).parts:
        raise VideoGenerationError("ComfyUI returned an unsafe input path")
    return f"{subfolder}/{name}" if subfolder else name


def submit_prompt(workflow: dict[str, Any], comfy_url: str) -> str:
    result = post_json(f"{comfy_url.rstrip('/')}/api/prompt", {"prompt": workflow}, 60)
    if result.get("error"):
        raise VideoGenerationError("ComfyUI rejected the HunyuanVideo workflow")
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id:
        raise VideoGenerationError("ComfyUI did not return a prompt id")
    return prompt_id


def _read_kib(path: Path, key: str) -> int | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(key + ":"):
            return int(line.split()[1]) * 1024
    return None


def find_comfy_pid(port: int = 7000) -> int | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    for candidate in proc.iterdir():
        if not candidate.name.isdigit():
            continue
        try:
            command = (candidate / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, UnicodeDecodeError):
            continue
        if "ComfyUI/main.py" in command and f"--port {port}" in command:
            return int(candidate.name)
    return None


@dataclass
class MemoryTracker:
    process_id: int | None
    peak_process_rss_bytes: int = 0
    process_hwm_bytes: int = 0
    peak_system_used_bytes: int = 0
    samples: int = 0
    warnings: list[str] = field(default_factory=list)

    def sample(self) -> None:
        self.samples += 1
        if self.process_id:
            status = Path("/proc") / str(self.process_id) / "status"
            rss = _read_kib(status, "VmRSS")
            hwm = _read_kib(status, "VmHWM")
            if rss is not None:
                self.peak_process_rss_bytes = max(self.peak_process_rss_bytes, rss)
            if hwm is not None:
                self.process_hwm_bytes = max(self.process_hwm_bytes, hwm)
        meminfo = Path("/proc/meminfo")
        total = _read_kib(meminfo, "MemTotal")
        available = _read_kib(meminfo, "MemAvailable")
        if total is not None and available is not None:
            self.peak_system_used_bytes = max(
                self.peak_system_used_bytes, total - available
            )

    def evidence(self) -> dict[str, Any]:
        return {
            "comfyui_pid": self.process_id,
            "peak_process_rss_bytes": self.peak_process_rss_bytes or None,
            "process_hwm_bytes": self.process_hwm_bytes or None,
            "peak_system_used_bytes": self.peak_system_used_bytes or None,
            "samples": self.samples,
            "warnings": self.warnings,
        }


def wait_for_prompt(
    prompt_id: str,
    comfy_url: str,
    timeout: int,
    tracker: MemoryTracker,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tracker.sample()
        history = request_json(
            f"{comfy_url.rstrip('/')}/api/history/{prompt_id}", timeout=30
        )
        entry = history.get(prompt_id)
        if isinstance(entry, dict):
            status = entry.get("status", {})
            for message in status.get("messages", []) or []:
                if isinstance(message, list) and message and message[0] == "execution_error":
                    detail = message[1] if len(message) > 1 and isinstance(message[1], dict) else {}
                    node = detail.get("node_type") or detail.get("node_id") or "unknown node"
                    reason = detail.get("exception_message") or "execution error"
                    raise VideoGenerationError(f"ComfyUI {node}: {reason}")
            if status.get("completed") or status.get("status_str") == "success":
                tracker.sample()
                return entry.get("outputs", {})
        time.sleep(poll_interval)
    raise VideoGenerationError(f"ComfyUI video generation timed out after {timeout} seconds")


def _walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def locate_video(outputs: dict[str, Any], comfy_output: Path) -> Path:
    root = comfy_output.expanduser().resolve()
    for item in _walk_dicts(outputs):
        filename = item.get("filename")
        if not isinstance(filename, str) or Path(filename).suffix.lower() not in {".mp4", ".webm"}:
            continue
        subfolder = item.get("subfolder") or ""
        candidate = (root / str(subfolder) / filename).resolve()
        if not candidate.is_relative_to(root):
            raise VideoGenerationError("ComfyUI returned an unsafe video path")
        if candidate.is_file():
            return candidate
    raise VideoGenerationError("ComfyUI history contained no playable video")


def input_image_metrics(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise VideoGenerationError("Pillow is required for image validation") from exc
    with Image.open(path) as image:
        image.load()
        return {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }


def _center_crop(image: Any, width: int, height: int) -> Any:
    target_ratio = width / height
    source_ratio = image.width / image.height
    if source_ratio > target_ratio:
        crop_width = round(image.height * target_ratio)
        left = (image.width - crop_width) // 2
        image = image.crop((left, 0, left + crop_width, image.height))
    else:
        crop_height = round(image.width / target_ratio)
        top = (image.height - crop_height) // 2
        image = image.crop((0, top, image.width, top + crop_height))
    return image.resize((width, height))


def _correlation(left: Any, right: Any) -> float:
    import numpy as np

    left_flat = left.astype("float32").reshape(-1)
    right_flat = right.astype("float32").reshape(-1)
    if float(left_flat.std()) < 1e-6 or float(right_flat.std()) < 1e-6:
        return 0.0
    return float(np.corrcoef(left_flat, right_flat)[0, 1])


def validate_video(
    video: Path,
    input_image: Path,
    first_frame_path: Path,
    last_frame_path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_length: int,
    expected_fps: float,
) -> dict[str, Any]:
    try:
        import av
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise VideoGenerationError("PyAV, NumPy and Pillow are required for video validation") from exc

    grayscale_frames: list[Any] = []
    first_rgb = None
    last_rgb = None
    with av.open(str(video)) as container:
        if not container.streams.video:
            raise VideoGenerationError("MP4 has no video stream")
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        width = int(stream.codec_context.width)
        height = int(stream.codec_context.height)
        rate = float(stream.average_rate) if stream.average_rate else 0.0
        for frame in container.decode(stream):
            image = frame.to_image().convert("RGB")
            if first_rgb is None:
                first_rgb = image.copy()
            last_rgb = image.copy()
            grayscale_frames.append(
                np.asarray(image.resize((212, 120)).convert("L"), dtype="float32")
            )

    if first_rgb is None or last_rgb is None:
        raise VideoGenerationError("MP4 contains no decodable frames")
    first_frame_path.parent.mkdir(parents=True, exist_ok=True)
    first_rgb.save(first_frame_path)
    last_rgb.save(last_frame_path)
    with Image.open(input_image) as source:
        source_gray = np.asarray(
            _center_crop(source.convert("RGB"), 212, 120).convert("L"), dtype="float32"
        )

    first = grayscale_frames[0]
    last = grayscale_frames[-1]
    consecutive = [
        float(np.mean(np.abs(current - previous)))
        for previous, current in zip(grayscale_frames, grayscale_frames[1:])
    ]
    frame_count = len(grayscale_frames)
    duration = frame_count / rate if rate else 0.0
    first_input_mae = float(np.mean(np.abs(first - source_gray)))
    first_input_correlation = _correlation(first, source_gray)
    first_last_mae = float(np.mean(np.abs(last - first)))
    first_last_correlation = _correlation(first, last)
    average_change = sum(consecutive) / len(consecutive) if consecutive else 0.0
    max_change = max(consecutive) if consecutive else 0.0

    technical_pass = bool(
        video.suffix.lower() == ".mp4"
        and codec == "h264"
        and width == expected_width
        and height == expected_height
        and frame_count >= expected_length - 2
        and abs(rate - expected_fps) <= 0.1
        and 2.5 <= duration <= 5.0
        and video.stat().st_size >= 100_000
    )
    first_frame_pass = bool(first_input_correlation >= 0.2 and first_input_mae <= 70.0)
    motion_pass = bool(first_last_mae >= 0.8 and average_change >= 0.08)
    continuity_pass = bool(first_last_correlation >= 0.1 and max_change <= 55.0)
    return {
        "container": "mp4",
        "codec": codec,
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "fps": round(rate, 4),
        "duration_seconds": round(duration, 4),
        "bytes": video.stat().st_size,
        "sha256": sha256(video),
        "first_frame_sha256": sha256(first_frame_path),
        "last_frame_sha256": sha256(last_frame_path),
        "first_input_mae": round(first_input_mae, 4),
        "first_input_correlation": round(first_input_correlation, 4),
        "first_last_mae": round(first_last_mae, 4),
        "first_last_correlation": round(first_last_correlation, 4),
        "average_consecutive_mae": round(average_change, 4),
        "max_consecutive_mae": round(max_change, 4),
        "technical_pass": technical_pass,
        "first_frame_pass": first_frame_pass,
        "motion_pass": motion_pass,
        "continuity_pass": continuity_pass,
        "passed": technical_pass and first_frame_pass and motion_pass and continuity_pass,
    }


def unload_other_models(comfy_url: str) -> dict[str, Any]:
    evidence = {"ollama_unloaded": False, "comfy_models_released": False}
    ollama_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        post_json(
            f"{ollama_url}/api/generate",
            {"model": os.environ.get("OLLAMA_MODEL", "qwen3.6:35b"), "keep_alive": 0},
            30,
        )
        evidence["ollama_unloaded"] = True
    except Exception:
        evidence["ollama_unloaded"] = False
    try:
        post_json(
            f"{comfy_url.rstrip('/')}/free",
            {"unload_models": True, "free_memory": True},
            30,
        )
        evidence["comfy_models_released"] = True
    except Exception:
        evidence["comfy_models_released"] = False
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-image", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--seed", type=int, default=20_260_722)
    parser.add_argument("--width", type=int, default=848)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--length", type=int, default=81)
    parser.add_argument("--fps", type=float, default=24.0)
    parser.add_argument("--steps", type=int, choices=(8, 12), default=8)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--comfy-url", default=os.environ.get("COMFYUI_URL", "http://127.0.0.1:7000")
    )
    parser.add_argument(
        "--comfy-output",
        type=Path,
        default=Path(os.environ.get("COMFY_OUTPUT_DIR", str(DEFAULT_COMFY_OUTPUT))),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    evidence_path = output_root / "video_validation.json"
    evidence: dict[str, Any] = {
        "contract_version": "1.0.0",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "HunyuanVideo-1.5 480p I2V Step Distilled FP8 via ComfyUI",
        "model_assets": list(MODEL_ASSETS),
        "motion_prompt": args.prompt,
        "parameters": {
            "seed": args.seed,
            "width": args.width,
            "height": args.height,
            "length": args.length,
            "fps": args.fps,
            "steps": args.steps,
            "cfg": 1.0,
            "flow_shift": 7.0,
        },
        "passed": False,
    }
    try:
        input_path = args.input_image.expanduser().resolve()
        if not input_path.is_file():
            raise VideoGenerationError("input stage image does not exist")
        retained_input = output_root / "input_stage_03.png"
        shutil.copy2(input_path, retained_input)
        evidence["input"] = {
            "source": str(input_path),
            "retained": str(retained_input),
            **input_image_metrics(retained_input),
        }

        object_info = request_json(f"{args.comfy_url.rstrip('/')}/object_info", timeout=60)
        evidence["preflight"] = preflight(object_info)
        if not evidence["preflight"]["passed"]:
            raise VideoGenerationError("HunyuanVideo preflight did not pass")

        evidence["memory_release"] = unload_other_models(args.comfy_url)
        uploaded_name = upload_image(retained_input, args.comfy_url)
        evidence["uploaded_input"] = uploaded_name
        prefix = f"evolab_video/hunyuan_stage_03_{int(time.time())}"
        workflow = build_workflow(
            input_image=uploaded_name,
            prompt=args.prompt,
            seed=args.seed,
            filename_prefix=prefix,
            width=args.width,
            height=args.height,
            length=args.length,
            steps=args.steps,
            fps=args.fps,
        )
        evidence["workflow_sha256"] = hashlib.sha256(
            json.dumps(workflow, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        tracker = MemoryTracker(find_comfy_pid())
        tracker.sample()
        generation_started = time.monotonic()
        prompt_id = submit_prompt(workflow, args.comfy_url)
        evidence["prompt_id"] = prompt_id
        outputs = wait_for_prompt(
            prompt_id,
            args.comfy_url,
            args.timeout,
            tracker,
            args.poll_interval,
        )
        evidence["generation_seconds"] = round(time.monotonic() - generation_started, 3)
        evidence["memory"] = tracker.evidence()
        source_video = locate_video(outputs, args.comfy_output)
        destination = output_root / "hunyuan_stage_03_silent.mp4"
        shutil.copy2(source_video, destination)
        first_frame = output_root / "first_frame.png"
        last_frame = output_root / "last_frame.png"
        evidence["output"] = {
            "source": str(source_video),
            "retained": str(destination),
            "first_frame": str(first_frame),
            "last_frame": str(last_frame),
            **validate_video(
                destination,
                retained_input,
                first_frame,
                last_frame,
                expected_width=args.width,
                expected_height=args.height,
                expected_length=args.length,
                expected_fps=args.fps,
            ),
        }
        evidence["passed"] = evidence["output"]["passed"]
    except Exception as exc:
        evidence["error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:1000],
        }
    evidence["total_seconds"] = round(time.monotonic() - started, 3)
    evidence["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(evidence_path, evidence)
    print(f"VIDEO_VALIDATION:{evidence_path}")
    return 0 if evidence["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
