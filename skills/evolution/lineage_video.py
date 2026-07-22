#!/usr/bin/env python3
"""Build a session-specific lineage recap from several saved stage images.

The recap is deterministic presentation, not another scientific inference. It
uses the images and Chinese choices already persisted in one completed session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse


WIDTH = 1280
HEIGHT = 720
FPS = 24
HOLD_SECONDS = 1.45
TRANSITION_SECONDS = 0.55
BACKGROUND = (6, 23, 29)
FOSSIL = (233, 226, 208)
MUTED = (185, 192, 184)
MEMBRANE = (105, 211, 190)
IRON = (216, 102, 63)
SULFUR = (231, 194, 90)
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
CONTRACT_VERSION = "1.1.0"

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
)


class LineageVideoError(RuntimeError):
    """A completed session could not be turned into a safe recap video."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def has_chinese(value: Any) -> bool:
    return isinstance(value, str) and bool(CHINESE_PATTERN.search(value))


def chinese_text(value: Any, fallback: str) -> str:
    if has_chinese(value):
        return " ".join(str(value).split())
    return fallback


def _selection_text(selection: dict[str, Any], key: str, field: str) -> str:
    item = selection.get(key) or {}
    value = item.get(field) if isinstance(item, dict) else ""
    return chinese_text(value, "")


def _safe_stage_file(session_dir: Path, image_url: str) -> Path:
    filename = PurePosixPath(urlparse(image_url).path).name
    if not filename or filename != Path(filename).name:
        raise LineageVideoError("stage image URL is unsafe")
    candidate = (session_dir / filename).resolve()
    if not candidate.is_relative_to(session_dir.resolve()) or not candidate.is_file():
        raise LineageVideoError(f"stage image is missing: {filename}")
    if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
        raise LineageVideoError(f"unsupported stage image: {filename}")
    return candidate


def stage_caption(stage: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    round_no = int(stage.get("round", 0))
    selection = stage.get("selection") if isinstance(stage.get("selection"), dict) else {}
    direction = _selection_text(selection, "direction", "title")
    direction_detail = _selection_text(selection, "direction", "description")
    environment = _selection_text(selection, "environment", "title")
    contingency = _selection_text(selection, "contingency", "title")
    chemistry = stage.get("scenario_id") == "hydrothermal_origin"
    if round_no == 0:
        title = chinese_text(
            stage.get("organism_name"),
            chinese_text(scenario.get("title"), "这条路线的起点"),
        )
        return {
            "round": 0,
            "chapter": "起点",
            "title": title,
            "environment": chinese_text(scenario.get("habitat"), "起始环境"),
            "contingency": "尚未发生偶然事件",
            "choice": "还没有作出选择",
            "change": chinese_text(
                stage.get("change_summary"),
                "路线从这里开始，后面的变化都要保留可辨认的来路。",
            ),
        }
    default_title = ("第 %d 次化学变化" if chemistry else "第 %d 次谱系变化") % round_no
    return {
        "round": round_no,
        "chapter": f"第 {round_no} 次改变",
        "title": chinese_text(stage.get("organism_name"), default_title),
        "environment": environment or "环境记录缺失",
        "contingency": contingency or "偶发事件记录缺失",
        "choice": direction or "选择记录缺失",
        "change": direction_detail
        or chinese_text(stage.get("change_summary"), "这一阶段保留了上一阶段的来路，也承担了新的代价。"),
    }


def build_recap_plan(session: dict[str, Any], session_dir: Path) -> dict[str, Any]:
    if session.get("status") != "completed":
        raise LineageVideoError("session has not completed three rounds")
    history = session.get("history")
    if not isinstance(history, list) or len(history) < 4:
        raise LineageVideoError("session does not contain enough stages")
    scenario = session.get("scenario") if isinstance(session.get("scenario"), dict) else {}
    stages: list[dict[str, Any]] = []
    for stage in history[:4]:
        if not isinstance(stage, dict):
            continue
        image = _safe_stage_file(session_dir, str(stage.get("image_url", "")))
        stages.append(
            {
                **stage_caption(stage, scenario),
                "image": str(image),
                "image_name": image.name,
                "image_sha256": sha256(image),
                "source_kind": (
                    "raster"
                    if image.suffix.lower() != ".svg"
                    else "vector_rasterized"
                    if shutil.which("gdk-pixbuf-thumbnailer")
                    else "vector_placeholder"
                ),
            }
        )
    if len(stages) < 4:
        raise LineageVideoError("fewer than four usable stage images remain")
    return {
        "contract_version": CONTRACT_VERSION,
        "session_id": str(session.get("session_id", "")),
        "scenario_id": str(session.get("scenario_id", "")),
        "session_updated_at": str(session.get("updated_at", "")),
        "input_stage_count": len(stages),
        "stages": stages,
        "presentation_boundary": "阶段图来自本次会话；转场和字幕用于回看选择，不是新的科学证据。",
    }


def _font(size: int, *, bold: bool = False) -> Any:
    from PIL import ImageFont

    preferred = os.environ.get("EVOLAB_CJK_FONT", "")
    candidates = ([preferred] if preferred else []) + list(FONT_CANDIDATES)
    if bold:
        candidates = sorted(candidates, key=lambda item: "Bold" not in item)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    raise LineageVideoError("no Chinese font is available for recap captions")


def _wrap(draw: Any, text: str, font: Any, width: int, max_lines: int) -> list[str]:
    text = " ".join(text.split())
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = character
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    consumed = "".join(lines)
    if len(consumed) < len(text) and lines:
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def _cover(image: Any, width: int, height: int) -> Any:
    from PIL import Image

    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _load_background(stage: dict[str, Any]) -> Any:
    from PIL import Image, ImageDraw

    path = Path(stage["image"])
    if path.suffix.lower() != ".svg":
        with Image.open(path) as source:
            source.load()
            return _cover(source.convert("RGB"), WIDTH, HEIGHT)
    rasterizer = shutil.which("gdk-pixbuf-thumbnailer")
    if rasterizer:
        with tempfile.NamedTemporaryFile(suffix=".png") as temporary:
            result = subprocess.run(
                [rasterizer, "-s", str(max(WIDTH, HEIGHT)), str(path), temporary.name],
                check=False,
                capture_output=True,
                timeout=20,
            )
            if result.returncode == 0 and Path(temporary.name).stat().st_size > 0:
                with Image.open(temporary.name) as source:
                    source.load()
                    return _cover(source.convert("RGB"), WIDTH, HEIGHT)
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for index in range(12):
        inset = index * 42
        color = (
            min(40, BACKGROUND[0] + index * 2),
            min(78, BACKGROUND[1] + index * 4),
            min(84, BACKGROUND[2] + index * 4),
        )
        draw.ellipse((120 + inset, 40 + inset // 2, 1160 - inset, 820 - inset // 2), outline=color, width=3)
    return image


def render_stage_card(stage: dict[str, Any]) -> Any:
    from PIL import Image, ImageDraw

    image = _load_background(stage).convert("RGBA")
    veil = Image.new("RGBA", image.size, (0, 0, 0, 0))
    veil_draw = ImageDraw.Draw(veil)
    veil_draw.rectangle((0, 0, WIDTH, 112), fill=(3, 15, 20, 176))
    for row in range(250):
        alpha = round(30 + 205 * (row / 249))
        veil_draw.rectangle((0, 470 + row, WIDTH, 471 + row), fill=(3, 15, 20, alpha))
    image = Image.alpha_composite(image, veil)
    draw = ImageDraw.Draw(image)

    data_font = _font(22)
    chapter_font = _font(26, bold=True)
    title_font = _font(49, bold=True)
    label_font = _font(22, bold=True)
    body_font = _font(23)

    line_left = 84
    line_right = WIDTH - 84
    line_y = 48
    draw.line((line_left, line_y, line_right, line_y), fill=(233, 226, 208, 92), width=3)
    for number in range(4):
        x = line_left + round((line_right - line_left) * number / 3)
        active = number <= int(stage["round"])
        draw.ellipse((x - 8, line_y - 8, x + 8, line_y + 8), fill=MEMBRANE if active else (77, 96, 98))
        draw.text((x - 18, 66), f"{number:02d}", font=data_font, fill=FOSSIL if number == int(stage["round"]) else MUTED)

    draw.text((64, 126), stage["chapter"], font=chapter_font, fill=MEMBRANE)
    title_lines = _wrap(draw, stage["title"], title_font, WIDTH - 128, 2)
    for index, line in enumerate(title_lines):
        draw.text((64, 170 + index * 60), line, font=title_font, fill=FOSSIL)

    draw.text((64, 505), "环境", font=label_font, fill=SULFUR)
    draw.text((142, 505), stage["environment"], font=body_font, fill=FOSSIL)
    draw.text((660, 505), "偶发", font=label_font, fill=SULFUR)
    draw.text((738, 505), stage["contingency"], font=body_font, fill=FOSSIL)
    draw.text((64, 555), "你的选择", font=label_font, fill=IRON)
    draw.text((188, 555), stage["choice"], font=body_font, fill=FOSSIL)
    draw.text((64, 606), "留下的变化", font=label_font, fill=MEMBRANE)
    change_lines = _wrap(draw, stage["change"], body_font, WIDTH - 230, 2)
    for index, line in enumerate(change_lines):
        draw.text((218, 606 + index * 34), line, font=body_font, fill=FOSSIL)
    return image.convert("RGB")


def frame_sequence(cards: list[Any], fps: int = FPS) -> Iterator[Any]:
    from PIL import Image

    hold = max(1, round(HOLD_SECONDS * fps))
    transition = max(1, round(TRANSITION_SECONDS * fps))
    for index, card in enumerate(cards):
        for _ in range(hold):
            yield card
        if index + 1 < len(cards):
            following = cards[index + 1]
            for frame_no in range(1, transition + 1):
                yield Image.blend(card, following, frame_no / (transition + 1))


def expected_frame_count(card_count: int, fps: int = FPS) -> int:
    return card_count * max(1, round(HOLD_SECONDS * fps)) + max(0, card_count - 1) * max(
        1, round(TRANSITION_SECONDS * fps)
    )


def _encode_with_av(frames: Iterable[Any], output: Path, fps: int) -> str:
    import av
    import numpy as np

    container = av.open(str(output), mode="w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width = WIDTH
    stream.height = HEIGHT
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": "21", "preset": "medium"}
    for image in frames:
        frame = av.VideoFrame.from_ndarray(np.asarray(image, dtype="uint8"), format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return "PyAV / H.264"


def _encode_with_ffmpeg(frames: Iterable[Any], output: Path, fps: int) -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise LineageVideoError("neither PyAV nor ffmpeg is available")
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{WIDTH}x{HEIGHT}",
        "-framerate",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-crf",
        "21",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for image in frames:
            process.stdin.write(image.tobytes())
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise LineageVideoError("ffmpeg could not encode the recap")
    return "ffmpeg / H.264"


def render_recap(plan: dict[str, Any], output: Path, fps: int = FPS) -> dict[str, Any]:
    cards = [render_stage_card(stage) for stage in plan["stages"]]
    if len(cards) < 2:
        raise LineageVideoError("at least two stage cards are required")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + ".rendering.mp4")
    temporary.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        import av  # noqa: F401

        encoder = _encode_with_av(frame_sequence(cards, fps), temporary, fps)
    except ImportError:
        encoder = _encode_with_ffmpeg(frame_sequence(cards, fps), temporary, fps)
    if not temporary.is_file() or temporary.stat().st_size < 50_000:
        temporary.unlink(missing_ok=True)
        raise LineageVideoError("encoded recap is missing or unexpectedly small")
    temporary.replace(output)
    frame_count = expected_frame_count(len(cards), fps)
    return {
        "encoder": encoder,
        "width": WIDTH,
        "height": HEIGHT,
        "fps": fps,
        "frame_count": frame_count,
        "duration_seconds": round(frame_count / fps, 3),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "render_seconds": round(time.monotonic() - started, 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_path = args.session.expanduser().resolve()
    output = args.output.expanduser().resolve()
    session_dir = session_path.parent.resolve()
    if output.parent != session_dir:
        raise LineageVideoError("recap output must stay inside its session directory")
    session = read_json(session_path)
    plan = build_recap_plan(session, session_dir)
    result = render_recap(plan, output)
    manifest = output.with_suffix(".json")
    write_json(
        manifest,
        {
            **plan,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "output": {"filename": output.name, **result},
        },
    )
    print(f"LINEAGE_VIDEO:{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
