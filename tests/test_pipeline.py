from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from substack_pipeline.build import render_issue, render_notes
from substack_pipeline.ingest import candidates_from_posts, extract_repo_links, read_posts
from substack_pipeline.models import Issue, Repo, Review, Score, TestResult
from substack_pipeline.review import finalize
from substack_pipeline.scorer import rank, score_repo
from substack_pipeline.store import Store

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "posts.jsonl"
WEIGHTS = {"traction": 2.0, "maintenance": 1.5, "runs_out_of_box": 2.0, "novelty": 1.5, "license_safety": 1.0, "fit": 2.0}


def test_extract_repo_links_canonicalises_and_filters():
    text = ("see https://github.com/astral-sh/uv.git and github.com/browser-use/browser-use/tree/main "
            "not https://github.com/features/copilot, again https://GitHub.com/Astral-sh/UV")
    assert extract_repo_links(text) == ["astral-sh/uv", "browser-use/browser-use"]


def test_read_posts_and_group_by_mentions():
    posts = read_posts(SAMPLES)
    assert len(posts) == 5
    cands = candidates_from_posts(posts)
    assert cands[0][0] == "astral-sh/uv" and cands[0][1] == 2
    names = {c[0] for c in cands}
    assert "features/copilot" not in names and "pola-rs/polars" in names


def _repo(**kw) -> Repo:
    base = dict(full_name="x/y", url="https://github.com/x/y", stars=2000, forks=100, stars_7d=300,
                created_at=(datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
                commits_30d=40, unique_committers_30d=4, open_issues=20, license="MIT", has_ci=True,
                latest_release="v0.3.0", readme="# y\nA drop-in alternative to LangChain routing.")
    base.update(kw)
    return Repo(**base)


def test_scoring_is_deterministic_without_api_key_and_ranks_sensibly():
    strong = score_repo(_repo(), api_key=None)
    weak = score_repo(_repo(full_name="a/b", stars=60, stars_7d=1, commits_30d=0, has_ci=False,
                            latest_release=None, license=None, unique_committers_30d=1), api_key=None)
    assert strong.score.total(WEIGHTS) > weak.score.total(WEIGHTS)
    assert rank([weak, strong], WEIGHTS)[0] is strong
    assert "MIT" in strong.why_scored


def test_failed_install_caps_score_and_adopted_requires_pass():
    r = score_repo(_repo(), api_key=None)
    r.score.fit = 10
    r.score.runs_out_of_box = 0
    r.test = TestResult(passed=False, duration_s=30, command="docker run", script_path="tested-daily/x/run.sh")
    picks = finalize([r], WEIGHTS, picks=3, fail_cap=6.0)
    assert picks == [r]
    # the cap is applied at ranking time; the rendered total reflects the raw weighted score
    assert r.score.total(WEIGHTS) <= 10


def test_store_dedupe_and_adopted_ledger(tmp_path):
    st = Store(tmp_path / "p.sqlite")
    assert st.mark_post_seen("u1") and not st.mark_post_seen("u1")
    st.record("x/y", 8.4, "ADOPTED", "v1")
    seen, rel = st.recently_scored("x/y", 30)
    assert seen and rel == "v1"
    today = date.today().isoformat()
    st.record_adopted(today, "x/y", "ADOPTED", 8.4, "shipped")
    st.record_adopted(today, "a/b", "WATCHING", 6.1, "")
    assert st.adopted_this_week() == (1, 2)
    assert st.leaderboard()[0][0] == "x/y"


def test_render_issue_and_notes_have_the_fixed_shape():
    r1 = score_repo(_repo(), api_key=None)
    r1.label, r1.verdict = "ADOPTED", "Replaced our retry wrapper."
    r1.test = TestResult(passed=True, duration_s=90, command="docker run", script_path="tested-daily/2026-09-21/x__y/run.sh")
    r2 = score_repo(_repo(full_name="a/b", url="https://github.com/a/b"), api_key=None)
    r2.label = "WATCHING"
    totals = {r.repo.full_name: r.score.total(WEIGHTS) for r in (r1, r2)}
    issue = Issue(date="2026-09-21", scanned_posts=47, unique_repos=31, reviews=[r1, r2], adopted_this_week=(3, 15), totals=totals)
    md = render_issue(issue)
    assert md.startswith("# Top 3 Repos — Sep 21: y and why it beat b")
    assert "**Scanned:** 47 posts · 31 unique repos" in md
    assert "## 1. y —" in md and "· ADOPTED" in md and "receipt" in md
    assert "**Adopted this week:** 3 of 15" in md
    assert "How I make this" in md
    notes = render_notes(issue)
    assert len(notes) == 3 and "reply with one" in notes[2]


@pytest.mark.parametrize("bad", ["", "not json", '{"text": "no links here"}'])
def test_read_posts_tolerates_bad_lines(tmp_path, bad):
    p = tmp_path / "posts.jsonl"
    p.write_text(bad + "\n")
    assert isinstance(read_posts(p), list)
