"""Stage 4: run each candidate's quickstart in a throwaway Docker container and keep the receipt.

Every run writes `tested-daily/YYYY-MM-DD/<owner>__<name>/run.sh` plus `result.json`, so a reader can
re-run exactly what the newsletter claims. If Docker is unavailable the result is recorded as
"not run" and the repo cannot be labelled ADOPTED.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .models import Repo, TestResult

DEFAULT_SCRIPT = """#!/usr/bin/env bash
# Auto-generated quickstart for {full_name}. Edit freely; this file is the published receipt.
set -euo pipefail
git clone --depth 1 {url} repo
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
"""


def script_for(repo: Repo, day_dir: Path) -> Path:
    folder = day_dir / repo.full_name.replace("/", "__")
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "run.sh"
    if not script.exists():
        script.write_text(DEFAULT_SCRIPT.format(full_name=repo.full_name, url=repo.url))
        script.chmod(0o755)
    return script


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def run_test(repo: Repo, day_dir: Path, image: str = "python:3.11-slim", timeout: int = 600) -> TestResult:
    script = script_for(repo, day_dir)
    folder = script.parent
    rel_script = str(script.relative_to(day_dir.parent.parent)) if day_dir.parent.parent in script.parents else str(script)

    if not docker_available():
        res = TestResult(passed=False, duration_s=0.0, command="(docker unavailable)", script_path=rel_script,
                         notes="Docker not available; test not run. Label cannot be ADOPTED.")
        (folder / "result.json").write_text(json.dumps(res.__dict__, indent=2))
        return res

    cmd = ["docker", "run", "--rm", "--network", "bridge", "--memory", "2g", "--cpus", "2",
           "-v", f"{folder.resolve()}:/work:ro", "-w", "/tmp", image,
           "bash", "-c", "apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null && bash /work/run.sh"]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout + "\n" + p.stderr)[-3000:]
        res = TestResult(passed=p.returncode == 0, duration_s=round(time.time() - t0, 1),
                         command=" ".join(cmd[-3:]), stdout_tail=out, script_path=rel_script)
    except subprocess.TimeoutExpired:
        res = TestResult(passed=False, duration_s=float(timeout), command="docker run ...",
                         script_path=rel_script, notes=f"timed out after {timeout}s")
    (folder / "result.json").write_text(json.dumps(res.__dict__, indent=2))
    return res


def runs_out_of_box_score(res: TestResult) -> float:
    if not res.passed:
        return 0.0 if res.command != "(docker unavailable)" else 0.0
    # Faster is better: under 2 min -> 10, 10 min -> 6.
    return round(max(6.0, 10.0 - (res.duration_s / 120.0)), 1)
