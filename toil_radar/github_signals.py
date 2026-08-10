"""Toil signals from GitHub Actions, fetched via the `gh` CLI.

Three signals:
  - ci_rerun:         a workflow run with run_attempt > 1 (someone babysat CI)
  - manual_dispatch:  a workflow_dispatch run (someone pushed a button)
  - broken_main:      the default branch went red and someone had to fix forward

broken_main is deliberately not "count every failed run". Consecutive failures
of one workflow on the default branch are collapsed into a single episode
ending at the next green run, because three failed pushes in ten minutes is one
person fixing one mistake, not three separate incidents.

Requires an authenticated `gh` and a GitHub remote. Failures are reported
back as a reason string so the caller can degrade gracefully.
"""

import json
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .git_signals import OUT_OF_HOURS_MULTIPLIER, WEIGHTS, is_out_of_hours

# A red default branch is costed by how long it stayed red, not by a fixed
# weight. Floored because even a fast fix is an interruption, capped because
# nobody is hands-on for the whole of an overnight breakage.
BROKEN_MAIN_MIN_MINUTES = 10
BROKEN_MAIN_MAX_MINUTES = 120


def _parse_dt(value):
    # fromisoformat() rejects a trailing 'Z' before Python 3.11
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _default_branch(repo_path):
    """The repo's default branch, or None if `gh` can't tell us."""
    try:
        result = subprocess.run(
            ["gh", "api", "repos/{owner}/{repo}", "--jq", ".default_branch"],
            cwd=repo_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def detect_broken_main(runs, default_branch):
    """Collapse consecutive failed default-branch runs into red-main episodes.

    An episode opens on the first failure and closes on the next success of the
    same workflow; its cost is how long the branch stayed red. Only closed
    episodes are reported - while the branch is still red the clock is running,
    and re-reporting a growing estimate under the same id would never overwrite
    the row already stored.
    """
    if not default_branch:
        return []

    by_workflow = defaultdict(list)
    for run in runs:
        if run.get("head_branch") != default_branch:
            continue
        # cancelled / skipped / still running aren't verdicts on the branch
        if run.get("conclusion") not in ("success", "failure"):
            continue
        by_workflow[run.get("name")].append(run)

    events = []
    for name, workflow_runs in by_workflow.items():
        workflow_runs.sort(key=lambda r: r["created_at"])
        episode = []
        for run in workflow_runs:
            if run["conclusion"] == "failure":
                episode.append(run)
            elif episode:
                events.append(_broken_main_event(name, default_branch, episode, run))
                episode = []
    return events


def _broken_main_event(name, branch, episode, recovery):
    first = episode[0]
    started = _parse_dt(first["created_at"])
    red_minutes = (_parse_dt(recovery["created_at"]) - started).total_seconds() / 60
    minutes = max(BROKEN_MAIN_MIN_MINUTES, min(BROKEN_MAIN_MAX_MINUTES, red_minutes))

    local = started.astimezone()
    ooh = is_out_of_hours(local)
    if ooh:
        minutes *= OUT_OF_HOURS_MULTIPLIER

    failures = len(episode)
    return {
        "signal": "broken_main",
        "ref": str(first["id"]),
        "date": local.date().isoformat(),
        "author": None,
        "description": "{} red on {} for {} min ({} failed run{})".format(
            name, branch, round(red_minutes), failures, "" if failures == 1 else "s"
        ),
        "out_of_hours": ooh,
        "minutes": round(minutes, 1),
        "detail": {
            "workflow": name,
            "branch": branch,
            "failed_runs": failures,
            "red_minutes": round(red_minutes, 1),
            "recovered_by": str(recovery["id"]),
            # The run API hands us the exact commit each run was built from, so
            # the culprit doesn't have to be guessed from a time window.
            "broken_by": first.get("head_sha"),
            "fixed_by": recovery.get("head_sha"),
        },
    }


def scan(repo_path, days_back=30):
    """Return (events, skip_reason). skip_reason is None on success."""
    repo_path = Path(repo_path).resolve()
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    try:
        result = subprocess.run(
            [
                "gh", "api", f"repos/{{owner}}/{{repo}}/actions/runs?created=>={since}",
                "--paginate",
                "--jq", ".workflow_runs[] | {id, name, event, run_attempt, conclusion,"
                        " created_at, head_branch, head_sha}",
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
    runs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        run = json.loads(line)
        runs.append(run)
        # Local time, like every other signal: a 3am re-run is someone's night,
        # and the UTC calendar date can land on the wrong day either way.
        started = _parse_dt(run["created_at"]).astimezone()
        date = started.date().isoformat()
        ooh = is_out_of_hours(started)
        amplifier = OUT_OF_HOURS_MULTIPLIER if ooh else 1
        if run.get("run_attempt", 1) > 1:
            extra_attempts = run["run_attempt"] - 1
            events.append({
                "signal": "ci_rerun",
                "ref": str(run["id"]),
                "date": date,
                "author": None,
                "description": f"{run['name']} re-run (attempt {run['run_attempt']})",
                "out_of_hours": ooh,
                "minutes": WEIGHTS["ci_rerun"] * extra_attempts * amplifier,
                "detail": {"attempts": run["run_attempt"], "conclusion": run.get("conclusion")},
            })
        if run.get("event") == "workflow_dispatch":
            events.append({
                "signal": "manual_dispatch",
                "ref": str(run["id"]),
                "date": date,
                "author": None,
                "description": f"{run['name']} triggered manually",
                "out_of_hours": ooh,
                "minutes": WEIGHTS["manual_dispatch"] * amplifier,
                "detail": {"conclusion": run.get("conclusion")},
            })

    # A repo with no default branch we can resolve still gets the other signals.
    events += detect_broken_main(runs, _default_branch(repo_path))
    return events, None
