"""Toil signals from GitHub Actions, fetched via the `gh` CLI.

Two signals:
  - ci_rerun:         a workflow run with run_attempt > 1 (someone babysat CI)
  - manual_dispatch:  a workflow_dispatch run (someone pushed a button)

Requires an authenticated `gh` and a GitHub remote. Failures are reported
back as a reason string so the caller can degrade gracefully.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .git_signals import WEIGHTS


def scan(repo_path, days_back=30):
    """Return (events, skip_reason). skip_reason is None on success."""
    repo_path = Path(repo_path).resolve()
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    try:
        result = subprocess.run(
            [
                "gh", "api", f"repos/{{owner}}/{{repo}}/actions/runs?created=>={since}",
                "--paginate",
                "--jq", ".workflow_runs[] | {id, name, event, run_attempt, conclusion, created_at}",
            ],
            cwd=repo_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except FileNotFoundError:
        return [], "gh CLI not installed"
    except subprocess.TimeoutExpired:
        return [], "gh API call timed out"
    if result.returncode != 0:
        reason = (result.stderr or "gh api failed").strip().splitlines()[0]
        return [], reason

    events = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        run = json.loads(line)
        date = run["created_at"][:10]
        if run.get("run_attempt", 1) > 1:
            extra_attempts = run["run_attempt"] - 1
            events.append({
                "signal": "ci_rerun",
                "ref": str(run["id"]),
                "date": date,
                "author": None,
                "description": f"{run['name']} re-run (attempt {run['run_attempt']})",
                "out_of_hours": False,
                "minutes": WEIGHTS["ci_rerun"] * extra_attempts,
                "detail": {"attempts": run["run_attempt"], "conclusion": run.get("conclusion")},
            })
        if run.get("event") == "workflow_dispatch":
            events.append({
                "signal": "manual_dispatch",
                "ref": str(run["id"]),
                "date": date,
                "author": None,
                "description": f"{run['name']} triggered manually",
                "out_of_hours": False,
                "minutes": WEIGHTS["manual_dispatch"],
                "detail": {"conclusion": run.get("conclusion")},
            })
    return events, None
