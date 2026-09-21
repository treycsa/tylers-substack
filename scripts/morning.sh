#!/usr/bin/env bash
# Tyler's 6:00 AM window. Nightly has already scanned/enriched/scored (or run `substack daily`).
# 1) tests the top 5 in Docker  2) asks you for Fit/verdict/label  3) builds the issue + Notes
# 4) publishes: a scheduled draft via the API when SUBSTACK_COOKIES / SUBSTACK_COOKIES_PATH is set, else clipboard.
set -euo pipefail
cd "$(dirname "$0")/.."
DAY="${1:-$(date +%F)}"
git pull --quiet || true
substack --date "$DAY" test
substack --date "$DAY" review
substack --date "$DAY" build
# .env is never sourced here (the CLI loads it via python-dotenv): an unquoted cookie value would make bash
# execute part of it and echo the secret. A read-only presence check is all this script needs.
if grep -Eq '^(SUBSTACK_COOKIES|SUBSTACK_COOKIES_PATH)=.+' .env 2>/dev/null; then
  substack --date "$DAY" publish --api --schedule
  PUBLISHED="Draft is scheduled on Substack (not published) — open the draft URL above for a last look."
else
  echo "SUBSTACK_COOKIES / SUBSTACK_COOKIES_PATH not set in .env: falling back to the clipboard path."
  substack --date "$DAY" publish
  PUBLISHED="Paste the issue into the new-post page."
fi
git add tested-daily out/work out/*.md data/adopted.csv data/pipeline.sqlite
git commit -qm "issue $DAY" && git push --quiet || true
echo "Done. $PUBLISHED Then post the 3 Notes from out/$DAY-notes.md across the day."
