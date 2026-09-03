"""Convert a portrait photograph into a detailed ASCII portrait SVG.

The output is a GitHub-friendly SVG that renders the ASCII characters in a
monospace font on a transparent background. The intent is to display as a
single, large block of art at the top of the profile README.

Usage:
    python scripts/generate_ascii.py [--config data/profile.yaml]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

import yaml
from PIL import Image, ImageEnhance, ImageOps


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def crop_image(img: Image.Image, crop_cfg: dict | None) -> Image.Image:
    if not crop_cfg:
        return img
    left = int(crop_cfg.get("left", 0))
    top = int(crop_cfg.get("top", 0))
    right = int(crop_cfg.get("right", 0))
    bottom = int(crop_cfg.get("bottom", 0))
    w, h = img.size
    box = (
        max(0, left),
        max(0, top),
        max(1, w - right),
        max(1, h - bottom),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return img
    return img.crop(box)


def to_grayscale(img: Image.Image) -> Image.Image:
    return img.convert("L")


def resize_for_ascii(
    img: Image.Image, target_width: int, vertical_compensation: float
) -> Image.Image:
    w, h = img.size
    target_height = max(1, int(round(h * target_width / w * vertical_compensation)))
    return img.resize((target_width, target_height), Image.LANCZOS)


def adjust_contrast(img: Image.Image, contrast: float) -> Image.Image:
    if contrast == 1.0:
        return img
    return ImageEnhance.Contrast(img).enhance(contrast)


def map_brightness_to_ascii(
    img: Image.Image, ramp: str, invert: bool = False
) -> list[str]:
    """Map each pixel's brightness to a single character from the ramp.

    The ramp goes from darkest character on the left to lightest on the right.
    Dark background areas should be light characters on a dark canvas if the
    photo is dark, so we invert the mapping by default.
    """
    if invert:
        ramp = ramp[::-1]
    chars = list(ramp)
    if not chars:
        raise ValueError("ASCII ramp is empty")
    ramp_max = len(chars) - 1

    pixels = img.load()
    width, height = img.size
    lines: list[str] = []
    for y in range(height):
        line_chars: list[str] = []
        for x in range(width):
            v = pixels[x, y]
            idx = int(round((v / 255.0) * ramp_max))
            line_chars.append(chars[idx])
        lines.append("".join(line_chars))
    return lines


def render_svg(
    lines: list[str],
    char_width: float,
    line_height: float,
    font_size: float,
    fg: str,
    bg: str | None,
) -> str:
    """Render the ASCII lines as an SVG with one <text> per line.

    Using a <text> per row gives sharp, monospaced output and avoids the
    rendering quirks of <tspan> with newlines.
    """
    width_px = max(1, int(round(len(lines[0]) * char_width))) if lines else 1
    height_px = max(1, int(round(len(lines) * line_height)))

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width_px} {height_px}" '
        f'width="100%" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="ASCII portrait of Rohith S">'
    )
    if bg is not None:
        parts.append(f'<rect width="100%" height="100%" fill="{bg}"/>')

    # Font-family is intentionally monospace. The fallback chain is verbose
    # so GitHub's renderer picks something that exists.
    font_family = (
        "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', 'DejaVu Sans Mono', monospace"
    )
    parts.append(
        f'<g font-family="{font_family}" font-size="{font_size}" '
        f'fill="{fg}" text-rendering="geometricPrecision" '
        f'shape-rendering="crispEdges">'
    )
    for i, line in enumerate(lines):
        y = (i + 1) * line_height
        # Escape XML-sensitive characters in the ASCII art itself.
        safe_line = escape(line)
        parts.append(
            f'<text x="0" y="{y:.2f}" xml:space="preserve">{safe_line}</text>'
        )
    parts.append("</g></svg>")
    return "".join(parts)


def derive_metrics(
    line_count: int, line_width: int, target_visual_width: int = 900
) -> tuple[float, float, float]:
    """Pick font size + metrics so the SVG fits roughly target_visual_width px.

    We aim for roughly target_visual_width pixels wide on a typical browser
    render. GitHub will scale the SVG with width="100%" via the parent
    <img>, but the viewBox keeps the proportions correct.
    """
    char_width = target_visual_width / max(1, line_width)
    # A monospace cell is roughly 0.6em wide and 1.2em tall.
    font_size = char_width / 0.6
    line_height = font_size * 1.0
    return char_width, line_height, font_size


def generate(config_path: Path) -> Path:
    cfg = load_config(config_path)
    ascii_cfg = cfg.get("ascii", {}) or {}

    image_path = Path(ascii_cfg.get("image", "assets/profile.jpg"))
    output_path = Path(ascii_cfg.get("output", "generated/ascii.svg"))
    ramp = ascii_cfg.get("ramp", "@%#*+=-:. ")
    width = int(ascii_cfg.get("width", 110))
    vertical_compensation = float(ascii_cfg.get("vertical_compensation", 0.5))
    contrast = float(ascii_cfg.get("contrast", 1.25))
    invert = bool(ascii_cfg.get("invert", False))
    crop_cfg = ascii_cfg.get("crop")

    if not image_path.is_absolute():
        image_path = config_path.parent.parent / image_path
    if not output_path.is_absolute():
        output_path = config_path.parent.parent / output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not image_path.exists():
        raise FileNotFoundError(f"Source image not found: {image_path}")

    with Image.open(image_path) as raw:
        img = raw.copy()
    img = crop_image(img, crop_cfg)
    img = to_grayscale(img)
    img = adjust_contrast(img, contrast)
    img = resize_for_ascii(img, width, vertical_compensation)

    lines = map_brightness_to_ascii(img, ramp, invert=invert)
    # Trim trailing whitespace on every row so the SVG width matches content.
    lines = [line.rstrip() for line in lines]

    line_width = max(len(line) for line in lines) if lines else 0
    char_width, line_height, font_size = derive_metrics(len(lines), line_width)
    svg = render_svg(
        lines,
        char_width=char_width,
        line_height=line_height,
        font_size=font_size,
        fg="#e6edf3",
        bg=None,
    )

    output_path.write_text(svg, encoding="utf-8")
    return output_path


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "profile.yaml",
        help="Path to the profile YAML config.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = generate(args.config)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
