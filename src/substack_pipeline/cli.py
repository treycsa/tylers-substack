"""`substack` command: the daily loop, and each stage on its own.

    substack scan      # GitHub trending + search -> candidates.json (cron, 11 PM); --source posts for a JSONL
    substack enrich    # candidates.json -> repos.json           (cron)
    substack score     # repos.json -> scored.json               (cron)
    substack test      # top N scored -> tested-daily/<date>/    (6:00 AM, can be cron too)
    substack review    # Tyler: fit, verdict, label -> picks.json
    substack build     # picks.json -> out/<date>-issue.md + notes
    substack publish   # manual (clipboard); --api [--schedule | --at T] [--section S] (cookie auth); or --browser
    substack daily     # all of the above; --auto skips the human step (WATCHING only)
    substack leaderboard --days 30
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import build as build_mod
from . import publish as publish_mod
from .config import Settings, load_settings
from .discover import candidates_from_sightings, collect_sightings
from .github_enrich import GitHub, enrich
from .ingest import candidates_from_posts, read_posts
from .models import Issue, Repo, Review, Score, TestResult
from .review import review_auto, review_interactive
from .scorer import rank, score_repo
from .store import Store
from .tester import run_test, runs_out_of_box_score

log = logging.getLogger("substack")


# --- (de)serialisation helpers -------------------------------------------
def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=lambda o: dataclasses.asdict(o) if dataclasses.is_dataclass(o) else str(o)))


def _load(path: Path):
    return json.loads(path.read_text())


def _review_from_dict(d: dict) -> Review:
    repo = Repo(**{k: v for k, v in d["repo"].items() if k in Repo.__dataclass_fields__})
    score = Score(**{k: v for k, v in d["score"].items() if k in Score.__dataclass_fields__})
    test = TestResult(**d["test"]) if d.get("test") else None
    return Review(repo=repo, score=score, claims=d.get("claims", ""), why_scored=d.get("why_scored", ""),
                  test_summary=d.get("test_summary", ""), test_snippet=d.get("test_snippet", ""),
                  verdict=d.get("verdict", ""), label=d.get("label", "WATCHING"), test=test)


def _work(s: Settings, day: str) -> Path:
    return s.out_dir / "work" / day


CARRIED = ("scanned_posts", "unique_repos", "source", "scanned_label")


def _carry(d: dict) -> dict:
    """The header facts every stage forwards; old work files (pre-`source`) default to the posts wording."""
    scanned = d.get("scanned_posts", 0)
    return {"scanned_posts": scanned, "unique_repos": d.get("unique_repos", 0),
            "source": d.get("source", "posts"), "scanned_label": d.get("scanned_label") or f"{scanned} posts"}


# --- stages ---------------------------------------------------------------
def _merge(a: list[tuple[str, int, list[str]]], b: list[tuple[str, int, list[str]]]) -> list[tuple[str, int, list[str]]]:
    """Union two candidate lists (case-insensitive): sum mentions, concat sources, mentions desc; ties keep
    first-seen order so the feeds' trending rank / the search's star order survives into enrich."""
    canon: dict[str, str] = {}
    n: dict[str, int] = {}
    srcs: dict[str, list[str]] = {}
    for full, count, sources in a + b:
        key = full.lower()
        canon.setdefault(key, full)
        n[key] = n.get(key, 0) + count
        srcs.setdefault(key, []).extend(sources)
    return sorted(((canon[k], n[k], srcs[k]) for k in n), key=lambda c: -c[1])  # stable: ties keep order


def cmd_scan(s: Settings, day: str, source: str = "github") -> int:
    store = Store(s.db_path)
    cands: list[tuple[str, int, list[str]]] = []
    scanned, labels = 0, []
    if source in ("github", "both"):
        cfg = s.discover
        sightings = collect_sightings(cfg["feeds"], GitHub(s.github_token), cfg["search"]["days_back"],
                                      cfg["search"]["min_stars"], cfg["search"]["languages"] or None)
        cands = _merge(cands, candidates_from_sightings(sightings))
        scanned += len(sightings)
        labels.append(f"{len(sightings)} repos surfaced across GitHub trending + new-repo search")
    if source in ("posts", "both"):
        posts = read_posts(s.posts_path)
        new_posts = [p for p in posts if not p.url or store.mark_post_seen(p.url)]
        cands = _merge(cands, candidates_from_posts(new_posts))
        scanned += len(new_posts)
        labels.append(f"{len(new_posts)} posts")
    win = s.selection["dedupe_window_days"]
    kept = []
    for full, n, srcs in cands:
        seen, _ = store.recently_scored(full, win)
        if seen:
            log.info("skip %s: scored in last %sd", full, win)
            continue
        kept.append({"full_name": full, "mentions": n, "sources": srcs})
    label = " + ".join(labels)
    _dump(_work(s, day) / "candidates.json",
          {"scanned_posts": scanned, "source": source, "scanned_label": label, "candidates": kept})
    print(f"scan [{source}]: {label}, {len(cands)} repos, {len(kept)} candidates after dedupe")
    return 0


def cmd_enrich(s: Settings, day: str) -> int:
    c = _load(_work(s, day) / "candidates.json")
    gh = GitHub(s.github_token)
    top = c["candidates"][: s.selection["candidates_to_score"] * 2]  # some 404 or are forks
    repos = enrich(gh, [(x["full_name"], x["mentions"], x["sources"]) for x in top])
    _dump(_work(s, day) / "repos.json", {**_carry({**c, "unique_repos": len(c["candidates"])}), "repos": repos})
    print(f"enrich: {len(repos)} repos enriched")
    return 0


def cmd_score(s: Settings, day: str) -> int:
    d = _load(_work(s, day) / "repos.json")
    repos = [Repo(**{k: v for k, v in r.items() if k in Repo.__dataclass_fields__}) for r in d["repos"]]
    min_stars = s.selection["min_stars_without_override"]
    reviews = [score_repo(r, s.anthropic_key, s.anthropic_model) for r in repos if r.stars >= min_stars]
    ranked = rank(reviews, s.weights)[: s.selection["candidates_to_score"]]
    _dump(_work(s, day) / "scored.json", {**_carry(d), "reviews": ranked})
    for r in ranked:
        print(f"  {r.score.total(s.weights):>4}  {r.repo.full_name}  ({r.repo.stars:,}★)")
    return 0


def cmd_test(s: Settings, day: str) -> int:
    d = _load(_work(s, day) / "scored.json")
    reviews = [_review_from_dict(x) for x in d["reviews"]][: s.selection["candidates_to_test"]]
    day_dir = s.tested_dir / day
    for r in reviews:
        res = run_test(r.repo, day_dir, s.rubric["test"]["docker_image"], s.rubric["test"]["timeout_seconds"])
        r.test = res
        r.score.runs_out_of_box = runs_out_of_box_score(res) if res.command != "(docker unavailable)" else None
        print(f"  {'PASS' if res.passed else 'FAIL'} {res.duration_s:>6}s  {r.repo.full_name}  {res.notes}")
    _dump(_work(s, day) / "tested.json", {**_carry(d), "reviews": reviews})
    return 0


def cmd_review(s: Settings, day: str, auto: bool) -> int:
    d = _load(_work(s, day) / "tested.json")
    reviews = [_review_from_dict(x) for x in d["reviews"]]
    fn = review_auto if auto else review_interactive
    picks = fn(reviews, s.weights, s.selection["picks"], s.selection["fail_install_cap"])
    _dump(_work(s, day) / "picks.json", {**_carry(d), "reviews": picks})
    print(f"review: {len(picks)} picks -> {[r.repo.full_name for r in picks]}")
    return 0


def cmd_build(s: Settings, day: str) -> int:
    d = _load(_work(s, day) / "picks.json")
    reviews = [_review_from_dict(x) for x in d["reviews"]]
    store = Store(s.db_path)
    totals = {r.repo.full_name: r.score.total(s.weights) for r in reviews}
    for r in reviews:
        store.record(r.repo.full_name, totals[r.repo.full_name], r.label, r.repo.latest_release, day)
        store.record_adopted(day, r.repo.full_name, r.label, totals[r.repo.full_name], r.verdict)
    issue = Issue(date=day, **_carry(d), reviews=reviews,
                  adopted_this_week=store.adopted_this_week(date.fromisoformat(day)), totals=totals)
    repo_url = s.rubric["publication"].get("repo_url", build_mod.REPO_URL)
    post, notes = build_mod.write_outputs(issue, s.out_dir, repo_url)
    title, subtitle = build_mod._title(issue)
    _dump(_work(s, day) / "meta.json", {"title": title, "subtitle": subtitle, "post": str(post), "notes": str(notes)})
    print(f"build: {post}\n       {notes}")
    return 0


def _publication_url(pub: dict) -> str:
    return pub.get("url") or f"https://{pub.get('subdomain') or pub['handle']}.substack.com"


def cmd_publish(s: Settings, day: str, browser: bool = False, api: bool = False, schedule: bool = False,
                at: str | None = None, section: str | None = None) -> int:
    """--api creates a draft; --schedule / --at also schedule it (never publish). Else --browser or manual."""
    meta = _load(_work(s, day) / "meta.json")
    pub = s.rubric["publication"]
    handle = pub.get("subdomain") or pub["handle"]
    if api or schedule or at:
        tz = ZoneInfo(pub["timezone"])
        now = datetime.now(tz)
        when = None
        if at:
            try:
                when = datetime.strptime(at, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
            except ValueError:
                print(f"--at must be YYYY-MM-DDTHH:MM (local time in {pub['timezone']}), got {at!r}.", file=sys.stderr)
                return 1
            if when <= now:
                print(f"--at {at} is in the past ({pub['timezone']}); refusing to schedule.", file=sys.stderr)
                return 1
        elif schedule:
            when = publish_mod.next_publish_time(now, int(pub["publish_hour_local"]), pub["timezone"])
        try:
            print(publish_mod.publish_api(Path(meta["post"]), meta["title"], meta["subtitle"], _publication_url(pub),
                                          cookies_string=s.substack_cookies, cookies_path=s.substack_cookies_path,
                                          section_name=section if section is not None else pub.get("section"),
                                          schedule_at=when))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
    elif browser:
        print(publish_mod.publish_browser(Path(meta["post"]), handle, meta["title"], meta["subtitle"]))
    else:
        print(publish_mod.publish_manual(Path(meta["post"]), handle))
    return 0


def cmd_leaderboard(s: Settings, days: int) -> int:
    for name, total, label in Store(s.db_path).leaderboard(days):
        print(f"  {total:>4}  {label or '':<20} {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="substack", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=date.today().isoformat(), help="issue date, YYYY-MM-DD")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    source_kw = dict(choices=("github", "posts", "both"), default="github",
                     help="github = trending feeds + new-repo search (default); posts = legacy POSTS_PATH JSONL; both = merge")
    sub.add_parser("scan").add_argument("--source", **source_kw)
    for name in ("enrich", "score", "test", "build"):
        sub.add_parser(name)
    sub.add_parser("review").add_argument("--auto", action="store_true", help="no human step: WATCHING only")
    p = sub.add_parser("publish")
    p.add_argument("--browser", action="store_true", help="Playwright profile (run `substack login` first); stops at a draft")
    p.add_argument("--api", action="store_true",
                   help="python-substack with SUBSTACK_COOKIES/_PATH; creates a DRAFT only unless --schedule/--at")
    p.add_argument("--schedule", action="store_true",
                   help="(implies --api) schedule for the next publish_hour_local in the rubric timezone")
    p.add_argument("--at", metavar="YYYY-MM-DDTHH:MM", help="(implies --api) schedule for this local time")
    p.add_argument("--section", metavar="NAME", help="Substack section (default: rubric publication.section)")
    sub.add_parser("login")
    d = sub.add_parser("daily")
    d.add_argument("--auto", action="store_true")
    d.add_argument("--publish", action="store_true")
    d.add_argument("--api", action="store_true", help="with --publish: draft via the API instead of the clipboard")
    d.add_argument("--source", **source_kw)
    sub.add_parser("leaderboard").add_argument("--days", type=int, default=30)

    for sp in sub.choices.values():  # accept `-v` after the subcommand too
        sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    s = load_settings()
    day = a.date

    if a.cmd == "scan":
        return cmd_scan(s, day, a.source)
    if a.cmd == "enrich":
        return cmd_enrich(s, day)
    if a.cmd == "score":
        return cmd_score(s, day)
    if a.cmd == "test":
        return cmd_test(s, day)
    if a.cmd == "review":
        return cmd_review(s, day, a.auto)
    if a.cmd == "build":
        return cmd_build(s, day)
    if a.cmd == "publish":
        return cmd_publish(s, day, a.browser, api=a.api, schedule=a.schedule, at=a.at, section=a.section)
    if a.cmd == "login":
        publish_mod.login(s.rubric["publication"]["handle"])
        return 0
    if a.cmd == "leaderboard":
        return cmd_leaderboard(s, a.days)
    if a.cmd == "daily":
        if cmd_scan(s, day, a.source):
            return 1
        for fn in (cmd_enrich, cmd_score, cmd_test):
            if fn(s, day):
                return 1
        cmd_review(s, day, a.auto)
        cmd_build(s, day)
        if a.publish:
            cmd_publish(s, day, api=a.api)  # manual (clipboard) unless --api; a draft, never scheduled
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
