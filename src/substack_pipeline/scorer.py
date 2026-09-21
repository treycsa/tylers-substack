"""Stage 3: score each repo on the rubric and draft the review lines.

Deterministic signals (traction, maintenance, license_safety) come from GitHub data so they are
auditable. Novelty and the draft prose come from Claude when ANTHROPIC_API_KEY is set; otherwise a
heuristic fills novelty and the draft lines come from the README, so the pipeline never blocks.
"""
from __future__ import annotations

import json
import logging
import math
import re

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


MARKETING = re.compile(r"\b(blazingly|blazing|powerful|seamless(?:ly)?|revolutionary|game[- ]changing|cutting[- ]edge|"
                       r"next[- ]gen(?:eration)?|ultimate|supercharged?|robust|elegant|effortless(?:ly)?|"
                       r"lightning[- ]fast|state[- ]of[- ]the[- ]art)\b[,]?\s*", re.I)


def plain_claim(text: str, limit: int = 160) -> str:
    """The repo's own description with the marketing adjectives removed, sentence-cased, one line."""
    s = MARKETING.sub("", text or "").strip().strip("# ").strip()
    s = re.sub(r"\s+", " ", s).replace(" ,", ",").strip(" ,")
    s = re.sub(r"[—–]", ", ", s)  # no dashes in generated prose (see docs/WRITING_VOICE.md)
    s = s[:1].upper() + s[1:] if s else s
    return s[:limit]


def draft_heuristic(repo: Repo) -> dict[str, str]:
    first = repo.description or (repo.readme.strip().splitlines()[0] if repo.readme.strip() else "")
    return {
        "claims": plain_claim(first) or "(no description)",
        "novelty_rationale": "",
        "test_idea": f"`git clone {repo.url}` and run the README quickstart; confirm one call succeeds.",
    }


SYSTEM_PROMPT = """You draft the fact lines for a daily review of GitHub repos read by builders and AI engineers.
Write like an engineer messaging a colleague who will run the command next. Use only facts in the input
(README, description, stars, license). Never invent a number, command, benchmark, name, or result.

Score NOVELTY on 0-10: does this repo do something the obvious incumbent (LangChain, LiteLLM, vLLM,
FastAPI, Airflow, etc.) does not, or do it measurably better? A thin wrapper or re-implementation scores 2-4;
a clear new capability with evidence in the README scores 7-9. Ignore hype in the description.

Also write:
- claims: one sentence, at most 25 words, the repo's own claim in plain words. No adjectives like fast,
  powerful, seamless, robust, blazing, cutting-edge, game-changing. Say what it does and what it replaces.
- novelty_rationale: one sentence naming the specific feature, benchmark, or README section that justifies
  the score. Say "README says" for claims you could not verify.
- test_idea: the exact command (or at most 10 lines) a reader runs in 2 minutes to check the main claim,
  in backticks, with the number they should see if the README states one.

Style rules, all of them: plain verbs (is, has, runs, took, failed); contractions are fine; sentence case;
no em or en dashes; no bold, emoji, exclamation marks, or rhetorical questions; no "not X but Y"; no
delve, robust, seamless, leverage, landscape, game-changer, groundbreaking, pivotal, crucial, showcase,
underscore, "it's worth noting", "in summary", "overall", "ultimately", "significantly" without a number.
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
