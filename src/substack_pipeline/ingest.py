"""Stage 1: read the LinkedIn scraper's JSONL, pull out GitHub repo links, dedupe.

Input format (one JSON object per line). Keys are forgiving: the reader accepts the common
names scrapers emit (`url`/`post_url`/`link`, `text`/`content`/`commentary`, `author`/`author_name`,
`date`/`posted_at`/`published_at`).

This module does NOT scrape LinkedIn. Point POSTS_PATH at whatever your existing scraper writes.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .models import Post

GITHUB_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/?#][^\s)\]]*)?",
    re.IGNORECASE,
)
# Paths under github.com that are not repos.
NOT_REPOS = {"features", "topics", "trending", "sponsors", "marketplace", "orgs", "settings",
             "explore", "collections", "events", "about", "pricing", "login", "join", "site"}


def extract_repo_links(text: str) -> list[str]:
    """Return canonical `owner/name` strings found in text, in order, without duplicates."""
    out: list[str] = []
    for m in GITHUB_RE.finditer(text or ""):
        owner, name = m.group(1), m.group(2)
        name = re.sub(r"\.git$", "", name)
        if owner.lower() in NOT_REPOS:
            continue
        full = f"{owner}/{name}"
        if full.lower() not in {o.lower() for o in out}:
            out.append(full)
    return out


def _get(d: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        if k in d and d[k]:
            return str(d[k])
    return default


def read_posts(path: Path) -> list[Post]:
    posts: list[Post] = []
    if not path.exists():
        return posts
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = _get(d, "text", "content", "commentary", "body")
            url = _get(d, "url", "post_url", "link", "postUrl")
            links = extract_repo_links(" ".join([text, *map(str, d.get("links", []) or [])]))  # text + resolved links
            posts.append(Post(
                url=url,
                author=_get(d, "author", "author_name", "authorName", "name"),
                text=text,
                posted_at=_get(d, "date", "posted_at", "published_at", "postedAt"),
                repo_links=links,
            ))
    return posts


def candidates_from_posts(posts: list[Post]) -> list[tuple[str, int, list[str]]]:
    """Group posts by repo. Returns [(full_name, mention_count, [post_urls])] sorted by mentions desc."""
    mentions: Counter[str] = Counter()
    sources: dict[str, list[str]] = defaultdict(list)
    canon: dict[str, str] = {}
    for p in posts:
        for full in p.repo_links:
            key = full.lower()
            canon.setdefault(key, full)
            mentions[key] += 1
            if p.url:
                sources[key].append(p.url)
    return [(canon[k], n, sources[k]) for k, n in mentions.most_common()]
