"""GitHub data fetcher for the profile README generator.

Uses the REST + GraphQL APIs to collect user, repo and contribution info.
Designed to fail safely: if any request errors (rate limit, network,
missing token), the caller can substitute zero/empty defaults.

Set the env var GITHUB_TOKEN to a GitHub personal access token (or rely on
the GITHUB_TOKEN injected by GitHub Actions) to raise the rate limit and
enable contribution data.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
DEFAULT_TIMEOUT = 15


class GitHubError(RuntimeError):
    """Raised when a GitHub API call fails in a non-recoverable way."""


@dataclass
class RepoInfo:
    name: str
    full_name: str
    html_url: str
    description: str | None
    language: str | None
    stars: int
    forks: int
    topics: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class UserInfo:
    login: str
    name: str | None
    bio: str | None
    public_repos: int
    followers: int
    following: int
    avatar_url: str
    html_url: str
    created_at: str = ""


@dataclass
class GitHubData:
    user: UserInfo | None = None
    repos: list[RepoInfo] = field(default_factory=list)
    total_stars: int = 0
    top_languages: list[tuple[str, int]] = field(default_factory=list)
    contributions: list[int] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip() or None
    return None


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
             body: dict[str, Any] | None = None, timeout: int = DEFAULT_TIMEOUT
             ) -> Any:
    req_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rohithsece-profile-generator",
    }
    if headers:
        req_headers.update(headers)
    token = _token()
    if token:
        req_headers["Authorization"] = f"Bearer {token}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    request = Request(url, data=data, method=method, headers=req_headers)
    try:
        with urlopen(request, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except HTTPError as exc:
        # Rate limits are surfaced as 403 with a message. Don't crash.
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            detail = str(exc)
        raise GitHubError(f"GitHub API error {exc.code} for {url}: {detail}") from exc
    except URLError as exc:
        raise GitHubError(f"Network error for {url}: {exc}") from exc


def fetch_user(username: str) -> UserInfo:
    payload = _request(f"{GITHUB_API}/users/{username}")
    return UserInfo(
        login=payload.get("login", username),
        name=payload.get("name"),
        bio=payload.get("bio"),
        public_repos=int(payload.get("public_repos", 0) or 0),
        followers=int(payload.get("followers", 0) or 0),
        following=int(payload.get("following", 0) or 0),
        avatar_url=payload.get("avatar_url", ""),
        html_url=payload.get("html_url", f"https://github.com/{username}"),
        created_at=payload.get("created_at", ""),
    )


def fetch_repos(username: str) -> list[RepoInfo]:
    repos: list[RepoInfo] = []
    page = 1
    while page < 10:  # safety guard: 1000 repo cap is enough for a personal page
        url = f"{GITHUB_API}/users/{username}/repos?per_page=100&page={page}&sort=updated"
        try:
            payload = _request(url)
        except GitHubError:
            break
        if not isinstance(payload, list) or not payload:
            break
        for r in payload:
            if r.get("fork"):
                continue
            repos.append(
                RepoInfo(
                    name=r.get("name", ""),
                    full_name=r.get("full_name", ""),
                    html_url=r.get("html_url", ""),
                    description=r.get("description") or "",
                    language=r.get("language") or None,
                    stars=int(r.get("stargazers_count", 0) or 0),
                    forks=int(r.get("forks_count", 0) or 0),
                    topics=list(r.get("topics", []) or []),
                    updated_at=r.get("updated_at", ""),
                )
            )
        if len(payload) < 100:
            break
        page += 1
    return repos


def fetch_contributions(username: str, weeks: int = 52) -> list[int]:
    """Return a list of contribution counts per day for the past `weeks` weeks.

    Uses GraphQL when a token is available; otherwise returns [].
    """
    if not _token():
        return []
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    try:
        payload = _request(
            GITHUB_GRAPHQL,
            method="POST",
            body={"query": query, "variables": {"login": username}},
        )
    except GitHubError:
        return []
    weeks_data = (
        payload.get("data", {})
        .get("user", {})
        .get("contributionsCollection", {})
        .get("contributionCalendar", {})
        .get("weeks", [])
    )
    days: list[int] = []
    for w in weeks_data[-weeks:]:
        for d in w.get("contributionDays", []):
            days.append(int(d.get("contributionCount", 0) or 0))
    return days


def aggregate_top_languages(repos: list[RepoInfo]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in repos:
        if r.language:
            counts[r.language] = counts.get(r.language, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def collect(username: str, *, weeks: int = 52) -> GitHubData:
    """Fetch everything we can for `username` without crashing the README."""
    data = GitHubData()
    try:
        data.user = fetch_user(username)
    except GitHubError as exc:
        data.error = str(exc)
    try:
        data.repos = fetch_repos(username)
    except GitHubError as exc:
        data.error = (data.error + "; " if data.error else "") + str(exc)
    data.total_stars = sum(r.stars for r in data.repos)
    data.top_languages = aggregate_top_languages(data.repos)
    try:
        data.contributions = fetch_contributions(username, weeks=weeks)
    except GitHubError as exc:
        data.error = (data.error + "; " if data.error else "") + str(exc)
    return data


def find_repo_for_project(
    project_candidates: list[str], repos: list[RepoInfo]
) -> RepoInfo | None:
    """Best-effort: match a project to a public repo by candidate names.

    Matching is case-insensitive and tries to handle dashes vs spaces.
    """
    if not project_candidates:
        return None
    norm = lambda s: s.lower().replace("_", "-").replace(" ", "-")
    repo_index = {norm(r.name): r for r in repos}

    for cand in project_candidates:
        key = norm(cand)
        if key in repo_index:
            return repo_index[key]
    # Substring fallback
    for cand in project_candidates:
        key = norm(cand)
        for rname, r in repo_index.items():
            if key in rname or rname in key:
                return r
    return None


if __name__ == "__main__":
    # Lightweight CLI for local debugging.
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "rohithsece"
    out = collect(name)
    print(json.dumps(
        {
            "user": out.user.__dict__ if out.user else None,
            "repo_count": len(out.repos),
            "total_stars": out.total_stars,
            "top_languages": out.top_languages,
            "contributions_days": len(out.contributions),
            "error": out.error,
        },
        indent=2,
    ))
