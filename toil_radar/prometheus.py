"""Prometheus text-format export of stored toil metrics.

Written for the node_exporter textfile collector: point `--output` at a .prom
file in the collector's directory and let it be scraped, or just pipe the
output somewhere. Everything here is string formatting against the standard
exposition format - no client library, no dependency.

Values are in seconds because Prometheus convention is base units, even though
the CLI reports minutes and hours.
"""

import os
from collections import defaultdict

from . import store

PREFIX = "toil_radar"

# One engineer's working capacity: a 40h week averaged over 7 days.
_CAPACITY_HOURS_PER_DAY = 40 / 7


def _escape(value):
    """Escape a label value per the exposition format.

    Backslashes matter more than they look: repo keys are absolute paths, and
    on Windows they arrive full of them.
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _labels(pairs):
    inner = ",".join('%s="%s"' % (k, _escape(v)) for k, v in pairs)
    return "{%s}" % inner if inner else ""


def _number(value):
    """Render a float without trailing noise, keeping it parseable."""
    if value == int(value):
        return str(int(value))
    return repr(round(float(value), 3))


def render(conn, days=30, repo=None):
    """Return the metrics for the reporting window as exposition-format text."""
    events = store.events_in_window(conn, days, repo)

    by_repo_signal = defaultdict(lambda: {"events": 0, "seconds": 0.0})
    out_of_hours = defaultdict(int)
    repos = set()
    total_minutes = 0.0
    for e in events:
        key = (e["repo"], e["signal"])
        by_repo_signal[key]["events"] += 1
        by_repo_signal[key]["seconds"] += e["minutes"] * 60
        repos.add(e["repo"])
        out_of_hours[e["repo"]] += 1 if e["out_of_hours"] else 0
        total_minutes += e["minutes"]

    capacity_hours = days * _CAPACITY_HOURS_PER_DAY
    ratio = (total_minutes / 60) / capacity_hours if capacity_hours else 0.0

    lines = []

    def metric(name, help_text, samples):
        # A metric with no samples is omitted entirely rather than emitted as a
        # bare HELP/TYPE pair, which some parsers dislike.
        if not samples:
            return
        lines.append("# HELP %s_%s %s" % (PREFIX, name, help_text))
        lines.append("# TYPE %s_%s gauge" % (PREFIX, name))
        for label_pairs, value in samples:
            lines.append("%s_%s%s %s" % (PREFIX, name, _labels(label_pairs), _number(value)))

    ordered = sorted(by_repo_signal.items())
    metric(
        "events",
        "Toil events recorded in the reporting window.",
        [([("repo", r), ("signal", s)], d["events"]) for (r, s), d in ordered],
    )
    metric(
        "seconds",
        "Estimated time lost to toil in the reporting window.",
        [([("repo", r), ("signal", s)], d["seconds"]) for (r, s), d in ordered],
    )
    # Named to avoid the tokens "hours" and "days": promtool's linter reads them
    # as units and wants base units instead, even where the value is a count.
    metric(
        "night_weekend_events",
        "Toil events that started at night or on a weekend.",
        [([("repo", r)], out_of_hours[r]) for r in sorted(repos)],
    )
    metric(
        "capacity_ratio",
        "Estimated toil as a fraction of one engineer's working time.",
        [([], ratio)],
    )
    metric(
        "window_seconds",
        "Length of the reporting window.",
        [([], days * 86400)],
    )

    return "\n".join(lines) + "\n"


def write(conn, path, days=30, repo=None):
    """Write the metrics to `path` atomically.

    The textfile collector can scrape at any moment, so the file is written
    alongside and renamed into place rather than truncated and filled.
    """
    text = render(conn, days=days, repo=repo)
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return text
