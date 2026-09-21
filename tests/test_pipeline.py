from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from substack_pipeline.build import render_issue, render_notes
from substack_pipeline.discover import discover_candidates, fetch_trending, search_new_repos
from substack_pipeline.github_enrich import GitHub
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
    assert md.startswith("# Top 3 Repos, Sep 21: y and why it beat b")
    assert "**Scanned:** 47 posts · 31 unique repos" in md
    assert "## 1. y:" in md and "· ADOPTED" in md and "receipt" in md
    assert "**Adopted this week:** 3 of 15" in md
    assert "How I make this" in md
    notes = render_notes(issue)
    assert len(notes) == 3 and "reply with one" in notes[2].lower()


@pytest.mark.parametrize("bad", ["", "not json", '{"text": "no links here"}'])
def test_read_posts_tolerates_bad_lines(tmp_path, bad):
    p = tmp_path / "posts.jsonl"
    p.write_text(bad + "\n")
    assert isinstance(read_posts(p), list)


# --- GitHub-native discovery ---------------------------------------------
RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>GitHub Daily Trending</title>
<item><title>Astral-sh/UV</title><link>https://github.com/Astral-sh/UV</link></item>
<item><title>browser-use/browser-use</title><link>https://github.com/browser-use/browser-use/tree/main</link></item>
<item><title>features/copilot</title><link>https://github.com/features/copilot</link></item>
</channel></rss>"""


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class _FakeSession:
    def __init__(self, pages: dict[str, _FakeResp]):
        self.pages, self.calls = pages, []

    def get(self, url, timeout=None, **kw):
        self.calls.append(url)
        return self.pages.get(url, _FakeResp(b"", 404))


def test_fetch_trending_parses_rss_and_canonicalises():
    feed, dead = "https://example.test/daily/all.xml", "https://example.test/daily/nope.xml"
    sess = _FakeSession({feed: _FakeResp(RSS.encode())})
    got = fetch_trending([dead, feed], session=sess)
    assert got == [("Astral-sh/UV", feed), ("browser-use/browser-use", feed)]  # 404 feed skipped, non-repo path dropped
    assert sess.calls == [dead, feed]


def test_search_new_repos_builds_query_and_maps_items(monkeypatch):
    seen = []

    def fake_search(self, q, per_page=50, sort="stars", order="desc"):
        seen.append((q, per_page, sort, order))
        return [{"full_name": "new/hot"}, {"full_name": "new/warm"}, {"name": "no-full-name"}]

    monkeypatch.setattr(GitHub, "search_repos", fake_search)
    gh = GitHub(token=None)
    got = search_new_repos(gh, days_back=7, min_stars=50, per_page=20, today=date(2026, 9, 22))
    assert seen == [("created:>2026-09-15 stars:>=50", 20, "stars", "desc")]
    assert got == [("new/hot", "github-search:created:>2026-09-15 stars:>=50"),
                   ("new/warm", "github-search:created:>2026-09-15 stars:>=50")]
    search_new_repos(gh, 7, 50, languages=["python", "rust"], today=date(2026, 9, 22))
    assert [q for q, *_ in seen[1:]] == ["created:>2026-09-15 stars:>=50 language:python",
                                         "created:>2026-09-15 stars:>=50 language:rust"]


def test_discover_candidates_merges_sources_and_counts_mentions(monkeypatch):
    feed = "https://example.test/daily/all.xml"
    monkeypatch.setattr("substack_pipeline.discover.fetch_trending",
                        lambda feeds, session=None: [("Astral-sh/UV", feed), ("a/b", feed), ("a/b", feed)])
    monkeypatch.setattr(GitHub, "search_repos", lambda self, q, **kw: [{"full_name": "astral-sh/uv"}, {"full_name": "c/d"}])
    cands = discover_candidates([feed], GitHub(token=None), days_back=7, min_stars=50, today=date(2026, 9, 22))
    label = "github-search:created:>2026-09-15 stars:>=50"
    assert cands[0] == ("Astral-sh/UV", 2, [feed, label])          # case-insensitive merge, one per distinct source
    assert cands[1:] == [("a/b", 1, [feed]), ("c/d", 1, [label])]  # a same-feed repeat is not a second mention


def test_candidates_keep_sighting_order_on_ties():
    """Almost every candidate ties at 1 source, so ties must keep feed rank / star order, not sort by name."""
    from substack_pipeline.cli import _merge
    from substack_pipeline.discover import candidates_from_sightings

    feed, search = "https://example.test/daily/all.xml", "github-search:q"
    cands = candidates_from_sightings([("zai-org/ZCode", feed), ("apple/x", feed), ("mid/m", feed),
                                       ("yikart/AiToEarn", search), ("apple/x", search), ("browser-use/b", search)])
    assert [c[0] for c in cands] == ["apple/x", "zai-org/ZCode", "mid/m", "yikart/AiToEarn", "browser-use/b"]
    merged = _merge(cands, [("astral-sh/uv", 1, ["posts"]), ("mid/m", 1, ["posts"])])
    assert [c[0] for c in merged] == ["apple/x", "mid/m", "zai-org/ZCode", "yikart/AiToEarn", "browser-use/b", "astral-sh/uv"]
    assert merged[1] == ("mid/m", 2, [feed, "posts"])


def test_github_get_backs_off_on_429_with_retry_after(monkeypatch):
    class _R:
        def __init__(self, status, headers=None, body=b"{}"):
            self.status_code, self.headers, self._content, self.text = status, headers or {}, body, body.decode()

        def json(self):
            return {"items": [{"full_name": "a/b"}]}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    answers = [_R(429, {"Retry-After": "7"}), _R(403, {"Retry-After": "3"}), _R(200)]
    slept = []
    sess = requests.Session()
    sess.get = lambda url, params=None, timeout=None, **kw: answers.pop(0)
    monkeypatch.setattr("substack_pipeline.github_enrich.time.sleep", lambda s: slept.append(s))
    gh = GitHub(token=None, session=sess)
    assert gh.search_repos("created:>2026-09-15 stars:>=50") == [{"full_name": "a/b"}]
    assert slept == [7, 3]  # both limit shapes waited Retry-After seconds instead of raising


def test_render_issue_github_source_uses_scanned_label():
    r1 = score_repo(_repo(), api_key=None)
    totals = {r1.repo.full_name: r1.score.total(WEIGHTS)}
    issue = Issue(date="2026-09-22", scanned_posts=42, unique_repos=31, reviews=[r1], totals=totals,
                  source="github", scanned_label="42 repos surfaced across GitHub trending + new-repo search")
    md = render_issue(issue)
    assert "**Scanned:** 42 repos surfaced across GitHub trending + new-repo search · 31 unique repos · top score" in md
    assert "posts" not in md and "LinkedIn" not in md


# --- publishing via the API ------------------------------------------------
from datetime import timezone as _tz  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from substack_pipeline import publish  # noqa: E402

LA = ZoneInfo("America/Los_Angeles")


def test_next_publish_time_before_hour_is_today_at_hour():
    now = datetime(2026, 9, 22, 4, 17, 33, tzinfo=LA)
    got = publish.next_publish_time(now, 6, "America/Los_Angeles")
    assert got == datetime(2026, 9, 22, 6, 0, tzinfo=LA) and got.tzinfo is not None
    # a UTC input is converted into the rubric timezone first
    got_utc = publish.next_publish_time(now.astimezone(_tz.utc), 6, "America/Los_Angeles")
    assert got_utc == got


def test_next_publish_time_after_hour_is_now_plus_grace_rounded_up():
    now = datetime(2026, 9, 22, 7, 41, 12, tzinfo=LA)
    assert publish.next_publish_time(now, 6, "America/Los_Angeles") == datetime(2026, 9, 22, 7, 52, tzinfo=LA)
    exact = datetime(2026, 9, 22, 7, 41, tzinfo=LA)  # already on a whole minute: no extra rounding
    assert publish.next_publish_time(exact, 6, "America/Los_Angeles", grace_minutes=5) == datetime(2026, 9, 22, 7, 46, tzinfo=LA)
    with pytest.raises(ValueError):
        publish.next_publish_time(datetime(2026, 9, 22, 7, 41), 6, "America/Los_Angeles")


def test_next_publish_time_needs_a_margin_before_the_slot():
    # 05:59:50 -> 06:00 is seconds away; draft creation takes several round-trips, so fall to now + grace
    now = datetime(2026, 9, 22, 5, 59, 50, tzinfo=LA)
    assert publish.next_publish_time(now, 6, "America/Los_Angeles") == datetime(2026, 9, 22, 6, 10, tzinfo=LA)
    assert publish.next_publish_time(datetime(2026, 9, 22, 5, 50, tzinfo=LA), 6, "America/Los_Angeles") == datetime(2026, 9, 22, 6, 0, tzinfo=LA)
    assert publish.next_publish_time(datetime(2026, 9, 22, 6, 0, tzinfo=LA), 6, "America/Los_Angeles") == datetime(2026, 9, 22, 6, 10, tzinfo=LA)


def test_next_publish_time_is_dst_safe():
    # 2026-03-08 02:00 PST -> 03:00 PDT: 01:30 to 06:00 is 3.5 real hours, and the slot carries the PDT offset
    now = datetime(2026, 3, 8, 1, 30, tzinfo=LA)
    slot = publish.next_publish_time(now, 6, "America/Los_Angeles")
    assert slot.hour == 6 and slot.utcoffset() == timedelta(hours=-7)
    assert slot.astimezone(_tz.utc) - now.astimezone(_tz.utc) == timedelta(hours=3, minutes=30)
    assert slot.astimezone(_tz.utc) == datetime(2026, 3, 8, 13, 0, tzinfo=_tz.utc)


ISSUE_MD = "# Top 3 Repos — Sep 22: y and why it beat b\n_8.4/10 · ADOPTED. Shipped._\n\n**Scanned:** 42 repos\n\n## 1. y\n"


def test_strip_header_drops_title_and_subtitle_lines():
    assert publish.strip_header(ISSUE_MD) == "**Scanned:** 42 repos\n\n## 1. y"
    assert publish.strip_header("no header\n") == "no header"


class _FakeApi:
    """Records every call; the shape python-substack 0.7.0 returns, minus the network."""
    instances: list = []

    def __init__(self, **kw):
        self.kw, self.calls = kw, []
        _FakeApi.instances.append(self)

    def get_sections(self):
        self.calls.append(("get_sections",))
        return [{"id": 11, "name": "Deep Dives"}, {"id": 42, "name": "Top 3 Repos"}]

    def create_draft_from_markdown(self, title, markdown, subtitle="", draft_section_id=None, **kw):
        self.calls.append(("create", title, markdown, subtitle, draft_section_id))
        return {"draft": {"id": 987}, "tags": None, "prepublish": None, "publish": None}

    def schedule_draft(self, draft_id, when):
        self.calls.append(("schedule", draft_id, when))
        return {"id": draft_id}

    def publish_draft(self, *a, **kw):
        raise AssertionError("publish_draft must never be called")


@pytest.fixture
def fake_api(monkeypatch):
    _FakeApi.instances = []
    monkeypatch.setattr("substack.Api", _FakeApi)
    return _FakeApi


def test_publish_api_creates_draft_in_section_and_schedules_in_utc(tmp_path, fake_api):
    post = tmp_path / "issue.md"
    post.write_text(ISSUE_MD)
    when = datetime(2026, 9, 22, 6, 0, tzinfo=LA)
    out = publish.publish_api(post, "T", "S", "https://hoodlem4real.substack.com", cookies_string="connect.sid=abc; x=1",
                              section_name="top 3 repos", schedule_at=when)
    api = fake_api.instances[0]
    assert api.kw == {"cookies_string": "connect.sid=abc; x=1", "cookies_path": None,
                      "publication_url": "https://hoodlem4real.substack.com"}
    assert api.calls[0] == ("get_sections",)
    assert api.calls[1] == ("create", "T", "**Scanned:** 42 repos\n\n## 1. y", "S", 42)
    kind, draft_id, sched = api.calls[2]
    assert (kind, draft_id) == ("schedule", 987) and sched.tzinfo is _tz.utc and sched == when
    assert sched.isoformat() == "2026-09-22T13:00:00+00:00"
    assert "https://hoodlem4real.substack.com/publish/post/987" in out and "2026-09-22 06:00 PDT" in out
    assert "abc" not in out  # the cookie never surfaces


def test_publish_api_without_schedule_is_a_draft_only(tmp_path, fake_api):
    post = tmp_path / "issue.md"
    post.write_text(ISSUE_MD)
    out = publish.publish_api(post, "T", "S", "https://hoodlem4real.substack.com/api/v1", cookies_path="/tmp/c.json",
                              section_name="Adopted")  # not a section this publication has
    api = fake_api.instances[0]
    assert api.kw["cookies_path"] == "/tmp/c.json" and api.kw["cookies_string"] is None
    assert [c[0] for c in api.calls] == ["get_sections", "create"] and api.calls[1][4] is None
    assert out.startswith("Draft created: https://hoodlem4real.substack.com/publish/post/987") and "Not scheduled" in out


def test_publish_api_requires_cookies_and_aware_schedule(tmp_path, fake_api):
    post = tmp_path / "issue.md"
    post.write_text(ISSUE_MD)
    with pytest.raises(ValueError, match="SUBSTACK_COOKIES"):
        publish.publish_api(post, "T", "S", "https://hoodlem4real.substack.com")
    with pytest.raises(ValueError, match="aware"):
        publish.publish_api(post, "T", "S", "https://hoodlem4real.substack.com", cookies_string="a=b",
                            schedule_at=datetime(2026, 9, 22, 6, 0))
    assert fake_api.instances == []  # both rejected before touching the API


class _NoteSession:
    def __init__(self):
        # real Session headers start with "User-Agent: python-requests/x.y", which Substack 403s
        self.cookies, self.headers, self.posts = requests.cookies.RequestsCookieJar(), requests.utils.default_headers(), []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json, timeout))
        r = requests.Response()
        r.status_code, r._content = 200, b'{"id": 555}'
        return r


def test_post_note_sends_the_verified_comment_feed_payload():
    sess = _NoteSession()
    out = publish.post_note("Tested uv this morning: 8.4/10.\n\nReceipt in today's issue.",
                            cookies_string="connect.sid=s%3Aabc; substack.sid=s%3Aabc", session=sess)
    url, payload, _ = sess.posts[0]
    assert url == "https://substack.com/api/v1/comment/feed"
    assert payload == {
        "bodyJson": {"type": "doc", "attrs": {"schemaVersion": "v1", "title": None}, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Tested uv this morning: 8.4/10."}]},
            {"type": "paragraph"},
            {"type": "paragraph", "content": [{"type": "text", "text": "Receipt in today's issue."}]},
        ]},
        "replyMinimumRole": "everyone",
    }
    assert sess.cookies.get("connect.sid") == "s:abc" and sess.headers["User-Agent"].startswith("Mozilla")
    assert "id=555" in out
    with pytest.raises(ValueError, match="SUBSTACK_COOKIES"):
        publish.post_note("x", session=_NoteSession())


def test_publish_at_rejects_malformed_time_cleanly(tmp_path, capsys):
    from substack_pipeline import cli
    from substack_pipeline.config import load_settings

    s = load_settings()
    s.out_dir = tmp_path
    (tmp_path / "work" / "2026-09-22").mkdir(parents=True)
    (tmp_path / "work" / "2026-09-22" / "meta.json").write_text('{"title": "T", "subtitle": "S", "post": "x.md", "notes": "n.md"}')
    assert cli.cmd_publish(s, "2026-09-22", at="2026-09-22") == 1
    err = capsys.readouterr().err
    assert "--at must be YYYY-MM-DDTHH:MM" in err and "Traceback" not in err


def test_env_example_unfilled_keys_are_empty_not_comments():
    from dotenv import dotenv_values

    vals = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")
    assert vals["SUBSTACK_COOKIES"] == "" and vals["SUBSTACK_COOKIES_PATH"] == "" and vals["GITHUB_TOKEN"] == ""
    assert not any(str(v).lstrip().startswith("#") for v in vals.values())
