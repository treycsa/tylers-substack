#!/usr/bin/env bash
# Tyler's 6:00 AM window. Nightly has already scanned/enriched/scored (or run `substack daily`).
# 1) tests the top 5 in Docker  2) asks you for Fit/verdict/label  3) builds the issue + Notes  4) copies to clipboard.
set -euo pipefail
cd "$(dirname "$0")/.."
DAY="${1:-$(date +%F)}"
git pull --quiet || true
substack --date "$DAY" test
substack --date "$DAY" review
substack --date "$DAY" build
substack --date "$DAY" publish
git add tested-daily out/work out/*.md data/adopted.csv data/pipeline.sqlite
git commit -qm "issue $DAY" && git push --quiet || true
echo "Done. Paste the issue, then post the 3 Notes from out/$DAY-notes.md across the day."
