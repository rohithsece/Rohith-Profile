"""Orchestrator that regenerates every artifact in the profile repository.

Pipeline:
  1. Load data/profile.yaml
  2. Fetch live GitHub data (with graceful failure)
  3. Cache the fetched data as JSON for the per-asset scripts
  4. Run ASCII, stats, contributions generators
  5. Render templates/README.template.md into README.md

Local use:
    python scripts/generate_readme.py
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

import github_data as gd
from generate_ascii import generate as generate_ascii
from generate_contributions import render_contributions
from generate_stats import render_repo_cards, render_stats


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "data" / "profile.yaml"
DEFAULT_TEMPLATE = REPO_ROOT / "templates" / "README.template.md"
DEFAULT_README = REPO_ROOT / "README.md"
DEFAULT_CACHE = REPO_ROOT / "generated" / "github_data.json"


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def collect_github(username: str, weeks: int) -> gd.GitHubData:
    return gd.collect(username, weeks=weeks)


def cache_github_data(data: gd.GitHubData, path: Path) -> None:
    payload = {
        "user": asdict(data.user) if data.user else None,
        "repos": [asdict(r) for r in data.repos],
        "total_stars": data.total_stars,
        "top_languages": data.top_languages,
        "contributions": data.contributions,
        "error": data.error,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_cached_github_data(path: Path) -> gd.GitHubData | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    data = gd.GitHubData()
    if payload.get("user"):
        data.user = gd.UserInfo(**payload["user"])
    data.repos = [gd.RepoInfo(**r) for r in payload.get("repos", [])]
    data.total_stars = int(payload.get("total_stars", 0))
    data.top_languages = [tuple(x) for x in payload.get("top_languages", [])]
    data.contributions = list(payload.get("contributions", []))
    data.error = payload.get("error")
    return data


def render_inline_list(items: list[str]) -> str:
    """Render a list of skills as a single monospace line of tokens."""
    return " · ".join(f"`{item}`" for item in items)


def render_education(edu: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for e in edu:
        bits: list[str] = []
        if e.get("degree"):
            bits.append(f"**{e['degree']}**")
        if e.get("field"):
            bits.append(f"in *{e['field']}*")
        if e.get("institution"):
            bits.append(f"at _({e['institution']})_")
        if e.get("batch"):
            bits.append(f"`{e['batch']}`")
        if e.get("score"):
            bits.append(f"**{e['score']}**")
        if bits:
            lines.append(" &nbsp;·&nbsp; ".join(bits))
    return "<br>".join(lines) if lines else "_n/a_"


def render_projects(projects: list[dict[str, Any]], repos: list[gd.RepoInfo]) -> str:
    blocks: list[str] = []
    for p in projects:
        name = p["name"]
        candidates = p.get("repo_candidates", [])
        match = gd.find_repo_for_project(candidates, repos)
        if match:
            name_md = f"[{name}]({match.html_url})"
        else:
            name_md = f"**{name}**"

        techs = " · ".join(f"`{t}`" for t in p.get("technologies", []))
        desc = p.get("description", "").strip()

        block_lines: list[str] = []
        block_lines.append(f"### {name_md}")
        if techs:
            block_lines.append(f"<sub>{techs}</sub>")
        if desc:
            block_lines.append("")
            block_lines.append(desc)
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks) if blocks else "_n/a_"


def render_experience(experience: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for e in experience:
        company = e.get("company", "")
        role = e.get("role", "")
        period = e.get("period", "")
        focus = e.get("focus", [])

        head = f"**{company}**  ·  _{role}_"
        if period:
            head += f"  ·  `{period}`"
        focus_line = ""
        if focus:
            focus_line = "\n<sub>" + " · ".join(focus) + "</sub>"

        blocks.append(head + focus_line)
    return "\n\n".join(blocks) if blocks else "_n/a_"


def render_achievements(achievements: dict[str, Any]) -> str:
    blocks: list[str] = []

    for pub in achievements.get("publications", []):
        title = pub.get("title", "")
        venue = pub.get("venue", "")
        url = pub.get("url", "")
        line = f"📄 **{title}**" if False else f"📄 **{title}**"
        if url:
            line += f"  \n<sub>↳ [{venue}]({url})</sub>" if venue else f"  \n<sub>↳ {url}</sub>"
        elif venue:
            line += f"  \n<sub>↳ {venue}</sub>"
        blocks.append(line)

    awards = achievements.get("awards", [])
    if awards:
        award_lines: list[str] = []
        for a in awards:
            title = a.get("title", "")
            event = a.get("event", "")
            org = a.get("org", "")
            bits = [f"- **{title}**"]
            sub_bits: list[str] = []
            if event:
                sub_bits.append(event)
            if org:
                sub_bits.append(org)
            if sub_bits:
                bits.append(f"  <sub>{' · '.join(sub_bits)}</sub>")
            award_lines.append("\n".join(bits))
        blocks.append("\n".join(award_lines))

    coding = achievements.get("coding", [])
    if coding:
        bits = []
        for c in coding:
            platform = c.get("platform", "")
            stat = c.get("stat", "")
            if platform and stat:
                bits.append(f"`{platform}` — {stat}")
        if bits:
            blocks.append("  \n".join(bits))

    return "\n\n".join(blocks) if blocks else "_n/a_"


def render_certifications(certifications: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for cert in certifications:
        issuer = cert.get("issuer", "")
        items = cert.get("items", [])
        if not items:
            continue
        head = f"**{issuer}**" if issuer else ""
        lines = [f"- {item}" for item in items]
        block = head + "\n" + "\n".join(lines) if head else "\n".join(lines)
        blocks.append(block)
    return "\n\n".join(blocks) if blocks else "_n/a_"


def render_contact(social: dict[str, dict[str, str]]) -> str:
    items: list[str] = []
    for _key, info in social.items():
        label = info.get("label", "")
        url = info.get("url", "")
        if not url:
            continue
        items.append(f"[{label} ↗]({url})")
    return " &nbsp;·&nbsp; ".join(items) if items else "_n/a_"


def render_about(about: str) -> str:
    # Trim + normalize whitespace.
    return re.sub(r"\s+", " ", about).strip()


def render_template(template: str, replacements: dict[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        key = match.group(1)
        return replacements.get(key, match.group(0))

    pattern = re.compile(r"\{([a-zA-Z0-9_]+)\}")
    return pattern.sub(sub, template)


def generate(config_path: Path) -> Path:
    cfg = load_config(config_path)

    profile = cfg.get("profile", {})
    stack = cfg.get("stack", {})
    ascii_cfg = cfg.get("ascii", {})
    stats_cfg = cfg.get("stats", {})
    contrib_cfg = cfg.get("contributions", {})

    username = profile.get("username", "rohithsece")
    weeks = int(contrib_cfg.get("weeks", 52))

    # 1. Fetch live GitHub data (best effort).
    data = collect_github(username, weeks=weeks)
    cache_github_data(data, DEFAULT_CACHE)

    # 2. Generate ASCII art.
    generate_ascii(config_path)

    # 3. Generate stats + repo cards.
    stats_svg = render_stats(
        data,
        width=int(stats_cfg.get("width", 900)),
        height=int(stats_cfg.get("height", 110)),
    )
    stats_path = Path(stats_cfg.get("output", "generated/stats.svg"))
    if not stats_path.is_absolute():
        stats_path = config_path.parent.parent / stats_path
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(stats_svg, encoding="utf-8")

    cards_path = stats_path.with_name("repo_cards.svg")
    cards_svg = render_repo_cards(data.repos, width=int(stats_cfg.get("width", 900)))
    if cards_svg:
        cards_path.write_text(cards_svg, encoding="utf-8")

    # 4. Generate contribution graph.
    contrib_svg = render_contributions(
        data.contributions,
        width=int(contrib_cfg.get("width", 900)),
        height=int(contrib_cfg.get("height", 130)),
        weeks=weeks,
    )
    contrib_path = Path(contrib_cfg.get("output", "generated/contributions.svg"))
    if not contrib_path.is_absolute():
        contrib_path = config_path.parent.parent / contrib_path
    contrib_path.write_text(contrib_svg, encoding="utf-8")

    # 5. Render the README from the template.
    template_path = DEFAULT_TEMPLATE
    template = template_path.read_text(encoding="utf-8")

    about = render_about(profile.get("about", ""))
    education_md = render_education(profile.get("education", []))
    projects_md = render_projects(cfg.get("projects", []), data.repos)
    experience_md = render_experience(cfg.get("experience", []))
    achievements_md = render_achievements(cfg.get("achievements", {}))
    certifications_md = render_certifications(cfg.get("certifications", []))
    contact_md = render_contact(cfg.get("social", {}))

    replacements: dict[str, str] = {
        "name": profile.get("name", username),
        "tagline": profile.get("tagline", ""),
        "about": about,
        "education": education_md,
        "languages": render_inline_list(stack.get("languages", [])),
        "frontend": render_inline_list(stack.get("frontend", [])),
        "backend": render_inline_list(stack.get("backend", [])),
        "databases": render_inline_list(stack.get("databases", [])),
        "ai_ml": render_inline_list(stack.get("ai_ml", [])),
        "other": render_inline_list(stack.get("other", [])),
        "projects": projects_md,
        "experience": experience_md,
        "achievements": achievements_md,
        "certifications": certifications_md,
        "contact": contact_md,
        "updated": _dt.date.today().isoformat(),
    }

    readme_md = render_template(template, replacements)
    DEFAULT_README.write_text(readme_md, encoding="utf-8")
    print(f"wrote {DEFAULT_README}")
    if data.error:
        print(f"warning: GitHub API partially failed: {data.error}")
    return DEFAULT_README


def main(argv: list[str] | None = None) -> int:
    class Args:  # minimal shim so we can use --config without argparse noise
        config = DEFAULT_CONFIG
    args = Args()
    if argv and len(argv) >= 2 and argv[0] == "--config":
        args.config = Path(argv[1])
    try:
        generate(args.config)
    except Exception as exc:  # noqa: BLE001
        # Never let the README generator destroy the existing README.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
