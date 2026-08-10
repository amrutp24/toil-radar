"""SQLite storage for toil-radar scan results."""

import json
import os
import sqlite3
from collections import Counter
from datetime import date, timedelta

DEFAULT_DB = os.environ.get("TOIL_RADAR_DB", "toil.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS commits (
    repo TEXT NOT NULL,
    hash TEXT NOT NULL,
    author TEXT,
    authored_at TEXT,
    subject TEXT,
    files TEXT,
    PRIMARY KEY (repo, hash)
);
CREATE TABLE IF NOT EXISTS toil_events (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    signal TEXT NOT NULL,
    ref TEXT NOT NULL,
    date TEXT NOT NULL,
    author TEXT,
    description TEXT,
    out_of_hours INTEGER DEFAULT 0,
    minutes REAL NOT NULL,
    detail TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE (repo, signal, ref)
);
"""


def connect(db_path=DEFAULT_DB):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def save_commits(conn, repo, commits):
    conn.executemany(
        "INSERT OR REPLACE INTO commits (repo, hash, author, authored_at, subject, files)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (repo, c.hash, c.author, c.authored_at, c.subject, json.dumps(c.files))
            for c in commits
        ],
    )
    conn.commit()


def save_events(conn, repo, events):
    """Insert events, skipping ones already recorded. Returns number of new rows.

    An event may carry its own "repo" key (incident sources use the service
    name), which overrides the repo argument.
    """
    new = 0
    for e in events:
        cur = conn.execute(
            "INSERT OR IGNORE INTO toil_events"
            " (repo, signal, ref, date, author, description, out_of_hours, minutes, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e.get("repo") or repo,
                e["signal"],
                e["ref"],
                e["date"],
                e.get("author"),
                e.get("description"),
                1 if e.get("out_of_hours") else 0,
                e["minutes"],
                json.dumps(e.get("detail", {})),
            ),
        )
        new += cur.rowcount
    conn.commit()
    return new


def _window(days):
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days + 1)
    return start.isoformat(), end.isoformat()


def events_in_window(conn, days, repo=None):
    start, end = _window(days)
    sql = (
        "SELECT repo, signal, ref, date, author, description, out_of_hours, minutes, detail"
        " FROM toil_events WHERE date >= ? AND date < ?"
    )
    params = [start, end]
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    sql += " ORDER BY date DESC"
    cols = ["repo", "signal", "ref", "date", "author", "description", "out_of_hours", "minutes", "detail"]
    events = [dict(zip(cols, row)) for row in conn.execute(sql, params).fetchall()]
    for e in events:
        e["detail"] = json.loads(e["detail"] or "{}")
    return events


def minutes_in_prior_window(conn, days, repo=None):
    """Total toil minutes in the window immediately before the current one."""
    start, _ = _window(days)
    prior_start = (date.fromisoformat(start) - timedelta(days=days)).isoformat()
    sql = "SELECT COALESCE(SUM(minutes), 0) FROM toil_events WHERE date >= ? AND date < ?"
    params = [prior_start, start]
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    return conn.execute(sql, params).fetchone()[0]


def file_hotspots(conn, days, repo=None, min_commits=4, top=5):
    """Files touched in the most distinct commits within the window."""
    start, end = _window(days)
    sql = "SELECT files FROM commits WHERE authored_at >= ? AND authored_at < ?"
    params = [start, end]
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    counts = Counter()
    for (files_json,) in conn.execute(sql, params).fetchall():
        for f in set(json.loads(files_json or "[]")):
            counts[f] += 1
    return [(f, n) for f, n in counts.most_common(top) if n >= min_commits]


def files_for_commits(conn, repo, hashes):
    """Map commit hash -> files touched, for hashes we happen to have stored."""
    hashes = list(hashes)
    if not hashes:
        return {}
    placeholders = ",".join("?" * len(hashes))
    rows = conn.execute(
        "SELECT hash, files FROM commits WHERE repo = ? AND hash IN (%s)" % placeholders,
        [repo] + hashes,
    ).fetchall()
    return {h: json.loads(files or "[]") for h, files in rows}


def repos(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT repo FROM toil_events ORDER BY repo")]
