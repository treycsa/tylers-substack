# tylers-substack

Pipeline behind **Top 3 Repos** ([hoodlem4real.substack.com](https://hoodlem4real.substack.com)) — a daily
Substack where every repo that trends or launches on GitHub gets scored on a public rubric, the top 3 get
hand-tested, and each one gets a verdict, a reproducible test case, and a yes/no on whether I adopted it.

```
GitHub trending RSS + new-repo search ─► scan ─► enrich (GitHub API) ─► score (rubric + Claude) ─► test (Docker)
                                                                                                      │
              Substack ◄─ publish ◄─ build (issue + 3 Notes) ◄─ review (Tyler: fit, verdict, label) ◄───────┘
```

## Quick start

```bash
pip install -e ".[ai,dev]"
cp .env.example .env            # add GITHUB_TOKEN (optional but avoids the 10 req/min search limit) and ANTHROPIC_API_KEY
python -m pytest                # 26 tests
substack daily --auto           # whole loop, no human step (everything labelled WATCHING)
```

Discovery is GitHub-native and needs nothing scraped: `substack scan` (default `--source github`) reads the
hosted [GitHubTrendingRSS](https://github.com/mshibanami/GitHubTrendingRSS) feeds listed under `discover.feeds`
in `config/rubric.yaml` and runs one GitHub Search API query for repos created in the last `days_back` days
with at least `min_stars` stars. A repo's "mentions" is the number of distinct sources that surfaced it.
`--source posts` still accepts a JSONL of posts at `POSTS_PATH` (`url`, `text`, `author`, `date`) if you have
one; `--source both` merges the two. Post text is never published — only the repo link and my review.

## The daily loop

| When | Command | Who | Output |
| --- | --- | --- | --- |
| 11 PM (GitHub Actions) | `substack scan` → `enrich` → `score` | cron | `out/work/<date>/scored.json` |
| 6:00 AM | `scripts/morning.sh` = `test` → `review` → `build` → `publish` | Tyler, ~45 min | `out/<date>-issue.md`, `out/<date>-notes.md`, `tested-daily/<date>/…/run.sh` |
| Missed morning | `substack review --auto && substack build` | cron fallback | Issue ships with WATCHING labels, no ADOPTED claim |

`substack leaderboard --days 30` prints the month's top 10 for the monthly leaderboard post.

## Rubric (`config/rubric.yaml`)

Six signals, weights sum to 10. Traction, maintenance and license come from GitHub data; novelty and the
draft lines come from Claude (heuristic fallback without a key); "runs out of the box" comes from the Docker
test; "fit" is mine. Guardrails: <50 stars needs an override, a failed install caps at 6.0, ADOPTED requires
a passing test. The rubric is published verbatim on the About page.

## Test receipts

`substack test` writes `tested-daily/<date>/<owner>__<repo>/run.sh` and runs it in `python:3.11-slim`
with a 10-minute timeout. Edit the part under the marker line to exercise the repo's main claim; that
snippet is what the issue shows, and the file is linked from the post as the receipt.

## Publishing

Substack has no official write API, so all three paths stop at a draft (or a scheduled draft) — never
"published" — so you always get a last look in the editor.

1. **`substack publish --api`** (recommended; `pip install -e ".[publish]"`). Uses
   [python-substack](https://github.com/ma2za/python-substack) with cookie auth: put the Cookie header
   from browser devtools on substack.com into `SUBSTACK_COOKIES` in `.env` (or a JSON cookie file in
   `SUBSTACK_COOKIES_PATH`). Creates a draft in the `publication.section` from `config/rubric.yaml`
   ("Top 3 Repos"); `--schedule` also schedules it for the next `publish_hour_local` (6:00
   America/Los_Angeles, or 10 minutes from now if that slot has passed or is less than 10 minutes away); `--at 2026-09-22T06:00`
   schedules for an explicit local time; `--section NAME` overrides the section. The cookie grants full
   account access — it is never logged and `.env` is git-ignored. `scripts/morning.sh` takes this path
   automatically when the cookie is set.
2. **`substack publish --browser`**: `substack login` once, then a Playwright profile you logged into
   yourself (the pipeline never handles your password) fills a new post and stops at a draft.
3. **`substack publish`** (manual, the fallback): copies the issue to the clipboard and prints the
   new-post URL.

Notes: python-substack cannot post Notes; `publish.post_note(text, cookies_string=...)` hits the
verified `POST /api/v1/comment/feed` shape from
[substack-api-reference](https://github.com/AnthonyDavidAdams/substack-api-reference) and is not wired
to a CLI flag yet — the 3 Notes in `out/<date>-notes.md` are still posted by hand across the day.

## Layout

```
src/substack_pipeline/   discover · ingest (legacy JSONL) · github_enrich · scorer · tester · review · build · publish · store · cli
config/rubric.yaml       weights, selection rules, labels, publication settings, discover feeds + search
data/                    pipeline.sqlite · adopted.csv · samples/ · posts.jsonl (optional, git-ignored)
tested-daily/            one folder per day per repo: run.sh + result.json
out/                     <date>-issue.md, <date>-notes.md, work/<date>/*.json
scripts/morning.sh       the 6 AM window
.github/workflows/       nightly scan+score
```
