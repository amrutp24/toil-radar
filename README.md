# Toil Radar

📡 **Detect, quantify, and reduce SRE/DevOps toil** — from signals your team already produces.

Toil is manual, repetitive, automatable operational work. The SRE handbook says to keep it under 50% of engineering time — but almost nobody measures it. Toil Radar estimates it from **structural signals** in git history and GitHub Actions, not keyword guessing, and tells you what to automate first.

## What it detects

**From git history (works offline):**

| Signal | What it means |
|---|---|
| `revert` | Reverts and rollbacks — bad changes reached mainline |
| `hotfix_merge` | Merges from hotfix/emergency branches |
| `quick_fix` | Same author re-touching the same file within 2 hours *with a corrective message* — fix-the-fix churn, not normal iteration |

Out-of-hours work (nights/weekends) doesn't create noise as its own signal — it **amplifies** the weight of real events, because a Saturday-night rollback costs more than a Tuesday-morning one.

**From GitHub Actions (via `gh`, optional):**

| Signal | What it means |
|---|---|
| `ci_rerun` | Workflow runs with `run_attempt > 1` — someone babysat flaky CI |
| `manual_dispatch` | `workflow_dispatch` runs — someone pushed a button that could be automated |

Every event gets an estimated cost in minutes. Rescans never double-count — events are deduplicated by commit hash / run id.

## Install

```bash
pip install toil-radar               # CLI only, zero dependencies
pip install "toil-radar[dashboard]"  # + Streamlit dashboard
```

## Usage

```bash
# Scan a repository (add --no-github to skip the gh API)
toil-radar scan /path/to/repo
toil-radar scan . --days 60

# Report: estimated hours, trend, hotspots, automation candidates
toil-radar summary
toil-radar summary --repo /path/to/repo --days 90

# Web dashboard
toil-dashboard
```

You can scan multiple repos into the same database — `summary` aggregates across all of them, or filter with `--repo`.

## Example output

```
Toil Radar - last 120 days - all repos
============================================================
Estimated toil: 6.2h (~1% of one engineer's time)   trend vs prior 120d: up 24%
Out-of-hours events: 13 (nights/weekends)

signal                        events  est. hours
------------------------------------------------
rapid follow-up fixes             17         5.9
CI re-runs                         1         0.3

Churn hotspots (same file touched in many commits):
  pyproject.toml  (10 commits)
  setup.py  (4 commits)

Top automation candidates:
1. rapid follow-up fixes: 17 events, ~5.9h
   Fix-the-fix churn means feedback arrives too late - add the missing
   linter, type check, or test that would catch these pre-push.
   e.g. "fix: explicitly include only toil_radar package in build" (2026-04-09)
2. CI re-runs: 1 events, ~0.3h
   Re-run workflows usually mean flaky tests or flaky infra - quarantine
   the flakiest jobs and fix them; every re-run is babysitting.
   e.g. "Publish to PyPI re-run (attempt 3)" (2026-03-30)
```

(That's real output from scanning this repo. The packaging churn was, in fact, toil.)

## Why structural signals instead of keywords?

Grepping commit messages for "fix" or "deploy" flags most normal development work and makes the numbers meaningless. Structural signals — an actual `Revert "..."` commit, a merge from a `hotfix/*` branch, the same file patched twice in 30 minutes, a CI run re-attempted three times — are things that *only* happen when somebody was doing operational cleanup. Low noise, defensible numbers.

## How the cost estimate works

Each signal type has a conservative per-event weight (revert 45 min, hotfix merge 60 min, quick fix 15 min, CI re-run 10 min per extra attempt, manual dispatch 10 min), multiplied ×1.5 out of hours. See `WEIGHTS` in [`toil_radar/git_signals.py`](toil_radar/git_signals.py). The point isn't per-minute accuracy — it's a consistent metric you can trend over time and use to rank what to automate first.

## Development

```bash
git clone https://github.com/amrutp24/toil-radar
cd toil-radar
pip install -e ".[dashboard,dev]"
pytest
```

## Roadmap

- PagerDuty / Opsgenie ingestion (repeated alerts = the strongest toil signal)
- Deploy→fix→deploy loop detection
- Per-team / per-author views
- Export to Prometheus metrics for long-term trending

## License

MIT — see [LICENSE](LICENSE)
