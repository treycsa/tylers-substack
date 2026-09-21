"""Stage 6: assemble the issue markdown (Substack-ready) and three Notes from the reviews."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .models import Issue, Review
from .review import snippet_from_script


def _title(issue: Issue) -> tuple[str, str]:
    d = date.fromisoformat(issue.date).strftime("%b %-d")
    if not issue.reviews:
        return f"Top 3 Repos — {d}: nothing cleared the bar", "Quiet day. Here's what came close."
    win = issue.reviews[0]
    runner = issue.reviews[1].repo.full_name.split("/")[1] if len(issue.reviews) > 1 else "the field"
    title = f"Top 3 Repos — {d}: {win.repo.full_name.split('/')[1]} and why it beat {runner}"
    subtitle = f"{issue.totals[win.repo.full_name]}/10 · {win.label}. {win.verdict}".strip()
    return title, subtitle[:200]


REPO_URL = "https://github.com/treycsa/tylers-substack/blob/main/"  # set publication.repo_url in rubric.yaml to override


def _review_block(i: int, r: Review, total: float, repo_url: str = REPO_URL) -> str:
    t = r.test
    test_line = ""
    if t and t.command != "(docker unavailable)":
        mins = max(1, round(t.duration_s / 60))
        test_line = f"**The test ({mins} min):** {'passed' if t.passed else 'FAILED'} — {r.test_summary}"
    else:
        test_line = f"**The test:** not run today — {r.test_summary}"
    lang = "bash" if not r.test_snippet or r.test_snippet.startswith(("git ", "pip ", "curl ", "npm ")) else "python"
    receipt = f" ([receipt]({repo_url}{r.test.script_path}))" if t and t.script_path else ""
    return "\n".join([
        f"## {i}. {r.repo.full_name.split('/')[1]} — {total}/10 · {r.label}",
        f"[{r.repo.full_name}]({r.repo.url}) · {r.repo.stars:,} stars · {r.repo.license or 'no license'}",
        "",
        f"**What it claims:** {r.claims}",
        f"**Why it scored {'high' if total >= 7 else 'where it did'}:** {r.why_scored}",
        f"{test_line}{receipt}",
        "",
        f"```{lang}",
        snippet_from_script(r),
        "```",
        "",
        f"**Verdict:** {r.verdict or '—'}",
        "",
    ])


def render_issue(issue: Issue, repo_url: str = REPO_URL) -> str:
    title, subtitle = _title(issue)
    adopted, reviewed = issue.adopted_this_week
    scanned = f"{issue.scanned_posts} posts" if issue.source == "posts" else (issue.scanned_label or f"{issue.scanned_posts} repos surfaced")
    how = ("every repo that hits my LinkedIn feed" if issue.source == "posts"
           else "every repo surfaced by GitHub trending and new-repo search")
    head = [
        f"# {title}",
        f"_{subtitle}_",
        "",
        f"**Scanned:** {scanned} · {issue.unique_repos} unique repos · "
        f"top score {issue.top_score}/10 · {sum(1 for r in issue.reviews if r.label == 'ADOPTED')} adopted",
        "",
        "---",
        "",
    ]
    body = [_review_block(i, r, issue.totals[r.repo.full_name], repo_url) for i, r in enumerate(issue.reviews, 1)]
    tail = [
        "---",
        f"**Adopted this week:** {adopted} of {reviewed} · running log → /adopted",
        "**Reply and tell me:** which of these would you actually put in prod?",
        "",
        f"_How I make this: an AI scores {how} on a public rubric and drafts "
        "the claim and score lines. I run the test, write the verdict, and set the label._",
    ]
    return "\n".join(head + body + tail)


def render_notes(issue: Issue) -> list[str]:
    """Three Notes per issue: the winner (quick fix), a contrarian take, and a question."""
    if not issue.reviews:
        return ["No repo cleared 6/10 today. That's the point of testing them."]
    win = issue.reviews[0]
    wt = issue.totals[win.repo.full_name]
    notes = [
        f"Tested {win.repo.full_name.split('/')[1]} this morning: {wt}/10, {win.label}. "
        f"{win.verdict or win.claims} Full test + receipt in today's issue.",
    ]
    failed = [r for r in issue.reviews if r.test and not r.test.passed and r.test.command != "(docker unavailable)"]
    if failed:
        f = failed[0]
        notes.append(f"{f.repo.stars:,} stars and the quickstart failed in a clean container. "
                     f"{f.repo.full_name.split('/')[1]} caps at 6/10 until that's fixed. Stars are not a test.")
    else:
        low = issue.reviews[-1]
        notes.append(f"Everyone's posting {low.repo.full_name.split('/')[1]}. I ran it: "
                     f"{issue.totals[low.repo.full_name]}/10, {low.label}. Hype in the post never moves the score.")
    notes.append(f"Which of today's three would you actually put in prod? "
                 f"{', '.join(r.repo.full_name.split('/')[1] for r in issue.reviews)} — reply with one.")
    return notes


def write_outputs(issue: Issue, out_dir: Path, repo_url: str = REPO_URL) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    post = out_dir / f"{issue.date}-issue.md"
    notes = out_dir / f"{issue.date}-notes.md"
    post.write_text(render_issue(issue, repo_url))
    notes.write_text("\n\n---\n\n".join(render_notes(issue)) + "\n")
    return post, notes
