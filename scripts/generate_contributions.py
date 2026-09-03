"""Generate a custom, low-key contribution heatmap SVG for the profile README.

Output is a calendar-style grid (weeks as columns, days as rows) with
monochrome shading tied to a single accent. Falls back to an empty grid
when contribution data is not available.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

from github_data import GitHubData, collect


BG = "transparent"
FG = "#e6edf3"
DIM = "#8b949e"
ACCENT_LOW = "#161b22"
ACCENT_HIGH = "#58a6ff"


def level_for(value: int, max_value: int) -> float:
    if max_value <= 0 or value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(max_value))


def blend(low: str, high: str, t: float) -> str:
    """Linear blend of two #rrggbb colors."""
    def h2i(h: str) -> tuple[int, int, int]:
        return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    lr, lg, lb = h2i(low)
    hr, hg, hb = h2i(high)
    r = round(lr + (hr - lr) * t)
    g = round(lg + (hg - lg) * t)
    b = round(lb + (hb - lb) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def render_contributions(
    days: list[int], width: int, height: int, weeks: int = 52
) -> str:
    """Render the calendar heatmap.

    `days` is a flat list of contribution counts. We'll group it into
    `weeks` columns of 7 rows each. If fewer days are available, we pad
    the front of the list with zeros so the most recent weeks appear
    on the right.
    """
    if weeks <= 0:
        weeks = 52
    target = weeks * 7
    if len(days) < target:
        days = [0] * (target - len(days)) + list(days)
    else:
        days = list(days[-target:])

    cols = weeks
    rows = 7
    cell = 11
    gap = 3
    grid_w = cols * cell + (cols - 1) * gap
    grid_h = rows * cell + (rows - 1) * gap

    # Resize to fit the requested SVG width while keeping aspect sensible.
    scale = min((width - 80) / max(1, grid_w), (height - 40) / max(1, grid_h), 1.0)
    cell_s = cell * scale
    gap_s = gap * scale
    grid_w_s = cols * cell_s + (cols - 1) * gap_s
    grid_h_s = rows * cell_s + (rows - 1) * gap_s
    origin_x = (width - grid_w_s) / 2
    origin_y = (height - grid_h_s) / 2 + 8

    max_v = max(days) if days else 0
    total = sum(days)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="GitHub contribution activity">'
    )
    font_family = (
        "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', 'DejaVu Sans Mono', monospace"
    )
    parts.append(
        f'<g font-family="{font_family}" text-rendering="geometricPrecision">'
    )
    parts.append(
        f'<text x="20" y="24" font-size="12" fill="{DIM}" letter-spacing="0.5">'
        f'CONTRIBUTIONS · {total} in the last {weeks} weeks</text>'
    )

    for w in range(cols):
        for d in range(rows):
            idx = w * 7 + d
            v = days[idx] if idx < len(days) else 0
            lvl = level_for(v, max_v)
            color = blend(ACCENT_LOW, ACCENT_HIGH, lvl)
            x = origin_x + w * (cell_s + gap_s)
            y = origin_y + d * (cell_s + gap_s)
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_s:.2f}" '
                f'height="{cell_s:.2f}" rx="2" ry="2" fill="{color}">'
                f'<title>{v} contribution{"s" if v != 1 else ""}</title>'
                f'</rect>'
            )

    # Month labels along the top.
    if grid_w_s > 0 and weeks >= 12:
        approx_month_w = grid_w_s / 12
        for m in range(12):
            x = origin_x + m * approx_month_w
            label = ["jan", "feb", "mar", "apr", "may", "jun",
                     "jul", "aug", "sep", "oct", "nov", "dec"][m]
            parts.append(
                f'<text x="{x:.1f}" y="{origin_y - 6:.1f}" font-size="9" '
                f'fill="{DIM}">{escape(label)}</text>'
            )

    # Legend.
    legend_y = height - 12
    parts.append(
        f'<text x="{origin_x:.1f}" y="{legend_y:.1f}" font-size="9" '
        f'fill="{DIM}">less</text>'
    )
    for i, lvl in enumerate([0.0, 0.25, 0.5, 0.75, 1.0]):
        color = blend(ACCENT_LOW, ACCENT_HIGH, lvl)
        x = origin_x + 30 + i * (cell_s + 2)
        parts.append(
            f'<rect x="{x:.1f}" y="{legend_y - 8:.1f}" width="{cell_s:.2f}" '
            f'height="{cell_s:.2f}" rx="2" ry="2" fill="{color}"/>'
        )
    parts.append(
        f'<text x="{origin_x + 30 + 5 * (cell_s + 2) + 6:.1f}" y="{legend_y:.1f}" '
        f'font-size="9" fill="{DIM}">more</text>'
    )

    parts.append("</g></svg>")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data" / "profile.yaml")
    parser.add_argument("--github-data", type=Path, default=None,
                        help="Optional pre-collected GitHub data JSON.")
    args = parser.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    contrib_cfg = cfg.get("contributions", {})
    width = int(contrib_cfg.get("width", 900))
    height = int(contrib_cfg.get("height", 130))
    weeks = int(contrib_cfg.get("weeks", 52))
    output = Path(contrib_cfg.get("output", "generated/contributions.svg"))
    if not output.is_absolute():
        output = args.config.parent.parent / output

    days: list[int] = []
    if args.github_data and args.github_data.exists():
        try:
            import json
            payload = json.loads(args.github_data.read_text(encoding="utf-8"))
            days = list(payload.get("contributions", []))
        except Exception:  # noqa: BLE001
            days = []
    if not days:
        # Last-resort: try to fetch directly.
        username = cfg["profile"]["username"]
        data = collect(username)
        days = data.contributions

    output.parent.mkdir(parents=True, exist_ok=True)
    svg = render_contributions(days, width=width, height=height, weeks=weeks)
    output.write_text(svg, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
