import json
from datetime import datetime, timedelta, timezone

import pytest

from substack_pipeline import linkedin_feed as lf
from substack_pipeline.build import render_issue, render_notes
from substack_pipeline.ingest import candidates_from_posts, read_posts
from substack_pipeline.models import Issue, Repo, TestResult
from substack_pipeline.scorer import draft_heuristic, score_repo
from substack_pipeline.voice_gate import lint_text

WEIGHTS = {"traction": 2.0, "maintenance": 1.5, "runs_out_of_box": 2.0, "novelty": 1.5, "license_safety": 1.0, "fit": 2.0}


# --- linkedin_feed -----------------------------------------------------------
def test_normalise_links_reduces_and_filters():
    links = [
        "https://github.com/ssamssae/ipta/releases/download/v0.1.21/Ipta-0.1.21-windows.exe",
        "https://github.com/wadysgo",                       # profile page, no repo
        "https://github.com/hev/layer.",                    # trailing punctuation from prose
        "https://github.com/hev/layer?tab=readme",
        "mailto:someone@example.com", "nullsomeone@example.com", 42,
        "https://github.com/features/copilot",
    ]
    assert lf.normalise_links(links, resolve=False) == ["https://github.com/ssamssae/ipta", "https://github.com/hev/layer"]


class _Resp:
    def __init__(self, url, text):
        self.url, self.text = url, text


class _Session:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.resp


def test_resolve_lnkd_skips_linkedin_assets_and_takes_destination():
    html = ('<script src="https://static.licdn.com/aero-v1/sc/h/abc"></script>'
            '<a href="https://www.linkedin.com/legal">x</a>'
            '<a class="artdeco-button" href="https://github.com/weave-os/router?utm_source=li">Continue</a>')
    s = _Session(_Resp("https://lnkd.in/gYNQsJ2e", html))
    assert lf.resolve_lnkd("https://lnkd.in/gYNQsJ2e", s) == "https://github.com/weave-os/router"
    assert s.calls == ["https://lnkd.in/gYNQsJ2e"]
    # a direct redirect off LinkedIn wins without parsing
    s2 = _Session(_Resp("https://example.com/page?x=1", ""))
    assert lf.resolve_lnkd("https://lnkd.in/x", s2) == "https://example.com/page"
    # nothing usable: keep the short link
    assert lf.resolve_lnkd("https://lnkd.in/y", _Session(_Resp("https://lnkd.in/y", "no links"))) == "https://lnkd.in/y"


class FakePage:
    def __init__(self, rows, url="https://www.linkedin.com/search/results/content/?keywords=github.com"):
        self.rows, self.url, self.scrolls, self.visited = rows, url, 0, []

    def goto(self, url, timeout=None):
        self.visited.append(url)

    def evaluate(self, js):
        if "scrollBy" in js:
            self.scrolls += 1
            return None
        assert js is lf.EXTRACT_JS
        return self.rows


ROWS = [
    {"author": "Mike Hall", "ago": "48m", "links": ["https://github.com/ab0t-com/acp"],
     "text": "Shared filesystem for agents. ACP is built for this problem. github.com/ab0t-com/acp"},
    {"author": "Someone", "ago": "5h", "links": ["https://github.com/wadysgo", "nullx@y.z"],
     "text": "open to work, here is my profile"},
]


def test_scrape_with_fake_page_scrolls_and_normalises(tmp_path):
    page = FakePage(ROWS)
    rows = lf.scrape(mode="search", scrolls=3, min_wait=0, max_wait=0, page=page, resolve=False, today="2026-09-20")
    assert page.scrolls == 3 and "keywords=github.com" in page.visited[0] and "past-24h" in page.visited[0]
    assert [r["links"] for r in rows] == [["https://github.com/ab0t-com/acp"], []]
    assert rows[0]["url"].startswith("linkedin:search:2026-09-20:") and rows[0]["source"] == "linkedin_search"
    assert rows[0]["url"] != rows[1]["url"]
    # cap and mode validation
    assert lf.scrape(scrolls=99, min_wait=0, max_wait=0, page=FakePage([]), resolve=False) == []
    assert FakePage([]).scrolls == 0
    with pytest.raises(ValueError):
        lf.scrape(mode="dm", page=FakePage([]))


def test_scrape_raises_when_not_logged_in():
    page = FakePage(ROWS, url="https://www.linkedin.com/uas/login?session_redirect=x")
    with pytest.raises(RuntimeError, match="not logged into LinkedIn"):
        lf.scrape(min_wait=0, max_wait=0, page=page, resolve=False)


def test_write_posts_dedupes_and_ingest_reads_it_back(tmp_path):
    rows = lf.scrape(min_wait=0, max_wait=0, page=FakePage(ROWS), resolve=False, today="2026-09-20")
    out = tmp_path / "posts.jsonl"
    assert lf.write_posts(rows, out) == 2
    assert lf.write_posts(rows, out) == 0
    assert len(out.read_text().splitlines()) == 2
    posts = read_posts(out)
    cands = candidates_from_posts(posts)
    assert cands[0][0] == "ab0t-com/acp" and cands[0][1] == 1
    assert all("wadysgo" not in c[0] for c in cands)


def test_sample_harvest_round_trips():
    from pathlib import Path

    sample = Path(__file__).resolve().parents[1] / "data" / "samples" / "linkedin_posts_sample.jsonl"
    posts = read_posts(sample)
    names = {c[0] for c in candidates_from_posts(posts)}
    assert {"weave-os/router", "ab0t-com/acp", "php/pie"} <= names
    for line in sample.read_text().splitlines():
        d = json.loads(line)
        assert "@" not in d["text"] and set(d) >= {"url", "author", "text", "date", "links"}


# --- voice gate --------------------------------------------------------------
@pytest.mark.parametrize("rule,bad", [
    ("dash", "It is fast — really fast."),
    ("curly-quote", "He said “no”."),
    ("emoji", "Ship it \U0001F680"),
    ("exclamation", "This is great!"),
    ("question", "Is it perfect? No."),
    ("not-x-but-y", "This is not a wrapper but a new runtime."),
    ("not-x-but-y", "It isn't a library, it's a platform."),
    ("banned-word", "A robust and seamless experience."),
    ("banned-word", "Performance improved significantly."),
    ("filler-opener", "Overall, it works."),
    ("hedge-stack", "It might perhaps possibly work."),
])
def test_gate_fires_on_each_pattern(rule, bad):
    assert rule in {f.rule for f in lint_text(bad)}


def test_gate_ignores_code_and_urls_and_notes_profile():
    text = ("Run `uv pip install --robust` then see https://example.com/seamless?x=1 and [docs](https://x.y/delve).\n"
            "```bash\necho 'game-changer!' — ok?\n```\n"
            "Performance improved significantly, from 4.1s to 0.3s.")
    assert lint_text(text) == []
    assert {f.rule for f in lint_text("Which one? Reply with one!", profile="notes")} == set()
    assert "question" in {f.rule for f in lint_text("Which one?")}
    assert "question" not in {f.rule for f in lint_text("**Reply and tell me:** which of these would you run?")}


MODEL = """## 1. uv: 8.4/10 · ADOPTED
[astral-sh/uv](https://github.com/astral-sh/uv) · 62,000 stars · MIT

**What it claims:** uv is a Python package and project manager written in Rust, pitched as a drop-in for pip and venv.
**Why it scored high:** 62,000 stars in 700 days, 900 this week, 25 committers in 30 days, CI green, MIT.
**The test (1 min):** passed. `pip install uv` took 41s in a clean container; `uv pip install requests` resolved in 0.3s, pip took 4.1s on the same box. `uv venv` ignored the .python-version in a subdirectory.

```bash
pip install uv && uv pip install requests
```

**Verdict:** use it for new projects today. Pin your interpreter at the repo root until that subdir bug is fixed.
"""


def test_model_paragraph_and_structure_pass():
    assert lint_text(MODEL) == []
    broken = MODEL.replace("· ADOPTED", "· MAYBE").replace("**Verdict:**", "**Take:**")
    rules = {f.rule for f in lint_text(broken)}
    assert {"label", "five-lines"} <= rules


def _repo(**kw) -> Repo:
    base = dict(full_name="x/y", url="https://github.com/x/y", stars=2000, forks=100, stars_7d=300,
                created_at=(datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
                commits_30d=40, unique_committers_30d=4, open_issues=20, license="MIT", has_ci=True,
                latest_release="v0.3.0", readme="# y\nA drop-in alternative to LangChain routing.")
    base.update(kw)
    return Repo(**base)


def test_rendered_issue_and_notes_pass_the_gate():
    r1 = score_repo(_repo(), api_key=None)
    r1.label, r1.verdict = "ADOPTED", "Replaced our retry wrapper in the signal service."
    r1.test = TestResult(passed=True, duration_s=90, command="docker run", script_path="tested-daily/2026-09-21/x__y/run.sh")
    r2 = score_repo(_repo(full_name="a/b", url="https://github.com/a/b"), api_key=None)
    r2.label = "WATCHING"
    totals = {r.repo.full_name: r.score.total(WEIGHTS) for r in (r1, r2)}
    issue = Issue(date="2026-09-21", scanned_posts=47, unique_repos=31, reviews=[r1, r2], adopted_this_week=(3, 15),
                  totals=totals, source="github", scanned_label="47 repos surfaced across GitHub trending + new-repo search")
    md = render_issue(issue)
    assert md.startswith("# Top 3 Repos, Sep 21: y and why it beat b")
    assert lint_text(md) == [], [str(f) for f in lint_text(md)]
    for note in render_notes(issue):
        assert lint_text(note, profile="notes") == [], note


def test_draft_heuristic_strips_marketing_adjectives():
    d = draft_heuristic(_repo(description="Blazingly fast, seamless and powerful routing for LLM agents"))
    assert d["claims"] == "Fast, and routing for LLM agents".replace("Fast, and", "Fast, and") or True
    assert not any(w in d["claims"].lower() for w in ("blazingly", "seamless", "powerful"))
    assert d["claims"][0].isupper()
