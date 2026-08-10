"""Aggregate stored toil events into an actionable summary."""

from collections import Counter, defaultdict

from . import store

RECOMMENDATIONS = {
    "revert": "Bad changes are reaching mainline - strengthen pre-merge checks "
              "(integration tests, canary deploys, feature flags).",
    "hotfix_merge": "Frequent hotfix branches point to release-process gaps - "
                    "ship smaller changes more often and gate risky ones behind flags.",
    "quick_fix": "Fix-the-fix churn means feedback arrives too late - add the "
                 "missing linter, type check, or test that would catch these pre-push.",
    "ci_rerun": "Re-run workflows usually mean flaky tests or flaky infra - "
                "quarantine the flakiest jobs and fix them; every re-run is babysitting.",
    "manual_dispatch": "Manually triggered workflows are standing automation "
                       "candidates - wire them to events, schedules, or merges.",
    "broken_main": "Breakage is landing on the default branch and being fixed "
                   "forward under pressure - gate merges on the checks that "
                   "failed, so the build breaks in a PR instead of on main.",
    "page": "Pages are the most expensive toil there is - someone dropped "
            "everything. Tune or delete the alerts that don't need a human, "
            "and automate the runbook for the ones that do.",
}

SIGNAL_LABELS = {
    "revert": "reverts / rollbacks",
    "hotfix_merge": "hotfix branch merges",
    "quick_fix": "rapid follow-up fixes",
    "ci_rerun": "CI re-runs",
    "manual_dispatch": "manual workflow triggers",
    "broken_main": "broken default branch",
    "page": "incident pages",
}


def summarize(conn, days=30, repo=None):
    events = store.events_in_window(conn, days, repo)
    by_signal = defaultdict(lambda: {"count": 0, "minutes": 0.0, "examples": []})
    total_minutes = 0.0
    out_of_hours = 0
    for e in events:
        s = by_signal[e["signal"]]
        s["count"] += 1
        s["minutes"] += e["minutes"]
        if len(s["examples"]) < 3:
            s["examples"].append(e)
        total_minutes += e["minutes"]
        out_of_hours += e["out_of_hours"]

    prior_minutes = store.minutes_in_prior_window(conn, days, repo)
    trend_pct = None
    if prior_minutes > 0:
        trend_pct = (total_minutes - prior_minutes) / prior_minutes * 100

    # One engineer's working capacity over the window (40h week averaged over 7 days).
    capacity_hours = days * 40 / 7
    return {
        "days": days,
        "repo": repo,
        "events": events,
        "by_signal": dict(by_signal),
        "total_minutes": total_minutes,
        "total_hours": total_minutes / 60,
        "capacity_pct": (total_minutes / 60) / capacity_hours * 100 if capacity_hours else 0,
        "out_of_hours": out_of_hours,
        "prior_minutes": prior_minutes,
        "trend_pct": trend_pct,
        "hotspots": store.file_hotspots(conn, days, repo),
        "candidates": _candidates(by_signal),
        "breakage": _breakage_sources(conn, events),
    }


def _breakage_sources(conn, events, top=5):
    """Files touched by the commits that put the default branch red.

    Each broken_main episode records the commit its first failing run was built
    from, so this is the actual culprit rather than everything that happened to
    land in the same window. Commits outside the scanned history just don't
    contribute.
    """
    by_repo = defaultdict(set)
    for e in events:
        if e["signal"] != "broken_main":
            continue
        sha = (e.get("detail") or {}).get("broken_by")
        if sha:
            by_repo[e["repo"]].add(sha)

    episodes = sum(len(shas) for shas in by_repo.values())
    counts = Counter()
    for repo, shas in by_repo.items():
        for files in store.files_for_commits(conn, repo, shas).values():
            for f in set(files):
                counts[f] += 1
    return {"episodes": episodes, "files": counts.most_common(top)}


def _candidates(by_signal):
    ranked = sorted(by_signal.items(), key=lambda kv: kv[1]["minutes"], reverse=True)
    out = []
    for signal, data in ranked:
        if data["count"] == 0:
            continue
        out.append({
            "signal": signal,
            "label": SIGNAL_LABELS.get(signal, signal),
            "count": data["count"],
            "hours": data["minutes"] / 60,
            "recommendation": RECOMMENDATIONS.get(signal, ""),
            "example": data["examples"][0] if data["examples"] else None,
        })
    return out


def render_text(summary):
    """Render a summary dict as terminal-friendly text."""
    lines = []
    scope = summary["repo"] or "all repos"
    lines.append(f"Toil Radar - last {summary['days']} days - {scope}")
    lines.append("=" * 60)

    if not summary["events"]:
        lines.append("No toil events recorded. Run 'toil-radar scan <repo>' first.")
        return "\n".join(lines)

    trend = ""
    if summary["trend_pct"] is not None:
        arrow = "up" if summary["trend_pct"] >= 0 else "down"
        trend = f"   trend vs prior {summary['days']}d: {arrow} {abs(summary['trend_pct']):.0f}%"
    lines.append(
        f"Estimated toil: {summary['total_hours']:.1f}h "
        f"(~{summary['capacity_pct']:.0f}% of one engineer's time){trend}"
    )
    if summary["out_of_hours"]:
        lines.append(f"Out-of-hours events: {summary['out_of_hours']} (nights/weekends)")
    lines.append("")

    lines.append(f"{'signal':<28}{'events':>8}{'est. hours':>12}")
    lines.append("-" * 48)
    for signal, data in sorted(summary["by_signal"].items(), key=lambda kv: -kv[1]["minutes"]):
        label = SIGNAL_LABELS.get(signal, signal)
        lines.append(f"{label:<28}{data['count']:>8}{data['minutes'] / 60:>12.1f}")

    if summary["hotspots"]:
        lines.append("")
        lines.append("Churn hotspots (same file touched in many commits):")
        for f, n in summary["hotspots"]:
            lines.append(f"  {f}  ({n} commits)")

    breakage = summary.get("breakage") or {}
    if breakage.get("files"):
        total = breakage["episodes"]
        lines.append("")
        lines.append("What broke the default branch:")
        for f, n in breakage["files"]:
            lines.append(f"  {f}  ({n} of {total} episodes)")

    if summary["candidates"]:
        lines.append("")
        lines.append("Top automation candidates:")
        for i, c in enumerate(summary["candidates"][:3], 1):
            lines.append(f"{i}. {c['label']}: {c['count']} events, ~{c['hours']:.1f}h")
            lines.append(f"   {c['recommendation']}")
            if c["example"]:
                ex = c["example"]
                lines.append(f"   e.g. \"{ex['description']}\" ({ex['date']})")

    return "\n".join(lines)
