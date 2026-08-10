"""Structural toil signals extracted from git history.

Signals are structural rather than keyword-based: reverts, hotfix branch
merges, and rapid fix-the-fix churn. Out-of-hours work amplifies an event's
weight instead of being a standalone (and noisy) signal.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# Estimated interruption cost per event, in minutes. Deliberately conservative.
WEIGHTS = {
    "revert": 45,
    "hotfix_merge": 60,
    "quick_fix": 15,
    "ci_rerun": 10,
    "manual_dispatch": 10,
}

OUT_OF_HOURS_MULTIPLIER = 1.5
QUICK_FIX_WINDOW = timedelta(hours=2)

REVERT_RE = re.compile(r'^Revert\b|^revert\b|^rollback\b|\broll(ed|ing)? back\b', re.IGNORECASE)
HOTFIX_MERGE_RE = re.compile(r"\b(hotfix|emergency)\b", re.IGNORECASE)
# A rapid same-file follow-up only counts as toil when the message sounds
# corrective; otherwise it's just normal iterative development.
CORRECTIVE_RE = re.compile(
    r"\b(fix(es|ed)?|hotfix|typo|oops|whoops|broken|breaks?|wrong|mistake|"
    r"forgot(ten)?|missing|missed|correct(s|ed)?|repair(s|ed)?|actually|again)\b",
    re.IGNORECASE,
)

_RS, _US = "\x1e", "\x1f"


@dataclass
class Commit:
    hash: str
    parents: List[str]
    author: str
    authored_at: str  # ISO 8601 with UTC offset
    subject: str
    files: List[str] = field(default_factory=list)

    @property
    def authored_dt(self):
        # fromisoformat() rejects a trailing 'Z' before Python 3.11
        return datetime.fromisoformat(self.authored_at.replace("Z", "+00:00"))


def _has_commits(repo_path):
    """False for a repo that's been init'd but never committed to."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode == 0


def read_commits(repo_path, days_back=30):
    """Read commits from the last `days_back` days, oldest first."""
    repo_path = Path(repo_path).resolve()
    fmt = f"{_RS}%H{_US}%P{_US}%an{_US}%aI{_US}%s"
    result = subprocess.run(
        [
            "git", "-C", str(repo_path), "log",
            f"--since={days_back} days ago",
            f"--pretty=format:{fmt}", "--name-only",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        # An empty repo isn't worth failing a multi-repo scan over. Checking
        # HEAD rather than matching git's wording keeps this locale-independent,
        # and it only runs on the failure path.
        if not _has_commits(repo_path):
            return []
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    commits = []
    for record in result.stdout.split(_RS):
        if not record.strip():
            continue
        header, _, body = record.partition("\n")
        commit_hash, parents, author, authored_at, subject = header.split(_US)
        files = [line.strip() for line in body.splitlines() if line.strip()]
        commits.append(Commit(
            hash=commit_hash,
            parents=parents.split(),
            author=author,
            authored_at=authored_at,
            subject=subject,
            files=files,
        ))
    commits.reverse()
    return commits


def is_out_of_hours(dt):
    """Weekend, or before 07:00 / after 20:00 in the author's local time."""
    return dt.weekday() >= 5 or dt.hour < 7 or dt.hour >= 20


def _event(signal, commit, detail=None):
    dt = commit.authored_dt
    ooh = is_out_of_hours(dt)
    minutes = WEIGHTS[signal] * (OUT_OF_HOURS_MULTIPLIER if ooh else 1)
    return {
        "signal": signal,
        "ref": commit.hash,
        "date": dt.date().isoformat(),
        "author": commit.author,
        "description": commit.subject,
        "out_of_hours": ooh,
        "minutes": minutes,
        "detail": detail or {},
    }


def detect(commits):
    """Detect toil events in a list of commits (oldest first)."""
    events = []
    flagged = set()

    for c in commits:
        if REVERT_RE.search(c.subject):
            events.append(_event("revert", c))
            flagged.add(c.hash)
        elif len(c.parents) >= 2 and HOTFIX_MERGE_RE.search(c.subject):
            events.append(_event("hotfix_merge", c))
            flagged.add(c.hash)

    # quick_fix: same author touches the same file again within a short window,
    # with a corrective-sounding message
    last_touch = {}  # (author, file) -> (datetime, hash)
    for c in commits:
        dt = c.authored_dt
        for f in c.files:
            prev = last_touch.get((c.author, f))
            if (
                prev
                and c.hash not in flagged
                and timedelta(0) < dt - prev[0] <= QUICK_FIX_WINDOW
                and CORRECTIVE_RE.search(c.subject)
            ):
                gap = round((dt - prev[0]).total_seconds() / 60)
                events.append(_event("quick_fix", c, {
                    "file": f, "follows": prev[1], "gap_minutes": gap,
                }))
                flagged.add(c.hash)
            last_touch[(c.author, f)] = (dt, c.hash)

    return events


def scan(repo_path, days_back=30):
    """Convenience wrapper: read commits and detect events."""
    commits = read_commits(repo_path, days_back)
    return commits, detect(commits)
