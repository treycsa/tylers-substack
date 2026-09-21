"""Data shapes that flow through the pipeline. Everything serialises to plain dicts/JSON."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Post:
    """One scraped LinkedIn post. Only the repo link and metadata are kept; the post text is never published."""
    url: str
    author: str
    text: str
    posted_at: str  # ISO date
    repo_links: list[str] = field(default_factory=list)


@dataclass
class Repo:
    full_name: str  # owner/name
    url: str
    description: str = ""
    stars: int = 0
    forks: int = 0
    stars_7d: int | None = None
    created_at: str = ""
    pushed_at: str = ""
    commits_30d: int = 0
    unique_committers_30d: int = 0
    open_issues: int = 0
    license: str | None = None
    has_ci: bool = False
    latest_release: str | None = None
    language: str | None = None
    readme: str = ""
    dependency_count: int | None = None
    mentioned_by: list[str] = field(default_factory=list)  # LinkedIn post URLs that surfaced it
    mention_count: int = 0

    @property
    def age_days(self) -> int:
        if not self.created_at:
            return 0
        created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        return max(1, (datetime.now(created.tzinfo) - created).days)


@dataclass
class Score:
    """Per-signal scores on a 0-10 scale; `total` is the weighted sum on 10."""
    traction: float = 0.0
    maintenance: float = 0.0
    runs_out_of_box: float | None = None  # None until tested
    novelty: float = 0.0
    license_safety: float = 0.0
    fit: float | None = None  # None until Tyler scores it
    rationale: dict[str, str] = field(default_factory=dict)

    def total(self, weights: dict[str, float]) -> float:
        num, den = 0.0, 0.0
        for k, w in weights.items():
            v = getattr(self, k)
            if v is None:
                continue
            num += v * w
            den += w
        # Normalise so an untested repo still reads on a 10-point scale.
        return round(10 * num / (10 * den), 1) if den else 0.0


@dataclass
class TestResult:
    passed: bool
    duration_s: float
    command: str
    stdout_tail: str = ""
    script_path: str = ""
    notes: str = ""


@dataclass
class Review:
    repo: Repo
    score: Score
    claims: str = ""          # "What it claims"
    why_scored: str = ""      # "Why it scored high"
    test_summary: str = ""    # "The test (N min)"
    test_snippet: str = ""    # code shown in the post
    verdict: str = ""         # Tyler's line
    label: str = "WATCHING"
    test: TestResult | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Issue:
    date: str
    scanned_posts: int
    unique_repos: int
    reviews: list[Review]
    adopted_this_week: tuple[int, int] = (0, 0)
    totals: dict[str, float] = field(default_factory=dict)  # repo full_name -> weighted total
    source: str = "posts"     # "github" | "posts" | "both" — where the candidates came from
    scanned_label: str = ""   # human line for the header, e.g. "42 repos surfaced across GitHub trending + new-repo search"

    @property
    def top_score(self) -> float:
        return max(self.totals.values(), default=0.0)
