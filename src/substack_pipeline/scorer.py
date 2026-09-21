"""Stage 3: score each repo on the rubric and draft the review lines.

Deterministic signals (traction, maintenance, license_safety) come from GitHub data so they are
auditable. Novelty and the draft prose come from Claude when ANTHROPIC_API_KEY is set; otherwise a
heuristic fills novelty and the draft lines come from the README, so the pipeline never blocks.
"""
from __future__ import annotations

import json
import logging
import math

from .models import Repo, Review, Score

log = logging.getLogger(__name__)

OSI_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0", "GPL-3.0", "GPL-2.0",
                "LGPL-3.0", "LGPL-2.1", "ISC", "Unlicense", "0BSD", "AGPL-3.0"}
INCUMBENTS = ["langchain", "llamaindex", "litellm", "openai", "huggingface", "fastapi", "airflow",
              "prefect", "dagster", "pandas", "polars", "vllm", "ollama", "crewai", "autogen"]


def _clamp(x: float) -> float:
    return round(max(0.0, min(10.0, x)), 1)


def score_traction(repo: Repo) -> tuple[float, str]:
    age = repo.age_days
    # Stars per day since creation, log-scaled; 7-day velocity gets a boost.
    velocity = repo.stars / age
    base = 3.5 * math.log10(1 + velocity * 10)  # 1 star/day -> ~3.6; 10/day -> ~7; 100/day -> ~10.5
    boost = 0.0
    if repo.stars_7d:
        boost = min(2.5, math.log10(1 + repo.stars_7d))  # 100 in a week -> +2
    committers = min(1.0, repo.unique_committers_30d * 0.25)
    s = _clamp(base + boost + committers)
    why = (f"{repo.stars:,} stars over {age}d"
           + (f", {repo.stars_7d} in the last 7d" if repo.stars_7d is not None else "")
           + f", {repo.unique_committers_30d} committers/30d")
    return s, why


def score_maintenance(repo: Repo) -> tuple[float, str]:
    s = 0.0
    s += min(4.0, repo.commits_30d * 0.2)          # 20 commits/month -> 4
    s += 2.0 if repo.has_ci else 0.0
    s += 2.0 if repo.latest_release else 0.0
    if repo.stars and repo.open_issues / max(repo.stars, 1) < 0.05:
        s += 2.0                                    # issues under control relative to popularity
    why = (f"{repo.commits_30d} commits/30d, CI {'yes' if repo.has_ci else 'no'}, "
           f"release {repo.latest_release or 'none'}, {repo.open_issues} open issues")
    return _clamp(s), why


def score_license_safety(repo: Repo) -> tuple[float, str]:
    if not repo.license or repo.license == "NOASSERTION":
        return 2.0, "no recognised license"
    s = 8.0 if repo.license in OSI_LICENSES else 4.0
    if repo.license.startswith("AGPL") or repo.license.startswith("GPL"):
        s -= 1.0  # fine, but a real constraint for a commercial stack
    readme = repo.readme.lower()
    if "telemetry" in readme or "analytics" in readme:
        s -= 1.0
    if repo.dependency_count is not None and repo.dependency_count > 60:
        s -= 1.0
    return _clamp(s + 2.0 if s >= 8 else s), f"{repo.license}"


def score_novelty_heuristic(repo: Repo) -> tuple[float, str]:
    readme = (repo.description + " " + repo.readme[:3000]).lower()
    own = repo.full_name.split("/")[1].lower()
    named = [i for i in INCUMBENTS if i in readme and i != own]
    # Mentioning incumbents and claiming a difference is a weak positive signal; being a wrapper is not.
    s = 5.0
    if any(w in readme for w in ("faster than", "alternative to", "unlike", "instead of", "drop-in")):
        s += 1.5
    if any(w in readme for w in ("wrapper", "thin layer", "port of")):
        s -= 1.5
    if not named:
        s += 0.5
    return _clamp(s), ("heuristic; compares itself to " + ", ".join(named)) if named else "heuristic"


def draft_heuristic(repo: Repo) -> dict[str, str]:
    first = (repo.description or repo.readme.strip().splitlines()[0] if repo.readme.strip() else "").strip("# ").strip()
    return {
        "claims": first[:160] or "(no description)",
        "novelty_rationale": "",
        "test_idea": f"pip install / clone {repo.full_name}, run the README quickstart, confirm one call succeeds.",
    }


SYSTEM_PROMPT = """You grade GitHub repositories for a daily newsletter read by builders and AI engineers.
Score NOVELTY on 0-10: does this repo do something the obvious incumbent (LangChain, LiteLLM, vLLM,
FastAPI, Airflow, etc.) does not, or do it materially better? A thin wrapper or re-implementation scores 2-4;
a clear new capability with evidence scores 7-9. Ignore hype in the description; use the README.
Also write:
- claims: one sentence, <= 25 words, what the repo says it does (no marketing adjectives)
- novelty_rationale: one sentence citing the specific feature or benchmark that justifies the score
- test_idea: one concrete 2-minute test a reader could run to verify the main claim (a command or <= 10 lines)
Return JSON only: {"novelty": <float>, "claims": "...", "novelty_rationale": "...", "test_idea": "..."}"""


def score_novelty_ai(repo: Repo, api_key: str, model: str) -> tuple[float, dict[str, str]] | None:
    try:
        import anthropic  # type: ignore
    except ImportError:
        log.warning("anthropic not installed; pip install 'substack-pipeline[ai]'")
        return None
    client = anthropic.Anthropic(api_key=api_key)
    content = (f"Repo: {repo.full_name}\nDescription: {repo.description}\nLanguage: {repo.language}\n"
               f"Stars: {repo.stars}\nLicense: {repo.license}\n\nREADME:\n{repo.readme[:8000]}")
    try:
        msg = client.messages.create(model=model, max_tokens=600, system=SYSTEM_PROMPT,
                                     messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
        return _clamp(float(data["novelty"])), {k: str(data.get(k, "")) for k in ("claims", "novelty_rationale", "test_idea")}
    except Exception as e:  # noqa: BLE001
        log.warning("AI scoring failed for %s: %s", repo.full_name, e)
        return None


def score_repo(repo: Repo, api_key: str | None = None, model: str = "") -> Review:
    sc = Score()
    sc.traction, sc.rationale["traction"] = score_traction(repo)
    sc.maintenance, sc.rationale["maintenance"] = score_maintenance(repo)
    sc.license_safety, sc.rationale["license_safety"] = score_license_safety(repo)

    draft: dict[str, str] | None = None
    if api_key:
        res = score_novelty_ai(repo, api_key, model)
        if res:
            sc.novelty, draft = res
            sc.rationale["novelty"] = draft.get("novelty_rationale", "")
    if draft is None:
        sc.novelty, sc.rationale["novelty"] = score_novelty_heuristic(repo)
        draft = draft_heuristic(repo)

    why = "; ".join(f"{k}: {v}" for k, v in sc.rationale.items() if v)
    return Review(repo=repo, score=sc, claims=draft["claims"], why_scored=why,
                  test_summary=draft["test_idea"])


def rank(reviews: list[Review], weights: dict[str, float]) -> list[Review]:
    return sorted(reviews, key=lambda r: r.score.total(weights), reverse=True)
