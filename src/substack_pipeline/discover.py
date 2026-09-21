"""Stage 1 (GitHub-native): find candidate repos from public GitHub signals, no LinkedIn.

Two sources, both ToS-clean:

1. GitHubTrendingRSS feeds (https://mshibanami.github.io/GitHubTrendingRSS): hosted RSS of
   github.com/trending. Items carry `<title>owner/repo</title>` and `<link>https://github.com/owner/repo</link>`.
2. GitHub Search API: repos created in the last N days with at least M stars, newest-hot first.

Output has the same shape as `ingest.candidates_from_posts` so enrich/score need no changes:
`[(full_name, mention_count, sources)]`, where mention_count is the number of distinct sources
(feed URLs / search labels) that surfaced the repo.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

from .github_enrich import GitHub
from .ingest import extract_repo_links

log = logging.getLogger(__name__)
TIMEOUT = 30

Sighting = tuple[str, str]  # (owner/repo, source label)


def fetch_trending(feeds: list[str], session=None) -> list[Sighting]:
    """Fetch each RSS feed and return (full_name, feed_url) per item. A failing feed is skipped with a warning."""
    s = session or requests
    out: list[Sighting] = []
    for url in feeds:
        try:
            r = s.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except (requests.RequestException, ET.ParseError) as e:
            log.warning("feed %s failed: %s", url, e)
            continue
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip() or (item.findtext("title") or "").strip()
            links = extract_repo_links(link) or extract_repo_links(f"github.com/{link}")
            if links:
                out.append((links[0], url))
    return out


def search_new_repos(gh: GitHub, days_back: int, min_stars: int, per_page: int = 50,
                     languages: list[str] | None = None, today: date | None = None) -> list[Sighting]:
    """Search repos created in the last `days_back` days with >= `min_stars` stars, most-starred first.

    One query per language when `languages` is given, otherwise a single unfiltered query. `today` is
    injectable so the `created:>` cutoff is deterministic in tests.
    """
    today = today or datetime.now(timezone.utc).date()
    since = (today - timedelta(days=days_back)).isoformat()
    base = f"created:>{since} stars:>={min_stars}"
    queries = [f"{base} language:{lang}" for lang in languages] if languages else [base]
    out: list[Sighting] = []
    for q in queries:
        try:
            items = gh.search_repos(q, per_page=per_page, sort="stars", order="desc")
        except requests.RequestException as e:
            log.warning("search %r failed: %s", q, e)
            continue
        label = f"github-search:{q}"
        out.extend((it["full_name"], label) for it in items if it.get("full_name"))
    return out


def collect_sightings(feeds: list[str], gh: GitHub, days_back: int, min_stars: int,
                      languages: list[str] | None = None, today: date | None = None) -> list[Sighting]:
    """Every raw (repo, source) pair from trending feeds + new-repo search, before dedupe."""
    return fetch_trending(feeds) + search_new_repos(gh, days_back, min_stars, languages=languages, today=today)


def candidates_from_sightings(sightings: list[Sighting]) -> list[tuple[str, int, list[str]]]:
    """Group sightings by repo (case-insensitive). Returns [(full_name, distinct_sources, [sources])]
    sorted by source count desc; ties keep first-sighting order (feed rank, then star-desc search order)."""
    canon: dict[str, str] = {}
    sources: dict[str, list[str]] = {}
    for full, src in sightings:
        key = full.lower()
        canon.setdefault(key, full)
        srcs = sources.setdefault(key, [])
        if src not in srcs:
            srcs.append(src)
    return sorted(((canon[k], len(v), v) for k, v in sources.items()), key=lambda c: -c[1])  # stable: ties keep order


def discover_candidates(feeds: list[str], gh: GitHub, days_back: int, min_stars: int,
                        languages: list[str] | None = None, today: date | None = None) -> list[tuple[str, int, list[str]]]:
    """Trending feeds + new-repo search, merged into the `candidates_from_posts` shape."""
    return candidates_from_sightings(collect_sightings(feeds, gh, days_back, min_stars, languages, today))
