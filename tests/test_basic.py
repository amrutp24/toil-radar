"""Tests for toil-radar structural signal detection, storage, and reporting."""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from toil_radar import git_signals, github_signals, report, store
from toil_radar.cli import scan_repo, show_summary


# --- helpers ---------------------------------------------------------------

def make_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)


def make_commit(path, filename, message, when=None, author=None):
    """Create/append a file and commit it, optionally at a specific time."""
    f = Path(path) / filename
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fh.write(message + "\n")
    env = os.environ.copy()
    if when:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    if author:
        env["GIT_AUTHOR_NAME"] = author
        env["GIT_AUTHOR_EMAIL"] = f"{author}@example.com"
    subprocess.run(["git", "add", filename], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True, env=env)


def weekday_working_hours(days_ago=1, hour=11):
    """A recent timestamp guaranteed to be in-hours (weekday, 11am)."""
    dt = datetime.now() - timedelta(days=days_ago)
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture
def repo(tmp_path):
    make_git_repo(tmp_path)
    return tmp_path


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


# --- git signal detection ----------------------------------------------------

def test_clean_history_has_no_events(repo):
    t = weekday_working_hours(days_ago=3)
    make_commit(repo, "a.py", "add feature A", when=t)
    make_commit(repo, "b.py", "add feature B", when=t + timedelta(hours=5))
    _, events = git_signals.scan(repo, days_back=30)
    assert events == []


def test_normal_fix_commits_are_not_flagged(repo):
    # "fix" in a message is normal development, not toil
    make_commit(repo, "a.py", "fix typo in docstring", when=weekday_working_hours())
    _, events = git_signals.scan(repo, days_back=30)
    assert events == []


def test_detects_git_revert_style_commit(repo):
    make_commit(repo, "a.py", 'Revert "add feature A"', when=weekday_working_hours())
    _, events = git_signals.scan(repo, days_back=30)
    assert [e["signal"] for e in events] == ["revert"]


def test_detects_rollback_commit(repo):
    make_commit(repo, "a.py", "rollback bad release", when=weekday_working_hours())
    _, events = git_signals.scan(repo, days_back=30)
    assert [e["signal"] for e in events] == ["revert"]


def test_detects_hotfix_merge(repo):
    t = weekday_working_hours(days_ago=2)
    make_commit(repo, "a.py", "initial", when=t)
    subprocess.run(["git", "checkout", "-q", "-b", "hotfix/db-outage"], cwd=repo, check=True)
    make_commit(repo, "b.py", "patch connection pool", when=t + timedelta(hours=1))
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
    env = os.environ.copy()
    stamp = (t + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(
        ["git", "merge", "--no-ff", "-q", "-m", "Merge branch 'hotfix/db-outage'", "hotfix/db-outage"],
        cwd=repo, check=True, env=env,
    )
    _, events = git_signals.scan(repo, days_back=30)
    assert "hotfix_merge" in [e["signal"] for e in events]


def test_detects_quick_fix_same_file_same_author(repo):
    t = weekday_working_hours(days_ago=2)
    make_commit(repo, "app.py", "add endpoint", when=t)
    make_commit(repo, "app.py", "fix missing header check", when=t + timedelta(minutes=25))
    _, events = git_signals.scan(repo, days_back=30)
    quick = [e for e in events if e["signal"] == "quick_fix"]
    assert len(quick) == 1
    assert quick[0]["detail"]["file"] == "app.py"
    assert quick[0]["detail"]["gap_minutes"] == 25


def test_no_quick_fix_outside_window(repo):
    t = weekday_working_hours(days_ago=3)
    make_commit(repo, "app.py", "add endpoint", when=t)
    make_commit(repo, "app.py", "fix endpoint validation", when=t + timedelta(hours=5))
    _, events = git_signals.scan(repo, days_back=30)
    assert [e for e in events if e["signal"] == "quick_fix"] == []


def test_no_quick_fix_for_normal_iteration(repo):
    # rapid same-file commits without a corrective message = dev session, not toil
    t = weekday_working_hours(days_ago=2)
    make_commit(repo, "app.py", "add endpoint", when=t)
    make_commit(repo, "app.py", "add pagination to endpoint", when=t + timedelta(minutes=20))
    make_commit(repo, "app.py", "document endpoint params", when=t + timedelta(minutes=40))
    _, events = git_signals.scan(repo, days_back=30)
    assert events == []


def test_no_quick_fix_for_different_authors(repo):
    t = weekday_working_hours(days_ago=2)
    make_commit(repo, "app.py", "add endpoint", when=t, author="alice")
    make_commit(repo, "app.py", "fix review comment", when=t + timedelta(minutes=20), author="bob")
    _, events = git_signals.scan(repo, days_back=30)
    assert [e for e in events if e["signal"] == "quick_fix"] == []


def test_reverted_commit_not_double_flagged_as_quick_fix(repo):
    t = weekday_working_hours(days_ago=2)
    make_commit(repo, "app.py", "add endpoint", when=t)
    make_commit(repo, "app.py", 'Revert "add endpoint"', when=t + timedelta(minutes=10))
    _, events = git_signals.scan(repo, days_back=30)
    assert [e["signal"] for e in events] == ["revert"]


# --- out-of-hours ------------------------------------------------------------

def test_out_of_hours_detection():
    assert git_signals.is_out_of_hours(datetime(2026, 7, 4, 23, 0))   # Saturday
    assert git_signals.is_out_of_hours(datetime(2026, 7, 8, 2, 30))   # Wednesday 2:30am
    assert not git_signals.is_out_of_hours(datetime(2026, 7, 8, 11, 0))


def test_out_of_hours_amplifies_minutes(repo):
    dt = datetime.now() - timedelta(days=1)
    # force a Saturday within the lookback
    while dt.weekday() != 5:
        dt -= timedelta(days=1)
    make_commit(repo, "a.py", "rollback broken deploy", when=dt.replace(hour=23, minute=0))
    _, events = git_signals.scan(repo, days_back=30)
    assert events[0]["out_of_hours"] is True
    expected = git_signals.WEIGHTS["revert"] * git_signals.OUT_OF_HOURS_MULTIPLIER
    assert events[0]["minutes"] == expected


# --- github signals (mocked) -------------------------------------------------

def _gh_result(lines):
    return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")

def test_github_rerun_and_dispatch_events():
    runs = [
        json.dumps({"id": 1, "name": "e2e", "event": "push", "run_attempt": 3,
                    "conclusion": "success", "created_at": "2026-07-01T10:00:00Z"}),
        json.dumps({"id": 2, "name": "deploy", "event": "workflow_dispatch", "run_attempt": 1,
                    "conclusion": "success", "created_at": "2026-07-02T10:00:00Z"}),
        json.dumps({"id": 3, "name": "ci", "event": "push", "run_attempt": 1,
                    "conclusion": "success", "created_at": "2026-07-03T10:00:00Z"}),
    ]
    with patch("toil_radar.github_signals.subprocess.run", return_value=_gh_result(runs)):
        events, reason = github_signals.scan(".", days_back=30)
    assert reason is None
    signals = sorted(e["signal"] for e in events)
    assert signals == ["ci_rerun", "manual_dispatch"]
    rerun = next(e for e in events if e["signal"] == "ci_rerun")
    assert rerun["minutes"] == git_signals.WEIGHTS["ci_rerun"] * 2  # 2 extra attempts


def test_github_missing_gh_degrades_gracefully():
    with patch("toil_radar.github_signals.subprocess.run", side_effect=FileNotFoundError):
        events, reason = github_signals.scan(".", days_back=30)
    assert events == []
    assert "gh" in reason


# --- store / dedup -----------------------------------------------------------

def test_rescan_does_not_double_count(repo, db):
    make_commit(repo, "a.py", "rollback bad release", when=weekday_working_hours())
    assert scan_repo(str(repo), days=30, db_path=db, github=False) == 0
    assert scan_repo(str(repo), days=30, db_path=db, github=False) == 0
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM toil_events").fetchone()[0]
    conn.close()
    assert count == 1


def test_scan_stores_commits_for_hotspots(repo, db):
    t = weekday_working_hours(days_ago=2)
    for i in range(5):
        make_commit(repo, "config.yaml", f"change {i}", when=t + timedelta(hours=3 * i))
    scan_repo(str(repo), days=30, db_path=db, github=False)
    conn = store.connect(db)
    hotspots = store.file_hotspots(conn, days=30)
    conn.close()
    assert hotspots and hotspots[0][0] == "config.yaml"
    assert hotspots[0][1] == 5


def test_scan_invalid_path_returns_error(db):
    assert scan_repo("/nonexistent/path", days=30, db_path=db) == 1


def test_scan_non_git_directory_returns_error(tmp_path, db):
    assert scan_repo(str(tmp_path), days=30, db_path=db) == 1


# --- report ------------------------------------------------------------------

def test_summary_empty_db(db, capsys):
    show_summary(db_path=db, days=30)
    assert "No toil events" in capsys.readouterr().out


def test_summary_after_scan(repo, db, capsys):
    make_commit(repo, "a.py", 'Revert "bad change"', when=weekday_working_hours())
    scan_repo(str(repo), days=30, db_path=db, github=False)
    show_summary(db_path=db, days=30)
    out = capsys.readouterr().out
    assert "Estimated toil" in out
    assert "reverts / rollbacks" in out
    assert "automation candidates" in out


def test_summary_math(db):
    conn = store.connect(db)
    today = datetime.now().date().isoformat()
    store.save_events(conn, "/r", [
        {"signal": "revert", "ref": "aaa", "date": today, "minutes": 45},
        {"signal": "ci_rerun", "ref": "111", "date": today, "minutes": 20},
    ])
    summary = report.summarize(conn, days=30)
    conn.close()
    assert summary["total_minutes"] == 65
    assert summary["by_signal"]["revert"]["count"] == 1
    assert summary["candidates"][0]["signal"] == "revert"  # ranked by minutes


def test_trend_vs_prior_period(db):
    conn = store.connect(db)
    now = datetime.now().date()
    recent = now.isoformat()
    prior = (now - timedelta(days=35)).isoformat()
    store.save_events(conn, "/r", [
        {"signal": "revert", "ref": "old", "date": prior, "minutes": 60},
        {"signal": "revert", "ref": "new1", "date": recent, "minutes": 45},
        {"signal": "revert", "ref": "new2", "date": recent, "minutes": 45},
    ])
    summary = report.summarize(conn, days=30)
    conn.close()
    assert summary["prior_minutes"] == 60
    assert summary["trend_pct"] == pytest.approx(50.0)
