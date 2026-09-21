# tylers-substack

Pipeline behind **Top 3 Repos** — a daily Substack where every GitHub repo that hits my LinkedIn feed
gets scored on a public rubric, the top 3 get hand-tested, and each one gets a verdict, a reproducible
test case, and a yes/no on whether I adopted it.

```
LinkedIn scrape (yours) ─► scan ─► enrich (GitHub API) ─► score (rubric + Claude) ─► test (Docker)
                                                                                        │
              Substack ◄─ publish ◄─ build (issue + 3 Notes) ◄─ review (Tyler: fit, verdict, label) ◄─┘
```

## Quick start

```bash
pip install -e ".[ai,dev]"
cp .env.example .env            # add GITHUB_TOKEN and ANTHROPIC_API_KEY
python -m pytest                # 9 tests
substack daily --auto           # whole loop, no human step (everything labelled WATCHING)
```

Point `POSTS_PATH` at your LinkedIn scraper's JSONL (one post per line; `url`, `text`, `author`, `date`).
The pipeline never scrapes LinkedIn itself and never publishes post text — only the repo link and my review.

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

Substack has no official write API. `substack publish` copies the issue to the clipboard and prints the
new-post URL. `substack login` then `substack publish --browser` uses a Playwright profile you log into
yourself (the pipeline never handles your password) and stops at a draft so you get a last look.

## Layout

```
src/substack_pipeline/   ingest · github_enrich · scorer · tester · review · build · publish · store · cli
config/rubric.yaml       weights, selection rules, labels, publication settings
data/                    posts.jsonl (yours, git-ignored) · pipeline.sqlite · adopted.csv · samples/
tested-daily/            one folder per day per repo: run.sh + result.json
out/                     <date>-issue.md, <date>-notes.md, work/<date>/*.json
scripts/morning.sh       the 6 AM window
.github/workflows/       nightly scan+score
```
