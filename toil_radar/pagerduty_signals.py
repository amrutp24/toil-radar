"""Incident ingestion from PagerDuty (or any JSON export in the same shape).

Two sources:
  - fetch_incidents(): pages through the PagerDuty REST API using a read-only
    token. Set PAGERDUTY_API_BASE to point somewhere else (a mock server, a
    regional endpoint).
  - load_incidents_file(): reads a JSON file containing either the API
    response shape ({"incidents": [...]}) or a bare list of incidents.

Both feed to_events(), which turns incidents into toil events. Cost is the
actual time-to-resolve (capped), not a guessed weight; unresolved incidents
get a default. Pages that fired out of hours are weighted like any other
out-of-hours toil - they interrupted someone's evening or sleep.

scan() picks a source and degrades to a (events, skip_reason) pair the same
way github_signals.scan() does.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .git_signals import OUT_OF_HOURS_MULTIPLIER, is_out_of_hours

DEFAULT_API_BASE = "https://api.pagerduty.com"
DEFAULT_MINUTES = 30      # unresolved incidents, or ones with unusable timestamps
MIN_MINUTES = 5           # even a one-minute blip costs a context switch
MAX_MINUTES = 240         # long-running incidents aren't 100% hands-on


def _parse_dt(value):
    # fromisoformat() rejects a trailing 'Z' before Python 3.11
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_incidents(token, days_back=30, api_base=None):
    """Fetch incidents from the PagerDuty REST API. Returns a list of dicts."""
    base = (api_base or os.environ.get("PAGERDUTY_API_BASE") or DEFAULT_API_BASE).rstrip("/")
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    incidents = []
    offset = 0
    while True:
        params = urllib.parse.urlencode({
            "since": since, "limit": 100, "offset": offset, "total": "false",
        })
        req = urllib.request.Request(
            f"{base}/incidents?{params}",
            headers={
                "Authorization": f"Token token={token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.load(resp)
        incidents.extend(page.get("incidents", []))
        if not page.get("more"):
            break
        offset += page.get("limit", 100)
    return incidents


def load_incidents_file(path):
    """Read incidents from a JSON export: API response shape or a bare list."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("incidents", [])
    return data


def to_events(incidents):
    """Convert PagerDuty incidents into toil events.

    Incidents that are missing or malformed enough that we can't date them are
    skipped rather than guessed at. Out-of-hours is judged in the local time of
    the machine running the scan - the responder's own timezone isn't in the
    API payload, so this is an approximation for distributed teams.
    """
    events = []
    for inc in incidents:
        if not isinstance(inc, dict):
            continue
        # Dedup keys off the incident id, so an id-less incident can't be stored
        # without silently colliding with every other one from its service.
        if not inc.get("id"):
            continue
        try:
            created = _parse_dt(inc["created_at"]).astimezone()
        except (KeyError, TypeError, ValueError, AttributeError):
            continue

        minutes = DEFAULT_MINUTES
        if inc.get("status") == "resolved" and inc.get("last_status_change_at"):
            try:
                resolved = _parse_dt(inc["last_status_change_at"])
                duration = (resolved - _parse_dt(inc["created_at"])).total_seconds() / 60
                if duration > 0:
                    minutes = max(MIN_MINUTES, min(MAX_MINUTES, duration))
            except (TypeError, ValueError, AttributeError):
                pass

        ooh = is_out_of_hours(created)
        if ooh:
            minutes *= OUT_OF_HOURS_MULTIPLIER

        service = (inc.get("service") or {}).get("summary") or "unknown-service"
        events.append({
            "signal": "page",
            "ref": str(inc.get("id")),
            "date": created.date().isoformat(),
            "author": None,
            "description": inc.get("title") or "(no title)",
            "out_of_hours": ooh,
            "minutes": round(minutes, 1),
            "repo": service,
            "detail": {
                "service": service,
                "urgency": inc.get("urgency"),
                "status": inc.get("status"),
            },
        })
    return events


def scan(days_back=30, token=None, path=None):
    """Return (events, skip_reason). skip_reason is None on success.

    Reads `path` if given, otherwise calls the API with `token` or the
    PAGERDUTY_TOKEN environment variable. The token is never accepted on the
    command line - it would end up in shell history and process listings.
    """
    if path:
        try:
            incidents = load_incidents_file(path)
        except OSError as e:
            return [], f"could not read {path}: {e}"
        except ValueError as e:
            return [], f"{path} is not valid JSON: {e}"
    else:
        token = token or os.environ.get("PAGERDUTY_TOKEN")
        if not token:
            return [], "PAGERDUTY_TOKEN is not set"
        try:
            incidents = fetch_incidents(token, days_back)
        except urllib.error.HTTPError as e:
            return [], f"PagerDuty API returned HTTP {e.code}"
        except urllib.error.URLError as e:
            return [], f"PagerDuty API unreachable: {e.reason}"
        except (OSError, ValueError) as e:
            return [], f"PagerDuty API call failed: {e}"

    if not isinstance(incidents, list):
        return [], "expected a list of incidents"

    # A file export can span years; keep ingestion aligned with --days.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date().isoformat()
    return [e for e in to_events(incidents) if e["date"] >= cutoff], None
