"""Tests for toil-radar structural signal detection, storage, and reporting."""

import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from toil_radar import git_signals, github_signals, pagerduty_signals, report, store
from toil_radar.cli import ingest_pages, scan_repo, show_summary


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


def recent_weekend_night(days_ago=1, hour=23):
    """A recent timestamp guaranteed to be out-of-hours (Saturday night)."""
    dt = datetime.now() - timedelta(days=days_ago)
    while dt.weekday() != 5:
        dt -= timedelta(days=1)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


def pd_incident(inc_id, created_local, resolved_after=None, status="resolved",
                service="checkout-api", title="High error rate"):
    """Build a PagerDuty incident payload from a naive local datetime."""
    created = created_local.astimezone(timezone.utc)
    inc = {
        "id": str(inc_id),
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "urgency": "high",
        "title": title,
        "service": {"summary": service},
    }
    if resolved_after is not None:
        changed = created + timedelta(minutes=resolved_after)
        inc["last_status_change_at"] = changed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return inc


class _FakeResponse(io.BytesIO):
    """Minimal stand-in for the object urlopen() yields as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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


def gh_run(run_id, name="CI", conclusion="success", branch="main",
           local_time=None, event="push", attempt=1):
    """A workflow run as the API reports it, from a naive *local* timestamp."""
    local_time = local_time or weekday_working_hours(days_ago=2)
    return {
        "id": run_id,
        "name": name,
        "event": event,
        "run_attempt": attempt,
        "conclusion": conclusion,
        "created_at": local_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "head_branch": branch,
    }


def test_github_rerun_and_dispatch_events():
    runs = [
        json.dumps({"id": 1, "name": "e2e", "event": "push", "run_attempt": 3,
                    "conclusion": "success", "created_at": "2026-07-01T10:00:00Z"}),
        json.dumps({"id": 2, "name": "deploy", "event": "workflow_dispatch", "run_attempt": 1,
                    "conclusion": "success", "created_at": "2026-07-02T10:00:00Z"}),
        json.dumps({"id": 3, "name": "ci", "event": "push", "run_attempt": 1,
                    "conclusion": "success", "created_at": "2026-07-03T10:00:00Z"}),
    ]
    with patch("toil_radar.github_signals.subprocess.run",
               side_effect=[_gh_result(runs), _gh_result(["main"])]):
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


# --- broken default branch ---------------------------------------------------

def test_broken_main_groups_consecutive_failures_into_one_episode():
    # three fix-forward pushes and a green run is one person fixing one mistake
    t = weekday_working_hours(days_ago=2)
    runs = [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="failure", local_time=t + timedelta(minutes=7)),
        gh_run(3, conclusion="failure", local_time=t + timedelta(minutes=9)),
        gh_run(4, conclusion="success", local_time=t + timedelta(minutes=14)),
    ]
    events = github_signals.detect_broken_main(runs, "main")
    assert len(events) == 1
    assert events[0]["signal"] == "broken_main"
    assert events[0]["ref"] == "1"                      # dedups on the first failure
    assert events[0]["detail"]["failed_runs"] == 3
    assert events[0]["detail"]["red_minutes"] == 14
    assert events[0]["detail"]["recovered_by"] == "4"
    assert events[0]["minutes"] == 14                   # inside the clamps
    assert "3 failed runs" in events[0]["description"]


def test_green_default_branch_produces_nothing():
    t = weekday_working_hours(days_ago=2)
    runs = [gh_run(i, local_time=t + timedelta(hours=i)) for i in range(4)]
    assert github_signals.detect_broken_main(runs, "main") == []


def test_failures_on_other_branches_are_not_broken_main():
    # a red feature branch is normal development, not toil
    t = weekday_working_hours(days_ago=2)
    runs = [
        gh_run(1, conclusion="failure", branch="feature/new-parser", local_time=t),
        gh_run(2, conclusion="failure", branch="feature/new-parser",
               local_time=t + timedelta(minutes=10)),
        gh_run(3, conclusion="success", branch="feature/new-parser",
               local_time=t + timedelta(minutes=20)),
    ]
    assert github_signals.detect_broken_main(runs, "main") == []


def test_still_red_branch_is_not_reported_yet():
    # no green run to close the episode: the clock is still running
    t = weekday_working_hours(days_ago=1)
    runs = [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="failure", local_time=t + timedelta(minutes=10)),
    ]
    assert github_signals.detect_broken_main(runs, "main") == []


def test_separate_breakages_are_separate_episodes():
    t = weekday_working_hours(days_ago=5)
    runs = [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="success", local_time=t + timedelta(minutes=20)),
        gh_run(3, conclusion="failure", local_time=t + timedelta(days=1)),
        gh_run(4, conclusion="success", local_time=t + timedelta(days=1, minutes=30)),
    ]
    events = github_signals.detect_broken_main(runs, "main")
    assert [e["ref"] for e in events] == ["1", "3"]


def test_different_workflows_do_not_merge_into_one_episode():
    t = weekday_working_hours(days_ago=2)
    runs = [
        gh_run(1, name="CI", conclusion="failure", local_time=t),
        gh_run(2, name="Publish", conclusion="failure", local_time=t + timedelta(minutes=1)),
        gh_run(3, name="CI", conclusion="success", local_time=t + timedelta(minutes=20)),
        gh_run(4, name="Publish", conclusion="success", local_time=t + timedelta(minutes=25)),
    ]
    events = github_signals.detect_broken_main(runs, "main")
    assert sorted(e["detail"]["workflow"] for e in events) == ["CI", "Publish"]
    assert all(e["detail"]["failed_runs"] == 1 for e in events)


def test_cancelled_runs_neither_open_nor_close_an_episode():
    t = weekday_working_hours(days_ago=2)
    runs = [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="cancelled", local_time=t + timedelta(minutes=5)),
        gh_run(3, conclusion=None, local_time=t + timedelta(minutes=6)),  # still running
        gh_run(4, conclusion="failure", local_time=t + timedelta(minutes=8)),
        gh_run(5, conclusion="success", local_time=t + timedelta(minutes=30)),
    ]
    events = github_signals.detect_broken_main(runs, "main")
    assert len(events) == 1
    assert events[0]["detail"]["failed_runs"] == 2
    assert events[0]["detail"]["red_minutes"] == 30


def test_broken_main_duration_is_clamped_at_both_ends():
    t = weekday_working_hours(days_ago=3, hour=9)
    quick = [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="success", local_time=t + timedelta(minutes=2)),
    ]
    overnight = [
        gh_run(3, name="Slow", conclusion="failure", local_time=t),
        gh_run(4, name="Slow", conclusion="success", local_time=t + timedelta(hours=9)),
    ]
    assert github_signals.detect_broken_main(quick, "main")[0]["minutes"] == \
        github_signals.BROKEN_MAIN_MIN_MINUTES
    assert github_signals.detect_broken_main(overnight, "main")[0]["minutes"] == \
        github_signals.BROKEN_MAIN_MAX_MINUTES


def test_broken_main_out_of_hours_is_amplified():
    t = recent_weekend_night()
    runs = [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="success", local_time=t + timedelta(minutes=40)),
    ]
    event = github_signals.detect_broken_main(runs, "main")[0]
    assert event["out_of_hours"] is True
    assert event["minutes"] == 40 * git_signals.OUT_OF_HOURS_MULTIPLIER


def test_broken_main_skipped_when_default_branch_is_unknown():
    t = weekday_working_hours(days_ago=2)
    runs = [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="success", local_time=t + timedelta(minutes=20)),
    ]
    assert github_signals.detect_broken_main(runs, None) == []


def test_scan_wires_broken_main_through_the_gh_calls():
    t = weekday_working_hours(days_ago=2)
    runs = [json.dumps(r) for r in [
        gh_run(1, conclusion="failure", local_time=t),
        gh_run(2, conclusion="success", local_time=t + timedelta(minutes=20)),
    ]]
    with patch("toil_radar.github_signals.subprocess.run",
               side_effect=[_gh_result(runs), _gh_result(["main"])]):
        events, reason = github_signals.scan(".", days_back=30)
    assert reason is None
    assert [e["signal"] for e in events] == ["broken_main"]


def test_scan_keeps_other_signals_when_default_branch_lookup_fails():
    runs = [json.dumps(gh_run(1, conclusion="failure", local_time=weekday_working_hours(),
                              event="workflow_dispatch"))]
    failed = SimpleNamespace(returncode=1, stdout="", stderr="not found")
    with patch("toil_radar.github_signals.subprocess.run",
               side_effect=[_gh_result(runs), failed]):
        events, reason = github_signals.scan(".", days_back=30)
    assert reason is None
    assert [e["signal"] for e in events] == ["manual_dispatch"]


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


# --- pagerduty signals -------------------------------------------------------

def test_page_costs_actual_time_to_resolve():
    inc = pd_incident(1, weekday_working_hours(days_ago=2), resolved_after=37)
    events = pagerduty_signals.to_events([inc])
    assert len(events) == 1
    assert events[0]["signal"] == "page"
    assert events[0]["minutes"] == 37
    assert events[0]["out_of_hours"] is False


def test_unresolved_page_gets_default_cost():
    inc = pd_incident(2, weekday_working_hours(days_ago=1), status="triggered")
    events = pagerduty_signals.to_events([inc])
    assert events[0]["minutes"] == pagerduty_signals.DEFAULT_MINUTES


def test_page_duration_is_clamped_at_both_ends():
    brief = pd_incident(3, weekday_working_hours(days_ago=2), resolved_after=1)
    marathon = pd_incident(4, weekday_working_hours(days_ago=2), resolved_after=60 * 20)
    events = pagerduty_signals.to_events([brief, marathon])
    assert events[0]["minutes"] == pagerduty_signals.MIN_MINUTES
    assert events[1]["minutes"] == pagerduty_signals.MAX_MINUTES


def test_out_of_hours_page_is_amplified():
    inc = pd_incident(5, recent_weekend_night(), resolved_after=20)
    events = pagerduty_signals.to_events([inc])
    assert events[0]["out_of_hours"] is True
    assert events[0]["minutes"] == 20 * git_signals.OUT_OF_HOURS_MULTIPLIER


def test_page_is_attributed_to_its_service():
    inc = pd_incident(6, weekday_working_hours(), resolved_after=10, service="payments")
    events = pagerduty_signals.to_events([inc])
    assert events[0]["repo"] == "payments"
    assert events[0]["detail"]["service"] == "payments"
    assert events[0]["detail"]["urgency"] == "high"


def test_malformed_incidents_are_skipped_not_guessed():
    good = pd_incident(7, weekday_working_hours(), resolved_after=15)
    junk = [
        {},                                        # no timestamps at all
        {"id": "x", "created_at": None},           # null timestamp
        {"id": "y", "created_at": "not-a-date"},   # unparseable
        "a bare string",                           # not an object
        good,
    ]
    events = pagerduty_signals.to_events(junk)
    assert [e["ref"] for e in events] == ["7"]


def test_incidents_without_an_id_are_skipped():
    # ref is the dedup key; without an id they would all collide as "None"
    a = pd_incident(80, weekday_working_hours(), resolved_after=10)
    b = pd_incident(81, weekday_working_hours(), resolved_after=20)
    del a["id"], b["id"]
    assert pagerduty_signals.to_events([a, b]) == []


def test_unparseable_resolve_time_falls_back_to_default():
    inc = pd_incident(8, weekday_working_hours(), resolved_after=15)
    inc["last_status_change_at"] = "garbage"
    events = pagerduty_signals.to_events([inc])
    assert events[0]["minutes"] == pagerduty_signals.DEFAULT_MINUTES


def test_resolve_time_before_creation_falls_back_to_default():
    inc = pd_incident(9, weekday_working_hours(), resolved_after=-30)
    events = pagerduty_signals.to_events([inc])
    assert events[0]["minutes"] == pagerduty_signals.DEFAULT_MINUTES


def test_scan_ignores_incidents_older_than_the_window(tmp_path):
    old = pd_incident(10, weekday_working_hours(days_ago=200), resolved_after=15)
    recent = pd_incident(11, weekday_working_hours(days_ago=2), resolved_after=15)
    export = tmp_path / "incidents.json"
    export.write_text(json.dumps({"incidents": [old, recent]}), encoding="utf-8")
    events, reason = pagerduty_signals.scan(days_back=30, path=str(export))
    assert reason is None
    assert [e["ref"] for e in events] == ["11"]


def test_scan_reads_bare_list_export(tmp_path):
    export = tmp_path / "incidents.json"
    export.write_text(
        json.dumps([pd_incident(12, weekday_working_hours(), resolved_after=15)]),
        encoding="utf-8",
    )
    events, reason = pagerduty_signals.scan(days_back=30, path=str(export))
    assert reason is None and len(events) == 1


def test_scan_missing_file_degrades_gracefully(tmp_path):
    events, reason = pagerduty_signals.scan(path=str(tmp_path / "nope.json"))
    assert events == []
    assert "could not read" in reason


def test_scan_invalid_json_degrades_gracefully(tmp_path):
    export = tmp_path / "incidents.json"
    export.write_text("{not json", encoding="utf-8")
    events, reason = pagerduty_signals.scan(path=str(export))
    assert events == []
    assert "not valid JSON" in reason


def test_scan_without_token_degrades_gracefully(monkeypatch):
    monkeypatch.delenv("PAGERDUTY_TOKEN", raising=False)
    events, reason = pagerduty_signals.scan(days_back=30)
    assert events == []
    assert "PAGERDUTY_TOKEN" in reason


def test_fetch_incidents_follows_pagination():
    inc_a = pd_incident(20, weekday_working_hours(), resolved_after=10)
    inc_b = pd_incident(21, weekday_working_hours(), resolved_after=10)
    pages = [
        {"incidents": [inc_a], "more": True, "limit": 1},
        {"incidents": [inc_b], "more": False},
    ]
    urls = []

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        assert req.get_header("Authorization").startswith("Token token=")
        return _FakeResponse(json.dumps(pages[len(urls) - 1]).encode())

    with patch("toil_radar.pagerduty_signals.urllib.request.urlopen", fake_urlopen):
        incidents = pagerduty_signals.fetch_incidents("placeholder", days_back=30)

    assert [i["id"] for i in incidents] == ["20", "21"]
    assert len(urls) == 2
    assert "offset=0" in urls[0] and "offset=1" in urls[1]


def test_fetch_incidents_against_a_real_socket(monkeypatch):
    """End-to-end over HTTP: nobody here has a PagerDuty account, so this stands
    in for one. Exercises the real urllib request, auth header, and paging loop
    against a local server speaking the /incidents response shape."""
    pages = [
        {"incidents": [pd_incident(60, weekday_working_hours(), resolved_after=10)],
         "limit": 1, "more": True},
        {"incidents": [pd_incident(61, weekday_working_hours(), resolved_after=10)],
         "limit": 1, "more": False},
    ]
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            seen.append((parsed.path, query, self.headers.get("Authorization")))
            offset = int(query.get("offset", "0"))
            body = json.dumps(pages[offset] if offset < len(pages) else
                              {"incidents": [], "more": False}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("PAGERDUTY_API_BASE", "http://127.0.0.1:%d" % server.server_address[1])
        events, reason = pagerduty_signals.scan(days_back=30, token="placeholder")
    finally:
        server.shutdown()
        server.server_close()

    assert reason is None
    assert [e["ref"] for e in events] == ["60", "61"]
    assert [path for path, _, _ in seen] == ["/incidents", "/incidents"]
    assert [q["offset"] for _, q, _ in seen] == ["0", "1"]  # follows the server's page size
    assert all(auth == "Token token=placeholder" for _, _, auth in seen)


def test_fetch_incidents_honours_api_base_override(monkeypatch):
    monkeypatch.setenv("PAGERDUTY_API_BASE", "https://eu.example.test/")
    urls = []

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        return _FakeResponse(json.dumps({"incidents": [], "more": False}).encode())

    with patch("toil_radar.pagerduty_signals.urllib.request.urlopen", fake_urlopen):
        pagerduty_signals.fetch_incidents("placeholder", days_back=7)

    assert urls[0].startswith("https://eu.example.test/incidents?")


# --- pages ingestion end to end ----------------------------------------------

def test_pages_ingest_stores_under_service_and_dedups(tmp_path, db, capsys):
    export = tmp_path / "incidents.json"
    export.write_text(json.dumps({"incidents": [
        pd_incident(30, weekday_working_hours(days_ago=2), resolved_after=45, service="payments"),
        pd_incident(31, recent_weekend_night(), resolved_after=90, service="checkout-api"),
    ]}), encoding="utf-8")

    assert ingest_pages(days=30, db_path=db, path=str(export)) == 0
    assert ingest_pages(days=30, db_path=db, path=str(export)) == 0
    capsys.readouterr()

    conn = store.connect(db)
    rows = conn.execute("SELECT repo, signal FROM toil_events ORDER BY repo").fetchall()
    conn.close()
    assert rows == [("checkout-api", "page"), ("payments", "page")]


def test_pages_ingest_reports_failure(db, tmp_path, capsys):
    assert ingest_pages(days=30, db_path=db, path=str(tmp_path / "missing.json")) == 1
    assert "ingestion failed" in capsys.readouterr().out


def test_summary_includes_pages(tmp_path, db, capsys):
    export = tmp_path / "incidents.json"
    export.write_text(json.dumps([
        pd_incident(40, weekday_working_hours(days_ago=2), resolved_after=45,
                    service="payments", title="Checkout 5xx spike"),
    ]), encoding="utf-8")
    ingest_pages(days=30, db_path=db, path=str(export))
    capsys.readouterr()

    show_summary(db_path=db, days=30)
    out = capsys.readouterr().out
    assert "incident pages" in out
    assert "Checkout 5xx spike" in out


def test_summary_can_filter_to_one_service(tmp_path, db):
    export = tmp_path / "incidents.json"
    export.write_text(json.dumps([
        pd_incident(50, weekday_working_hours(days_ago=2), resolved_after=45, service="payments"),
        pd_incident(51, weekday_working_hours(days_ago=2), resolved_after=45, service="search"),
    ]), encoding="utf-8")
    ingest_pages(days=30, db_path=db, path=str(export))

    conn = store.connect(db)
    summary = report.summarize(conn, days=30, repo="payments")
    conn.close()
    assert summary["by_signal"]["page"]["count"] == 1


def test_stored_event_detail_round_trips_as_a_dict(db):
    conn = store.connect(db)
    today = datetime.now().date().isoformat()
    store.save_events(conn, "/r", [
        {"signal": "quick_fix", "ref": "aaa", "date": today, "minutes": 15,
         "detail": {"file": "app.py", "gap_minutes": 25}},
        {"signal": "revert", "ref": "bbb", "date": today, "minutes": 45},
    ])
    events = {e["ref"]: e for e in store.events_in_window(conn, days=30)}
    conn.close()
    assert events["aaa"]["detail"] == {"file": "app.py", "gap_minutes": 25}
    assert events["bbb"]["detail"] == {}
