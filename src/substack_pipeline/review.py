"""Stage 5: Tyler's 45-minute window. Interactive CLI that walks the tested candidates,
asks for a Fit score (0-10), a verdict line and a label, then picks the top 3.

Non-interactive fallback (`--auto`): everything becomes WATCHING with an empty verdict, so a missed
morning still ships an issue and never claims ADOPTED.
"""
from __future__ import annotations

from .models import Review, TestResult

LABELS = ["ADOPTED", "TRIED, NOT ADOPTED", "WATCHING"]


def _ask(prompt: str, default: str = "") -> str:
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return v or default


def review_interactive(reviews: list[Review], weights: dict[str, float], picks: int,
                       fail_cap: float) -> list[Review]:
    print("\n=== Tyler review window: Fit score, verdict, label ===\n")
    for i, r in enumerate(reviews, 1):
        t = r.test
        print(f"[{i}/{len(reviews)}] {r.repo.full_name}  ({r.repo.stars:,} stars, {r.repo.license})")
        print(f"   claims: {r.claims}")
        print(f"   pre-test score: {r.score.total(weights)}  |  {r.why_scored}")
        if t:
            print(f"   test: {'PASS' if t.passed else 'FAIL'} in {t.duration_s}s  ({t.script_path})")
            if t.notes:
                print(f"   note: {t.notes}")
        fit = _ask("   Fit for a real stack, 0-10", "5")
        try:
            r.score.fit = max(0.0, min(10.0, float(fit)))
        except ValueError:
            r.score.fit = 5.0
        r.verdict = _ask("   Verdict line (your words)", "")
        label = _ask(f"   Label {LABELS}", "WATCHING").upper()
        r.label = next((l for l in LABELS if l.startswith(label[:3])), "WATCHING")
        if r.label == "ADOPTED" and not (t and t.passed):
            print("   ! ADOPTED requires a passing test; downgrading to TRIED, NOT ADOPTED")
            r.label = "TRIED, NOT ADOPTED"
        r.test_snippet = _ask("   Test snippet to show (blank = from run.sh)", "")
        print()
    return finalize(reviews, weights, picks, fail_cap)


def review_auto(reviews: list[Review], weights: dict[str, float], picks: int, fail_cap: float) -> list[Review]:
    for r in reviews:
        r.score.fit = None
        r.label = "WATCHING"
        r.verdict = r.verdict or "Auto-published: not hand-reviewed today. Re-test scheduled."
    return finalize(reviews, weights, picks, fail_cap)


def finalize(reviews: list[Review], weights: dict[str, float], picks: int, fail_cap: float) -> list[Review]:
    """Apply guardrails, rank, and cut to the published set."""
    def total(r: Review) -> float:
        t = r.score.total(weights)
        if r.test and not r.test.passed and r.test.command != "(docker unavailable)":
            t = min(t, fail_cap)
        return t

    ranked = sorted(reviews, key=lambda r: (total(r), -(r.test.duration_s if r.test else 9e9)), reverse=True)
    return ranked[:picks]


def snippet_from_script(r: Review) -> str:
    """Show the reader the part of run.sh below the marker line."""
    if r.test_snippet:
        return r.test_snippet
    if r.test and r.test.script_path:
        try:
            lines = open(r.test.script_path).read().splitlines()
            if "# --- your 2-minute test goes below this line ---" in lines:
                idx = lines.index("# --- your 2-minute test goes below this line ---")
                return "\n".join(lines[idx + 1:]).strip()
        except OSError:
            pass
    return f"git clone {r.repo.url} && cd {r.repo.full_name.split('/')[1]}  # then follow the README quickstart"
