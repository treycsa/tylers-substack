"""Stage 2: enrich each candidate from the GitHub REST API (stars, commits, license, CI, README)."""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from .models import Repo

log = logging.getLogger(__name__)
API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str | None, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers.update({"Accept": "application/vnd.github+json", "User-Agent": "tylers-substack-pipeline"})
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, **params):
        for attempt in range(3):
            r = self.s.get(f"{API}{path}", params=params, timeout=30)
            if self._rate_limited(r):
                wait = self._retry_after(r)
                log.warning("rate limited (%s); sleeping %ss", r.status_code, wait)
                time.sleep(min(wait, 120))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        return None

    @staticmethod
    def _rate_limited(r) -> bool:
        """429 (primary + secondary limits nowadays) or the older 403 rate-limit shape."""
        if r.status_code == 429:
            return True
        return r.status_code == 403 and ("Retry-After" in r.headers or "rate limit" in r.text.lower())

    @staticmethod
    def _retry_after(r) -> int:
        """Seconds to wait: Retry-After when present, else until X-RateLimit-Reset, else 60."""
        ra = r.headers.get("Retry-After")
        if ra and str(ra).isdigit():
            return max(1, int(ra))
        reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
        return max(1, reset - int(time.time()))

    def search_repos(self, q: str, per_page: int = 50, sort: str = "stars", order: str = "desc") -> list[dict]:
        """GET /search/repositories; returns the raw `items` list (empty on 404 / rate-limit exhaustion)."""
        d = self._get("/search/repositories", q=q, per_page=per_page, sort=sort, order=order)
        return list((d or {}).get("items", []))

    def repo(self, full_name: str) -> Repo | None:
        d = self._get(f"/repos/{full_name}")
        if not d:
            return None
        repo = Repo(
            full_name=d["full_name"],
            url=d["html_url"],
            description=d.get("description") or "",
            stars=d.get("stargazers_count", 0),
            forks=d.get("forks_count", 0),
            created_at=d.get("created_at", ""),
            pushed_at=d.get("pushed_at", ""),
            open_issues=d.get("open_issues_count", 0),
            license=(d.get("license") or {}).get("spdx_id"),
            language=d.get("language"),
        )
        self._commits(repo)
        self._release(repo)
        self._ci(repo)
        self._readme(repo)
        self._stars_7d(repo)
        return repo

    def _commits(self, repo: Repo) -> None:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        commits = self._get(f"/repos/{repo.full_name}/commits", since=since, per_page=100) or []
        repo.commits_30d = len(commits)
        repo.unique_committers_30d = len({
            (c.get("author") or {}).get("login") or (c.get("commit", {}).get("author") or {}).get("email")
            for c in commits
        } - {None})

    def _release(self, repo: Repo) -> None:
        rel = self._get(f"/repos/{repo.full_name}/releases/latest")
        repo.latest_release = rel.get("tag_name") if rel else None

    def _ci(self, repo: Repo) -> None:
        wf = self._get(f"/repos/{repo.full_name}/actions/workflows")
        repo.has_ci = bool(wf and wf.get("total_count", 0) > 0)

    def _readme(self, repo: Repo) -> None:
        d = self._get(f"/repos/{repo.full_name}/readme")
        if d and d.get("content"):
            try:
                repo.readme = base64.b64decode(d["content"]).decode("utf-8", errors="replace")[:12000]
            except Exception:  # noqa: BLE001
                repo.readme = ""

    def _stars_7d(self, repo: Repo) -> None:
        """Approximate stars in the last 7 days by reading the newest page of stargazers with timestamps."""
        headers = {"Accept": "application/vnd.github.star+json"}
        # Page from the end: the API pages oldest-first, so compute the last page.
        per_page = 100
        last_page = max(1, -(-repo.stars // per_page))
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        count = 0
        for page in (last_page, last_page - 1):
            if page < 1:
                break
            r = self.s.get(f"{API}/repos/{repo.full_name}/stargazers",
                           params={"per_page": per_page, "page": page}, headers=headers, timeout=30)
            if r.status_code != 200:
                break
            for s in r.json():
                ts = s.get("starred_at")
                if ts and datetime.fromisoformat(ts.replace("Z", "+00:00")) >= cutoff:
                    count += 1
        repo.stars_7d = count


def enrich(gh: GitHub, candidates: list[tuple[str, int, list[str]]]) -> list[Repo]:
    repos: list[Repo] = []
    for full_name, mentions, sources in candidates:
        try:
            repo = gh.repo(full_name)
        except requests.HTTPError as e:
            log.warning("skip %s: %s", full_name, e)
            continue
        if not repo:
            continue
        repo.mention_count = mentions
        repo.mentioned_by = sources
        repos.append(repo)
    return repos
