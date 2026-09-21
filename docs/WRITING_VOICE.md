# Writing voice: how an issue should read

Every issue is read by a builder who will run the command next. Write like an engineer messaging a
colleague. The AI drafts the claim and score lines from the facts in `scored.json`; Tyler runs the test,
writes the verdict, and sets the label. `substack lint` enforces the mechanical parts of this document
before anything is published.

Sources (all read 2026-09-20; stars and dates from the GitHub API that day):

- blader/humanizer, https://github.com/blader/humanizer (50,717 stars, pushed 2026-09-06): 25 tells of
  machine prose, strongest first. Its "keep the voice" list (odd detail, mixed feelings, self-correction)
  is the positive target.
- Wikipedia, Signs of AI writing, https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing (revised
  2026-09-20): vocabulary list, rule of three, copula avoidance. A Sept-2026 note says em dashes are now
  mostly a Claude tell.
- theclaymethod/unslop, https://github.com/theclaymethod/unslop (454 stars, pushed 2026-09-07): the taboo
  phrase regex block and structure thresholds (avg sentence under 8 or over 34 words, 3+ one-line
  paragraphs, 4+ repeated openers).
- Nanako0129/sepia, https://github.com/Nanako0129/sepia (2,718 stars, pushed 2026-09-20): tech-article
  house style. Open at the number that made you look, one opinion plus its disagreement condition,
  include the dead end.
- seyedehsanhadi/sloptrim, https://github.com/seyedehsanhadi/sloptrim (209 stars, pushed 2026-09-16):
  stdlib-only scorer, 71 patterns.
- Human verdicts to imitate: https://www.loopwerk.io/articles/2026/uv-ux-mess/ (every gripe tied to a
  command), https://simonwillison.net/2026/Jul/28/uv/ (zero em dashes, "I think it's time I switched"),
  https://newsletter.pragmaticengineer.com/p/what-is-happening-with-code-reviews ("PRs merged: 353 ->
  684 (+94%)", "I was skeptical about...").

## The shape

Five lines per repo, always in this order, plain labels, nothing else bolded:

    What it claims:  one sentence, the repo's own claim, no adjectives
    Why it scored:   the rubric numbers, sourced from GitHub data
    The test:        the exact command in backticks, the number you measured, what broke
    Verdict:         one first-person opinion with a scope and the condition that would change it
    Label:           ADOPTED / TRIED, NOT ADOPTED / WATCHING (the heading carries it)

End each repo on the verdict. No intro, no recap, no sign-off. 700 to 900 words for three repos.

## Banned, and what to write instead

| Pattern | Instead |
|---|---|
| not X but Y; "isn't X, it's Y" | state Y |
| em dash, en dash | comma, period, parentheses |
| adjective triads ("fast, simple, and powerful") | one measured claim |
| delve, robust, seamless, leverage, landscape, tapestry, testament, pivotal, crucial, showcase, underscore | the plain word |
| game-changer, groundbreaking, blazingly fast, revolutionary, cutting-edge | the number |
| rhetorical question | the answer |
| "Is it perfect? No." | a sentence |
| bold labels inside prose | the five line names only |
| Great question / Let's dive in / I hope this helps | delete |
| In summary / Overall / Ultimately | end on the last fact |
| the future looks bright / only time will tell | cut |
| Here's the thing / Honestly? | cut |
| It's worth noting | the note alone |
| stands as / serves as / boasts | is, has |
| "-ing" riders ("..., highlighting its maturity") | cut |
| experts say / the community | a name, or cut |
| hedge stacks (may perhaps possibly) | one hedge |
| significantly / dramatically without a number | the number |
| "everything from X to Y" | the items |
| "In the world of X" | the repo |
| "I'm not saying / To be clear" | cut |
| Title Case, emoji, arrows in prose | sentence case, none |
| curly quotes | straight quotes |
| simple yet powerful | drop the pair |
| make no mistake / let that sink in | cut |
| at its core / the real question | the claim |
| seamlessly integrates | what it connects to |
| developers of all levels | cut |
| deep dive / unpack / explore | read, ran, measured |
| battle-tested, production-ready | one or none |
| colon reveal ("The catch: ...") | a sentence |
| fragment rows ("No config. Just speed.") | one sentence with a fact |
| same opener three times | vary |
| exclamation marks | none |
| Additionally / Moreover / Furthermore | a new sentence |

## Do this

1. Open with the repo's own claim, then your number. Never the category.
2. Exact command in backticks.
3. Say what broke and what you got instead.
4. One first-person opinion per verdict, with the condition that would change it.
5. Contractions are fine. The register is a colleague's Slack message.
6. Numbers carry their conditions: clean container, version, same box.
7. Compare against what readers use now.
8. "ignores .python-version in a subdir" beats "rough edges".
9. One sentence under 10 words per repo.
10. The verdict says who should use it today and who should wait.
11. Say what you did not test.
12. Attribute: "README says" or "I measured".
13. Tag unverified claims "reported".
14. The surprising repo gets three times the words.
15. Plain verbs: is, has, took, failed.
16. When no test ran, the test line says so. No ADOPTED without a passing test.
17. End on the verdict, no recap.

## Model paragraph

    What it claims: uv (astral-sh/uv) is a Python package and project manager written in Rust, pitched
    as a drop-in for pip and venv.
    Why it scored: 62,000 stars in 700 days, 900 this week, 25 committers in 30 days, CI green, MIT.
    The test: clean container, `pip install uv`, then the README quickstart. Install took 41s.
    `uv pip install requests` resolved in 0.3s; pip took 4.1s on the same box. One thing bit me:
    `uv venv` ignored the .python-version in a subdirectory and gave me the system interpreter
    instead of 3.12.
    Verdict: use it for new projects today. The speed claim holds and the install is boring in the
    good way. Pin your interpreter at the repo root until that subdir bug is fixed.
    Label: ADOPTED.

## Pre-publish checklist

`[R]` = enforced by `substack lint`.

1. [R] zero em or en dashes outside code
2. [R] zero banned words or phrases
3. [R] no not-X-but-Y or isn't-X-it's-Y constructions
4. [R] no line starts with In summary / Overall / Ultimately / Additionally / Moreover
5. [R] no "!" and no "?" in prose (the closing "Reply and tell me" line is the one question)
6. [R] no emoji, no curly quotes
7. [R] every repo section carries a label in its heading and the four lines in order
8. [R] "The test" line has a measured number or says "not run"
9. [R] at most one hedge per sentence
10. [human] the verdict has a scope and a reversal condition, and every number in it appears in the facts
