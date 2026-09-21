#!/usr/bin/env bash
# Auto-generated quickstart for pola-rs/polars. Edit freely; this file is the published receipt.
set -euo pipefail
git clone --depth 1 https://github.com/pola-rs/polars repo
cd repo
if [ -f pyproject.toml ] || [ -f setup.py ]; then
  pip install -q . 2>&1 | tail -n 5 || pip install -q -e . 2>&1 | tail -n 5
elif [ -f requirements.txt ]; then
  pip install -q -r requirements.txt 2>&1 | tail -n 5
elif [ -f package.json ]; then
  echo "node project: install node in the image or edit this script"; exit 2
fi
# --- your 2-minute test goes below this line ---
python -c "print('import smoke test: edit run.sh to exercise the main claim')"
