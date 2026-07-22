#!/usr/bin/env python3
"""Generate a validated first/last-frame video for each lineage transition."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_FILE = SCRIPT_DIR / "wan_flf_workflow.json"
DEFAULT_COMFY_OUTPUT = Path(
    "/home/Developer/build_a_claw_workshop-bundle/comfyui-app/ComfyUI/output"
)
DEFAULT_NEGATIVE = (
    "突然换成无关物种，身份跳变，额外肢体，肢体消失，重复动物，多个主体，"
    "解剖畸形，静态画面，镜头切换，字幕，文字，水印，低质量，模糊"
)
MODEL_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "directory": "diffusion_models",
        "filename": "wan2.1_flf2v_720p_14B_fp8_e4m3fn.safetensors",
        "bytes": 16_397_952_536,
        "sha256": "d68ca694a695274e48e00974128337e06e497d95a1dc09e86fd2a01a405f455f",
        "source": (
            "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/"
            "main/split_files/diffusion_models/"
            "wan2.1_flf2v_720p_14B_fp8_e4m3fn.safetensors"
        ),
    },
    {
        "directory": "text_encoders",
        "filename": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "bytes": 6_735_906_897,
        "sha256": "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
        "source": (
            "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/"
            "main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
        ),
    },
    {
        "directory": "vae",
        "filename": "wan_2.1_vae.safetensors",
        "bytes": 253_815_318,
        "sha256": "2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b",
        "source": (
            "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/"
            "main/split_files/vae/wan_2.1_vae.safetensors"
        ),
    },
    {
        "directory": "clip_vision",
        "filename": "clip_vision_h.safetensors",
        "bytes": 1_264_219_396,
        "sha256": "64a7ef761bfccbadbaa3da77366aac4185a6c58fa5de5f589b42a65bcc21f161",
        "source": (
            "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/"
            "main/split_files/clip_vision/clip_vision_h.safetensors"
        ),
    },
)
REQUIRED_NODES = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "CLIPVisionLoader",
    "LoadImage",
    "CLIPVisionEncode",
    "CLIPTextEncode",
    "WanFirstLastFrameToVideo",
    "ModelSamplingSD3",
    "KSampler",
    "VAEDecode",
    "CreateVideo",
    "SaveVideo",
}


def _load_shared() -> Any:
    name = "evolab_lineage_flf_shared_video"
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / "video_generation.py")
    if not spec or not spec.loader:
        raise RuntimeError("cannot load shared video helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
VideoGenerationError = shared.VideoGenerationError


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_comfy_path(value: str) -> str:
    candidate = PurePosixPath(value)
    if not value or candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ValueError("image must be a safe ComfyUI input path")
    return value


def build_workflow(
    *,
    start_image: str,
    end_image: str,
    prompt: str,
    seed: int,
    filename_prefix: str,
    width: int = 1280,
    height: int = 720,
    length: int = 33,
    steps: int = 20,
    fps: float = 16.0,
) -> dict[str, Any]:
    _safe_comfy_path(start_image)
    _safe_comfy_path(end_image)
    if width % 16 or height % 16 or min(width, height) < 16:
        raise ValueError("dimensions must be positive multiples of 16")
    if length < 5 or (length - 1) % 4:
        raise ValueError("length must be 1 plus a multiple of 4")
    if steps < 10 or steps > 50:
        raise ValueError("steps must stay between 10 and 50")
    if not prompt.strip():
        raise ValueError("transition prompt cannot be empty")
    workflow = copy.deepcopy(_read_json(WORKFLOW_FILE))
    workflow["5"]["inputs"]["image"] = start_image
    workflow["6"]["inputs"]["image"] = end_image
    workflow["9"]["inputs"]["text"] = prompt.strip()
    workflow["10"]["inputs"]["text"] = DEFAULT_NEGATIVE
    workflow["11"]["inputs"].update(
        {"width": int(width), "height": int(height), "length": int(length)}
    )
    workflow["13"]["inputs"].update({"seed": int(seed), "steps": int(steps)})
    workflow["15"]["inputs"]["fps"] = float(fps)
    workflow["16"]["inputs"]["filename_prefix"] = filename_prefix
    return workflow


def preflight(object_info: dict[str, Any]) -> dict[str, Any]:
    available = shared.available_model_names(object_info)
    missing_models = [
        item["filename"] for item in MODEL_ASSETS if item["filename"] not in available
    ]
    missing_nodes = sorted(REQUIRED_NODES - set(object_info))
    return {
        "passed": not missing_models and not missing_nodes,
        "missing_models": missing_models,
        "missing_nodes": missing_nodes,
    }


def _stage_file(session_dir: Path, stage: dict[str, Any]) -> Path:
    filename = Path(str(stage.get("image_url", ""))).name
    path = (session_dir / filename).resolve()
    if not path.is_relative_to(session_dir.resolve()) or not path.is_file():
        raise VideoGenerationError(f"missing lineage stage image: {filename}")
    if path.suffix.lower() != ".png":
        raise VideoGenerationError("first/last-frame generation requires raster PNG stages")
    return path


def plan_segments(session: dict[str, Any], session_dir: Path) -> list[dict[str, Any]]:
    history = session.get("history")
    if session.get("status") != "completed" or not isinstance(history, list) or len(history) < 4:
        raise VideoGenerationError("session does not contain four completed lineage stages")
    segments: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(zip(history[:3], history[1:4]), start=1):
        selection = end.get("selection") if isinstance(end.get("selection"), dict) else {}
        direction = selection.get("direction") if isinstance(selection.get("direction"), dict) else {}
        title = str(direction.get("title") or end.get("organism_name") or f"第 {index} 次改变")
        description = str(direction.get("description") or end.get("change_summary") or "")
        prompt = (
            f"同一条谱系从首帧逐步变化到尾帧：{title}。{description}"
            "保持头、躯干、尾部和成对附肢的连续同源关系；变化应连续发生在同一主体上，"
            "不要切镜头，不要突然换成另一物种。自然历史纪录片镜头，主体完整可见。"
        )
        segments.append(
            {
                "index": index,
                "start_round": index - 1,
                "end_round": index,
                "start": _stage_file(session_dir, start),
                "end": _stage_file(session_dir, end),
                "prompt": prompt,
            }
        )
    return segments


def _prepare_frame(source: Path, destination: Path, width: int, height: int) -> None:
    from PIL import Image, ImageOps

    with Image.open(source) as image:
        fitted = ImageOps.fit(
            image.convert("RGB"),
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        fitted.save(destination)


def _gray(image: Any) -> Any:
    import numpy as np

    return np.asarray(image.resize((320, 180)).convert("L"), dtype="float32")


def validate_segment(
    video: Path,
    start_image: Path,
    end_image: Path,
    first_frame_path: Path,
    last_frame_path: Path,
    *,
    width: int,
    height: int,
    length: int,
    fps: float,
) -> dict[str, Any]:
    import av
    import numpy as np
    from PIL import Image

    frames: list[Any] = []
    first_rgb = None
    last_rgb = None
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        actual_width = int(stream.codec_context.width)
        actual_height = int(stream.codec_context.height)
        actual_fps = float(stream.average_rate) if stream.average_rate else 0.0
        for frame in container.decode(stream):
            image = frame.to_image().convert("RGB")
            first_rgb = image.copy() if first_rgb is None else first_rgb
            last_rgb = image.copy()
            frames.append(_gray(image))
    if first_rgb is None or last_rgb is None:
        raise VideoGenerationError("segment has no decodable frames")
    first_rgb.save(first_frame_path)
    last_rgb.save(last_frame_path)
    with Image.open(start_image) as image:
        expected_start = _gray(image.convert("RGB"))
    with Image.open(end_image) as image:
        expected_end = _gray(image.convert("RGB"))
    first_mae = float(np.mean(np.abs(frames[0] - expected_start)))
    last_mae = float(np.mean(np.abs(frames[-1] - expected_end)))
    first_corr = shared._correlation(frames[0], expected_start)
    last_corr = shared._correlation(frames[-1], expected_end)
    consecutive = [
        float(np.mean(np.abs(current - previous)))
        for previous, current in zip(frames, frames[1:])
    ]
    technical_pass = bool(
        codec == "h264"
        and actual_width == width
        and actual_height == height
        and len(frames) >= length - 2
        and abs(actual_fps - fps) <= 0.1
        and video.stat().st_size >= 100_000
    )
    endpoint_pass = bool(
        first_corr >= 0.35
        and last_corr >= 0.35
        and first_mae <= 55.0
        and last_mae <= 55.0
    )
    motion_pass = bool(consecutive and sum(consecutive) / len(consecutive) >= 0.08)
    continuity_pass = bool(consecutive and max(consecutive) <= 55.0)
    return {
        "codec": codec,
        "width": actual_width,
        "height": actual_height,
        "fps": round(actual_fps, 4),
        "frame_count": len(frames),
        "duration_seconds": round(len(frames) / actual_fps, 4),
        "bytes": video.stat().st_size,
        "sha256": shared.sha256(video),
        "first_frame_sha256": shared.sha256(first_frame_path),
        "last_frame_sha256": shared.sha256(last_frame_path),
        "first_endpoint_mae": round(first_mae, 4),
        "last_endpoint_mae": round(last_mae, 4),
        "first_endpoint_correlation": round(first_corr, 4),
        "last_endpoint_correlation": round(last_corr, 4),
        "average_consecutive_mae": round(sum(consecutive) / len(consecutive), 4),
        "max_consecutive_mae": round(max(consecutive), 4),
        "technical_pass": technical_pass,
        "endpoint_pass": endpoint_pass,
        "motion_pass": motion_pass,
        "continuity_pass": continuity_pass,
        "passed": technical_pass and endpoint_pass and motion_pass and continuity_pass,
    }


def _merge_segments_pyav(segments: list[Path], output: Path, fps: float) -> None:
    import av

    output.parent.mkdir(parents=True, exist_ok=True)
    rate = Fraction(str(fps))
    with av.open(str(segments[0])) as first_container:
        first_stream = first_container.streams.video[0]
        width = int(first_stream.codec_context.width)
        height = int(first_stream.codec_context.height)
    with av.open(str(output), mode="w", format="mp4", options={"movflags": "+faststart"}) as target:
        output_stream = target.add_stream("libx264", rate=rate)
        output_stream.width = width
        output_stream.height = height
        output_stream.pix_fmt = "yuv420p"
        output_stream.options = {"crf": "19"}
        frame_number = 0
        for segment_index, segment in enumerate(segments):
            with av.open(str(segment)) as source:
                for source_index, frame in enumerate(source.decode(source.streams.video[0])):
                    if segment_index and source_index == 0:
                        continue
                    encoded = frame.reformat(width=width, height=height, format="yuv420p")
                    encoded.pts = frame_number
                    encoded.time_base = Fraction(1, 1) / rate
                    frame_number += 1
                    for packet in output_stream.encode(encoded):
                        target.mux(packet)
        for packet in output_stream.encode():
            target.mux(packet)


def merge_segments(segments: list[Path], output: Path, fps: float) -> dict[str, Any]:
    if len(segments) < 2:
        raise VideoGenerationError("at least two segments are required")
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, segment in enumerate(segments):
        inputs.extend(["-i", str(segment)])
        chain = (
            "setpts=PTS-STARTPTS"
            if index == 0
            else "trim=start_frame=1,setpts=PTS-STARTPTS"
        )
        filters.append(f"[{index}:v]{chain}[v{index}]")
        labels.append(f"[v{index}]")
    filters.append(
        "".join(labels)
        + f"concat=n={len(segments)}:v=1:a=0,fps={fps},format=yuv420p[outv]"
    )
    command = [
        "ffmpeg",
        "-v",
        "error",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outv]",
        "-an",
        "-c:v",
        "libx264",
        "-crf",
        "19",
        "-movflags",
        "+faststart",
        "-y",
        str(output),
    ]
    if shutil.which("ffmpeg"):
        subprocess.run(command, check=True, timeout=300)
    else:
        _merge_segments_pyav(segments, output, fps)
    return {"bytes": output.stat().st_size, "sha256": shared.sha256(output)}


def validate_merged_video(
    video: Path,
    segment_validations: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    fps: float,
) -> dict[str, Any]:
    import av
    import numpy as np

    frames: list[Any] = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        actual_width = int(stream.codec_context.width)
        actual_height = int(stream.codec_context.height)
        actual_fps = float(stream.average_rate) if stream.average_rate else 0.0
        for frame in container.decode(stream):
            frames.append(_gray(frame.to_image().convert("RGB")))
    expected_frames = sum(int(item["frame_count"]) for item in segment_validations)
    expected_frames -= len(segment_validations) - 1
    seam_indices: list[int] = []
    position = int(segment_validations[0]["frame_count"])
    for item in segment_validations[1:]:
        seam_indices.append(position)
        position += int(item["frame_count"]) - 1
    seam_mae = [
        float(np.mean(np.abs(frames[index] - frames[index - 1])))
        for index in seam_indices
        if 0 < index < len(frames)
    ]
    technical_pass = bool(
        codec == "h264"
        and actual_width == width
        and actual_height == height
        and abs(actual_fps - fps) <= 0.1
        and abs(len(frames) - expected_frames) <= 1
        and video.stat().st_size >= 100_000
    )
    seam_pass = bool(len(seam_mae) == len(seam_indices) and max(seam_mae) <= 55.0)
    return {
        "codec": codec,
        "width": actual_width,
        "height": actual_height,
        "fps": round(actual_fps, 4),
        "frame_count": len(frames),
        "expected_frame_count": expected_frames,
        "duration_seconds": round(len(frames) / actual_fps, 4),
        "bytes": video.stat().st_size,
        "sha256": shared.sha256(video),
        "seam_frame_indices": seam_indices,
        "seam_mae": [round(value, 4) for value in seam_mae],
        "technical_pass": technical_pass,
        "seam_pass": seam_pass,
        "passed": technical_pass and seam_pass,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--length", type=int, default=33)
    parser.add_argument("--fps", type=float, default=16.0)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20_260_722)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument(
        "--comfy-url", default="http://127.0.0.1:7000"
    )
    parser.add_argument(
        "--comfy-output", type=Path, default=DEFAULT_COMFY_OUTPUT
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    session_path = args.session.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    evidence_path = output_root / "lineage_flf_validation.json"
    evidence: dict[str, Any] = {
        "contract_version": "1.0.0",
        "generator": "Wan2.1 FLF2V 14B 720p FP8 via ComfyUI",
        "model_assets": list(MODEL_ASSETS),
        "parameters": {
            "width": args.width,
            "height": args.height,
            "length": args.length,
            "fps": args.fps,
            "steps": args.steps,
            "seed": args.seed,
        },
        "passed": False,
        "segments": [],
    }
    try:
        session = _read_json(session_path)
        plan = plan_segments(session, session_path.parent)
        evidence["session"] = {
            "session_id": session.get("session_id"),
            "updated_at": session.get("updated_at"),
            "source": str(session_path),
            "stage_sha256": [
                {"round": item["start_round"], "sha256": shared.sha256(item["start"])}
                for item in plan[:1]
            ]
            + [
                {"round": item["end_round"], "sha256": shared.sha256(item["end"])}
                for item in plan
            ],
        }
        object_info = shared.request_json(f"{args.comfy_url.rstrip('/')}/object_info", timeout=60)
        evidence["preflight"] = preflight(object_info)
        if not evidence["preflight"]["passed"]:
            raise VideoGenerationError("Wan FLF preflight did not pass")
        evidence["memory_release"] = shared.unload_other_models(args.comfy_url)
        tracker = shared.MemoryTracker(shared.find_comfy_pid())
        generated: list[Path] = []
        for item in plan:
            index = int(item["index"])
            segment_dir = output_root / f"segment_{index:02d}"
            start = segment_dir / "start.png"
            end = segment_dir / "end.png"
            _prepare_frame(item["start"], start, args.width, args.height)
            _prepare_frame(item["end"], end, args.width, args.height)
            start_upload = shared.upload_image(start, args.comfy_url)
            end_upload = shared.upload_image(end, args.comfy_url)
            prefix = f"evolab_video/wan_flf_{session.get('session_id', 'lineage')}_{index:02d}"
            workflow = build_workflow(
                start_image=start_upload,
                end_image=end_upload,
                prompt=item["prompt"],
                seed=args.seed + index,
                filename_prefix=prefix,
                width=args.width,
                height=args.height,
                length=args.length,
                steps=args.steps,
                fps=args.fps,
            )
            generation_started = time.monotonic()
            prompt_id = shared.submit_prompt(workflow, args.comfy_url)
            outputs = shared.wait_for_prompt(
                prompt_id, args.comfy_url, args.timeout, tracker, 2.0
            )
            source = shared.locate_video(outputs, args.comfy_output)
            destination = segment_dir / "segment.mp4"
            shutil.copy2(source, destination)
            first = segment_dir / "first_frame.png"
            last = segment_dir / "last_frame.png"
            validation = validate_segment(
                destination,
                start,
                end,
                first,
                last,
                width=args.width,
                height=args.height,
                length=args.length,
                fps=args.fps,
            )
            record = {
                "index": index,
                "start_round": item["start_round"],
                "end_round": item["end_round"],
                "prompt": item["prompt"],
                "prompt_id": prompt_id,
                "generation_seconds": round(time.monotonic() - generation_started, 3),
                "validation": validation,
            }
            evidence["segments"].append(record)
            shared.write_json(evidence_path, evidence)
            if not validation["passed"]:
                raise VideoGenerationError(f"segment {index} did not pass validation")
            generated.append(destination)
        merged = output_root / "lineage_flf_complete.mp4"
        merge_segments(generated, merged, args.fps)
        evidence["merged"] = validate_merged_video(
            merged,
            [record["validation"] for record in evidence["segments"]],
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
        if not evidence["merged"]["passed"]:
            raise VideoGenerationError("merged video did not pass technical or seam validation")
        evidence["memory"] = tracker.evidence()
        evidence["passed"] = True
    except Exception as exc:
        evidence["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    evidence["total_seconds"] = round(time.monotonic() - started, 3)
    evidence["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    shared.write_json(evidence_path, evidence)
    print(f"LINEAGE_FLF_VALIDATION:{evidence_path}")
    return 0 if evidence["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
