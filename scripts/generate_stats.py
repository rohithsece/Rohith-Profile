"""Generate a minimal, dark-theme stats SVG for the profile README.

The card is intentionally understated: monospace text on a GitHub-dark
canvas, with thin dividers and a single accent color. No rainbow palettes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

from github_data import GitHubData


BG = "#0d1117"          # GitHub dark
PANEL = "#161b22"        # subtle inset
FG = "#e6edf3"           # primary text
DIM = "#8b949e"          # secondary text
DIV = "#30363d"          # divider
ACCENT = "#58a6ff"       # GitHub-style subtle blue


def render_stats(
    data: GitHubData,
    width: int,
    height: int,
) -> str:
    user = data.user
    repos = data.repos
    total_stars = data.total_stars
    top_languages = data.top_languages

    repositories = user.public_repos if user else 0
    followers = user.followers if user else 0
    following = user.following if user else 0
    top_lang = top_languages[0][0] if top_languages else "—"
    contribs = sum(data.contributions) if data.contributions else 0

    label_y = 38
    value_y = 70

    cells = [
        ("repositories", str(repositories), label_y, value_y),
        ("followers", str(followers), label_y, value_y + 40),
        ("stars", str(total_stars), label_y, value_y + 80),
        ("top language", str(top_lang), label_y, value_y + 120),
        ("contributions", str(contribs) if contribs else "—", label_y, value_y + 160),
    ]

    cell_w = (width - 40) / len(cells)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="GitHub stats for the profile">'
    )
    parts.append(
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" '
        f'rx="8" ry="8" fill="{BG}" stroke="{DIV}"/>'
    )

    font_family = (
        "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', 'DejaVu Sans Mono', monospace"
    )
    parts.append(
        f'<g font-family="{font_family}" text-rendering="geometricPrecision">'
    )

    for i, (label, value, _, _) in enumerate(cells):
        x = 20 + i * cell_w + cell_w / 2
        parts.append(
            f'<text x="{x:.1f}" y="{label_y}" font-size="11" '
            f'fill="{DIM}" text-anchor="middle" letter-spacing="0.5">'
            f'{escape(label.upper())}</text>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{value_y}" font-size="22" '
            f'fill="{FG}" text-anchor="middle" font-weight="600">'
            f'{escape(value)}</text>'
        )
        if i > 0:
            x_div = 20 + i * cell_w
            parts.append(
                f'<line x1="{x_div:.1f}" y1="22" x2="{x_div:.1f}" y2="{height - 22}" '
                f'stroke="{DIV}" stroke-width="1"/>'
            )

    parts.append("</g></svg>")
    return "".join(parts)


def render_repo_cards(
    repos, width: int, *, max_cards: int = 3, card_height: int = 90
) -> str:
    """Render a small set of repository cards under the main stats bar."""
    if not repos:
        return ""
    cards = sorted(
        repos,
        key=lambda r: (r.stars, r.updated_at),
        reverse=True,
    )[:max_cards]

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} '
        f'{(card_height + 18) * len(cards) + 8}" width="100%" role="img" '
        f'aria-label="Top repositories">'
    )

    font_family = (
        "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', 'DejaVu Sans Mono', monospace"
    )
    parts.append(
        f'<g font-family="{font_family}" text-rendering="geometricPrecision">'
    )
    y = 8
    for repo in cards:
        parts.append(
            f'<rect x="0.5" y="{y}" width="{width - 1}" height="{card_height}" '
            f'rx="6" ry="6" fill="{PANEL}" stroke="{DIV}"/>'
        )
        parts.append(
            f'<text x="18" y="{y + 28}" font-size="14" fill="{ACCENT}" '
            f'font-weight="600">{escape(repo.name)}</text>'
        )
        meta_bits = []
        if repo.language:
            meta_bits.append(escape(repo.language))
        if repo.stars:
            meta_bits.append(f"★ {repo.stars}")
        if repo.forks:
            meta_bits.append(f"⑂ {repo.forks}")
        if meta_bits:
            parts.append(
                f'<text x="18" y="{y + 50}" font-size="11" fill="{DIM}">'
                f'{"  ·  ".join(meta_bits)}</text>'
            )
        if repo.description:
            # Trim and clip long descriptions.
            text = repo.description.strip()
            if len(text) > 120:
                text = text[:117] + "…"
            parts.append(
                f'<text x="18" y="{y + 72}" font-size="12" fill="{FG}">'
                f'{escape(text)}</text>'
            )
        y += card_height + 18

    parts.append("</g></svg>")
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).resolve().parent.parent / "data" / "profile.yaml")
    parser.add_argument("--github-data", type=Path, default=None,
                        help="Optional pre-collected GitHub data JSON to render from.")
    args = parser.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    stats_cfg = cfg.get("stats", {})
    width = int(stats_cfg.get("width", 900))
    height = int(stats_cfg.get("height", 110))
    output = Path(stats_cfg.get("output", "generated/stats.svg"))
    if not output.is_absolute():
        output = args.config.parent.parent / output

    # Prefer pre-collected JSON if supplied (so we can decouple fetches
    # and rendering), otherwise call the fetcher.
    if args.github_data and args.github_data.exists():
        raw = args.github_data.read_text(encoding="utf-8")
        from github_data import GitHubData
        # Re-importing the dataclass to keep the function above pure.
        import github_data as gd
        # Build via a no-network rehydration path.
        data = gd.GitHubData()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if payload.get("user"):
            u = payload["user"]
            from github_data import UserInfo
            data.user = UserInfo(**u)
        from github_data import RepoInfo
        data.repos = [RepoInfo(**r) for r in payload.get("repos", [])]
        data.total_stars = int(payload.get("total_stars", 0))
        data.top_languages = [tuple(x) for x in payload.get("top_languages", [])]
        data.contributions = list(payload.get("contributions", []))
        data.error = payload.get("error")
    else:
        username = cfg["profile"]["username"]
        from github_data import collect
        data = collect(username)

    output.parent.mkdir(parents=True, exist_ok=True)
    svg = render_stats(data, width=width, height=height)
    output.write_text(svg, encoding="utf-8")
    print(f"wrote {output}")

    cards_path = output.with_name("repo_cards.svg")
    cards_svg = render_repo_cards(data.repos, width=width)
    if cards_svg:
        cards_path.write_text(cards_svg, encoding="utf-8")
        print(f"wrote {cards_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
