"""LinkedIn discovery (optional): read the owner's OWN logged-in LinkedIn session over the Chrome
DevTools Protocol and turn posts that link to GitHub repos into the JSONL that `--source posts` /
`--source both` ingest.

Modes:
  search  (default)  LinkedIn content search for a keyword ("github.com"), newest first, past 24 h.
                     This is where the repo posts are: ~25 posts per 8 scrolls, half with a repo.
  feed               the home feed. Far fewer repo posts; kept for completeness.

This module never touches cookies, passwords, or the Chrome profile. It attaches to a Chrome the owner
launched with remote debugging (control-tower: linkedin/launch_chrome_cdp.sh) and, if that Chrome is not
logged in, stops with an error asking the owner to log in once in that window.

LinkedIn's User Agreement prohibits automated access. This reads only the owner's own session, at a human
cadence (random 2-5 s between scrolls, at most 30 scrolls, one run per invocation). The owner accepted that
risk on 2026-09-20; GitHub trending is the default source and needs none of this.
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from .ingest import NOT_REPOS

log = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
SEARCH_URL = ("https://www.linkedin.com/search/results/content/?keywords={query}"
              "&datePosted=%22{date_posted}%22&sortBy=%22date_posted%22")
FEED_URL = "https://www.linkedin.com/feed/"
LAUNCH_SCRIPT = "algochains-control-tower/linkedin/launch_chrome_cdp.sh"
MAX_SCROLLS = 30
SCROLL_PX = 1400
SKIP_HOSTS = ("linkedin.com", "licdn.com", "lnkd.in")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0 Safari/537.36"
GITHUB_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
GITHUB_ANY = re.compile(r"^https?://(?:www\.)?github\.com/", re.I)

LOGIN_MSG = ("The CDP Chrome is not logged into LinkedIn. Log in once in that Chrome window (the profile "
             f"launched by {LAUNCH_SCRIPT}); the session persists after that. No credentials are handled here.")

# LinkedIn (2026) ships hashed class names and no data-urn attributes, so posts are found structurally:
# every "Like" button belongs to exactly one post. Walk up to the smallest ancestor that has an actor link,
# exactly one Like button and real text, then keep climbing while the parent DIV still holds only that post.
# Links come from four places: anchors, URLs in the text, GitHub link-card titles ("GitHub - owner/repo")
# and bare "github.com/owner/repo" mentions. lnkd.in short links are resolved later in Python.
EXTRACT_JS = r"""
() => {
  const isLike = b => /^\s*Like\s*$/.test(b.innerText || '');
  const likeCount = n => [...n.querySelectorAll('button')].filter(isLike).length;
  const container = btn => {
    let n = btn;
    for (let i = 0; i < 14 && n; i++) {
      n = n.parentElement;
      if (!n) break;
      const actor = n.querySelector('a[href*="/in/"], a[href*="/company/"]');
      if (actor && likeCount(n) === 1 && (n.innerText || '').length > 80) {
        let m = n;
        while (m.parentElement && m.parentElement.tagName === 'DIV' && likeCount(m.parentElement) === 1) m = m.parentElement;
        return m;
      }
    }
    return null;
  };
  const clean = h => { try { const u = new URL(h); return u.origin + u.pathname; } catch (e) { return String(h).split('?')[0]; } };
  const seen = new Set();
  const out = [];
  for (const btn of [...document.querySelectorAll('button')].filter(isLike)) {
    const c = container(btn);
    if (!c || seen.has(c)) continue;
    seen.add(c);
    const text = (c.innerText || '').replace(/^Feed post\s*/, '').replace(/\s+/g, ' ').trim();
    const actor = c.querySelector('a[href*="/in/"], a[href*="/company/"]');
    const author = ((actor && actor.innerText) || '').trim().split('\n')[0].slice(0, 60);
    const ago = (text.match(/\b(\d+)(h|d|w|mo)\s*•/) || [''])[0].replace(/\s*•/, '');
    const hrefs = [...c.querySelectorAll('a[href]')].map(a => clean(a.href)).filter(h => /^https?:/.test(h) && !/linkedin\.com/.test(h));
    const inText = (text.match(/https?:\/\/[^\s"'<>)\]]+/g) || []).map(clean);
    const cards = [...text.matchAll(/GitHub - ([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)/g)].map(m => 'https://github.com/' + m[1]);
    const bare = [...text.matchAll(/(?:^|\s)github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)/g)].map(m => 'https://github.com/' + m[1]);
    out.push({ author, ago, links: [...new Set([...hrefs, ...inText, ...cards, ...bare])], text: text.slice(0, 2000) });
  }
  return out;
}
"""


# --- pure helpers -----------------------------------------------------------
def clean_url(u: str) -> str:
    """origin + path only: query strings and fragments carry tracking, never the repo."""
    if not isinstance(u, str) or not re.match(r"^https?://", u):
        return u
    p = urlsplit(u)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def resolve_lnkd(url: str, session=None, timeout: int = 20) -> str:
    """Follow a lnkd.in short link. LinkedIn answers with an interstitial page; the destination is the
    first http(s) URL on it that is not LinkedIn's own (a naive `url=` regex grabs a static asset)."""
    s = session or requests.Session()
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": UA})
    except requests.RequestException as e:
        log.warning("lnkd.in resolve failed for %s: %s", url, e)
        return url
    final = getattr(r, "url", "") or ""
    if final and not any(h in final for h in SKIP_HOSTS):
        return clean_url(final)
    for m in re.finditer(r"https?://[^\s\"'<>)\]]+", getattr(r, "text", "") or ""):
        cand = m.group(0)
        if not any(h in cand for h in SKIP_HOSTS):
            return clean_url(cand)
    return url


def normalise_links(links, session=None, resolve: bool = True) -> list[str]:
    """Canonical, deduped links: lnkd.in resolved, GitHub release/blob/tree URLs reduced to the repo,
    GitHub profile pages (no repo path) dropped, mailto/junk dropped, trailing punctuation stripped."""
    out: list[str] = []
    for raw in links or []:
        if not isinstance(raw, str):
            continue
        u = raw.strip().rstrip(".,);]")
        if not re.match(r"^https?://", u):
            continue
        if resolve and "lnkd.in/" in u:
            u = resolve_lnkd(u, session)
        m = GITHUB_REPO.match(u)
        if m:
            owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2))
            if owner.lower() in NOT_REPOS:
                continue
            u = f"https://github.com/{owner}/{repo}"
        elif GITHUB_ANY.match(u):
            continue  # a user or org page, not a repo
        if u.lower() not in {o.lower() for o in out}:
            out.append(u)
    return out


def post_key(author: str, text: str) -> str:
    """Stable dedupe key: LinkedIn's DOM does not expose permalinks, so hash author + first 200 chars."""
    return hashlib.sha1(f"{author}|{(text or '')[:200]}".encode("utf-8")).hexdigest()


def _row(raw: dict, mode: str, day: str, session=None, resolve: bool = True) -> dict:
    author = (raw.get("author") or "").strip()
    text = (raw.get("text") or "").strip()
    return {
        "url": f"linkedin:{mode}:{day}:{post_key(author, text)[:12]}",
        "author": author,
        "ago": raw.get("ago") or "",
        "text": text,
        "links": normalise_links(raw.get("links"), session, resolve),
        "date": day,
        "source": f"linkedin_{mode}",
    }


# --- scraping ----------------------------------------------------------------
def _scrape_page(page, url: str, scrolls: int, min_wait: float, max_wait: float, mode: str,
                 resolve: bool, day: str, session=None) -> list[dict]:
    page.goto(url, timeout=60000)
    time.sleep(random.uniform(min_wait, max_wait))
    if "/login" in (page.url or "") or "authwall" in (page.url or ""):
        raise RuntimeError(LOGIN_MSG)
    for _ in range(scrolls):
        page.evaluate(f"window.scrollBy(0, {SCROLL_PX})")
        time.sleep(random.uniform(min_wait, max_wait))
    raw = page.evaluate(EXTRACT_JS) or []
    return [_row(r, mode, day, session, resolve) for r in raw]


def _with_cdp(cdp_url: str, fn):
    from playwright.sync_api import sync_playwright  # lazy: optional dependency

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=15000)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"No Chrome with remote debugging at {cdp_url}. Launch it with {LAUNCH_SCRIPT} "
                               "(or set LINKEDIN_CDP_URL).") from e
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = next((pg for c in browser.contexts for pg in c.pages if "linkedin.com" in (pg.url or "")), None)
            page = page or ctx.new_page()
            return fn(page)
        finally:
            browser.close()  # disconnects from the CDP Chrome; does not quit it


def scrape(mode: str = "search", query: str = "github.com", date_posted: str = "past-24h", scrolls: int = 8,
           min_wait: float = 2.0, max_wait: float = 5.0, cdp_url: str | None = None, page=None,
           resolve: bool = True, today: str | None = None) -> list[dict]:
    """One human-paced pass over the search results (or the feed). `page` lets tests pass a fake."""
    if mode not in ("search", "feed"):
        raise ValueError(f"mode must be search or feed, got {mode!r}")
    scrolls = max(0, min(int(scrolls), MAX_SCROLLS))
    if scrolls > 15:
        log.warning("keep the LinkedIn cadence human: %d scrolls requested", scrolls)
    url = SEARCH_URL.format(query=quote(query), date_posted=date_posted) if mode == "search" else FEED_URL
    day = today or date.today().isoformat()
    session = requests.Session() if resolve else None
    if page is not None:
        return _scrape_page(page, url, scrolls, min_wait, max_wait, mode, resolve, day, session)
    cdp = cdp_url or os.getenv("LINKEDIN_CDP_URL", "").strip() or DEFAULT_CDP_URL
    return _with_cdp(cdp, lambda pg: _scrape_page(pg, url, scrolls, min_wait, max_wait, mode, resolve, day, session))


def write_posts(rows: list[dict], path: Path) -> int:
    """Append rows to the JSONL, skipping urls already present. Returns the number written."""
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line).get("url", ""))
            except (json.JSONDecodeError, AttributeError):
                continue
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            if r.get("url") in seen:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            seen.add(r.get("url", ""))
            n += 1
    return n
