"""Pre-publish lint: block the tells of machine-written prose before an issue goes out.

Rules come from docs/WRITING_VOICE.md (sources: blader/humanizer, Wikipedia "Signs of AI writing",
theclaymethod/unslop, seyedehsanhadi/sloptrim). Code fences, inline code and URLs are never linted.

    substack lint out/2026-09-21-issue.md      # exit 1 on any finding; morning.sh runs this before publish

Profiles: "issue" applies every rule; "notes" (the three social Notes) allows "?" and "!" since a Note is a
question or a reaction by design.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LABELS = ("ADOPTED", "TRIED, NOT ADOPTED", "WATCHING")
REQUIRED_LINES = ("**What it claims:**", "**Why it scored", "**The test", "**Verdict:**")

# Words and phrases that mark machine prose. Case-insensitive; matched on prose only.
BANNED = [
    r"\bdelve", r"\brobust\b", r"\bseamless(?:ly)?\b", r"\bleverag(?:e|es|ed|ing)\b", r"\blandscape\b",
    r"\btapestry\b", r"\btestament\b", r"\bpivotal\b", r"\bcrucial\b", r"\bshowcas(?:e|es|ing)\b",
    r"\bunderscor(?:e|es|ing)\b", r"\bgame[- ]changer\b", r"\bgame[- ]changing\b", r"\bgroundbreaking\b",
    r"\bblazingly\b", r"\brevolutionary\b", r"\bcutting[- ]edge\b", r"\bnext[- ]gen(?:eration)?\b",
    r"\bsupercharge", r"\bunlock(?:s|ed|ing)? the\b", r"\bempower", r"\belevate\b", r"\bharness the\b",
    r"\bhere'?s the thing\b", r"\blet'?s dive in\b", r"\bdeep dive\b", r"\bit'?s worth noting\b",
    r"\bin summary\b", r"\bultimately\b", r"\bthe future (?:looks|is) bright\b", r"\bonly time will tell\b",
    r"\bexperts say\b", r"\bstands as\b", r"\bserves as\b", r"\bboasts\b", r"\badditionally\b", r"\bmoreover\b",
    r"\bfurthermore\b", r"\bin the world of\b", r"\bin today'?s (?:fast|ever|rapidly|world|digital|competitive)",
    r"\bat its core\b", r"\bmake no mistake\b",
    r"\blet that sink in\b", r"\bgreat question\b", r"\bI hope this helps\b", r"\bsimple yet powerful\b",
    r"\bbattle[- ]tested\b", r"\bproduction[- ]ready\b", r"\bdevelopers of all levels\b",
    r"\bsignificantly\b(?![^.]{0,30}\d)", r"\bdramatically\b(?![^.]{0,30}\d)",
]
HEDGES = re.compile(r"\b(may|might|could|seems?|perhaps|possibly|arguably|likely)\b", re.I)
NOT_BUT = [re.compile(r"\bnot (?:just |only |merely )?[^.?!\n]{1,60}?\bbut\b", re.I),
           re.compile(r"\bisn'?t [^.?!\n]{1,60}?, it'?s\b", re.I)]
LINE_START = re.compile(r"^\s*(?:In summary|Overall|Ultimately|Additionally|Moreover|Furthermore)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]")
CURLY = re.compile("[“”‘’]")
DASH = re.compile("[—–]")
URL = re.compile(r"(?:https?://|www\.)\S+|\([^()\s]+\)(?=\s|$)")  # bare URLs and markdown link targets
INLINE_CODE = re.compile(r"`[^`\n]*`")


@dataclass
class Finding:
    rule: str
    line: int
    excerpt: str

    def __str__(self) -> str:
        return f"{self.rule:<14} line {self.line}: {self.excerpt}"


def prose_lines(text: str) -> list[tuple[int, str]]:
    """(line_no, prose) with fenced blocks, inline code and URLs blanked out; line numbers kept."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append((i, ""))
            continue
        if in_fence:
            out.append((i, ""))
            continue
        cleaned = INLINE_CODE.sub(" ", line)
        cleaned = re.sub(r"\]\([^)]*\)", "]", cleaned)  # markdown link targets
        cleaned = URL.sub(" ", cleaned)
        out.append((i, cleaned))
    return out


def _excerpt(s: str, m: re.Match | None = None, width: int = 70) -> str:
    if m:
        start = max(0, m.start() - 25)
        return s[start:start + width].strip()
    return s.strip()[:width]


def lint_text(text: str, profile: str = "issue") -> list[Finding]:
    findings: list[Finding] = []
    lines = prose_lines(text)
    for no, s in lines:
        if not s.strip():
            continue
        if (m := DASH.search(s)):
            findings.append(Finding("dash", no, _excerpt(s, m)))
        if (m := CURLY.search(s)):
            findings.append(Finding("curly-quote", no, _excerpt(s, m)))
        if (m := EMOJI.search(s)):
            findings.append(Finding("emoji", no, _excerpt(s, m)))
        if profile == "issue":
            if "!" in s:
                findings.append(Finding("exclamation", no, _excerpt(s)))
            if "?" in s and "Reply and tell me" not in s:
                findings.append(Finding("question", no, _excerpt(s)))
        for rx in NOT_BUT:
            if (m := rx.search(s)):
                findings.append(Finding("not-x-but-y", no, _excerpt(s, m)))
        for rx in BANNED:
            if (m := re.search(rx, s, re.I)):
                findings.append(Finding("banned-word", no, _excerpt(s, m)))
        if LINE_START.search(s):
            findings.append(Finding("filler-opener", no, _excerpt(s)))
        for sentence in re.split(r"(?<=[.!?])\s+", s):
            if len(HEDGES.findall(sentence)) > 1:
                findings.append(Finding("hedge-stack", no, _excerpt(sentence)))
    if profile == "issue":
        findings.extend(_structure(text))
    return findings


def _structure(text: str) -> list[Finding]:
    """Every '## N. name' section carries a label in its heading and the five lines in order."""
    out: list[Finding] = []
    lines = text.splitlines()
    heads = [(i, l) for i, l in enumerate(lines, 1) if re.match(r"^## \d+\. ", l)]
    for idx, (no, head) in enumerate(heads):
        if not any(head.rstrip().endswith(lab) for lab in LABELS):
            out.append(Finding("label", no, _excerpt(head)))
        end = heads[idx + 1][0] - 1 if idx + 1 < len(heads) else len(lines)
        block = lines[no:end]
        pos = []
        for marker in REQUIRED_LINES:
            hit = next((j for j, l in enumerate(block) if l.startswith(marker)), None)
            if hit is None:
                out.append(Finding("five-lines", no, f"missing {marker}"))
            pos.append(hit)
        known = [p for p in pos if p is not None]
        if known != sorted(known):
            out.append(Finding("five-lines", no, "lines out of order"))
        test_line = next((l for l in block if l.startswith("**The test")), "")
        if test_line and "not run" not in test_line and not re.search(r"\d", test_line):
            out.append(Finding("test-line", no, "The test line needs a measured number or 'not run'"))
    return out


def lint_file(path: Path | str, profile: str = "issue") -> list[Finding]:
    return lint_text(Path(path).read_text(encoding="utf-8"), profile)
