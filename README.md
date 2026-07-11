# toil-radar

Estimates how much time a team loses to toil — reverts, hotfixes, flaky CI, manual button-pushing — from signals already sitting in git history and GitHub Actions. Point it at a repo and get back estimated hours, a trend, and a ranked list of what to automate first.

The SRE handbook says to keep toil under 50% of engineering time. Almost nobody measures it, and grepping commit messages for "fix" doesn't work — it flags half of normal development. So toil-radar only counts events that don't happen unless someone was cleaning up a mess:

| signal | what happened |
|---|---|
| `revert` | a revert or rollback commit |
| `hotfix_merge` | a merge from a hotfix or emergency branch |
| `quick_fix` | the same author patched the same file again within 2 hours, with a corrective message |
| `ci_rerun` | a workflow run needed more than one attempt |
| `manual_dispatch` | someone triggered a workflow by hand |

The git signals work offline. The two GitHub Actions signals use the `gh` CLI if it's installed and authenticated, and are skipped quietly if not.

Every event gets a rough cost in minutes (weights are in [`toil_radar/git_signals.py`](toil_radar/git_signals.py), deliberately conservative), counted 1.5x if it happened on a night or weekend. The total isn't meant to be payroll-accurate — it's a consistent number you can trend over time and use to decide what to automate first. Rescanning never double-counts: events are deduplicated by commit hash or run id.

## Install

```bash
pip install toil-radar               # CLI, no dependencies
pip install "toil-radar[dashboard]"  # adds the Streamlit dashboard
```

## Usage

```bash
toil-radar scan /path/to/repo        # add --no-github to skip the gh API
toil-radar scan . --days 60
toil-radar summary
toil-radar summary --repo /path/to/repo --days 90
toil-dashboard
```

Scans from multiple repos accumulate in one database; `summary` aggregates across all of them unless you filter with `--repo`.

## Example

Real output from scanning this repo:

```
Toil Radar - last 120 days - all repos
============================================================
Estimated toil: 3.5h (~1% of one engineer's time)
Out-of-hours events: 7 (nights/weekends)

signal                        events  est. hours
------------------------------------------------
rapid follow-up fixes              9         3.1
CI re-runs                         1         0.3

Churn hotspots (same file touched in many commits):
  pyproject.toml  (10 commits)
  setup.py  (4 commits)

Top automation candidates:
1. rapid follow-up fixes: 9 events, ~3.1h
   Fix-the-fix churn means feedback arrives too late - add the missing
   linter, type check, or test that would catch these pre-push.
   e.g. "fix: explicitly include only toil_radar package in build" (2026-04-09)
2. CI re-runs: 1 events, ~0.3h
   Re-run workflows usually mean flaky tests or flaky infra - quarantine
   the flakiest jobs and fix them; every re-run is babysitting.
   e.g. "Publish to PyPI re-run (attempt 3)" (2026-03-30)
```

The verdict on our own history was fair: the packaging churn in April really was toil, and that PyPI publish really did take three attempts.

## Development

```bash
git clone https://github.com/amrutp24/toil-radar
cd toil-radar
pip install -e ".[dashboard,dev]"
pytest
```

## Roadmap

- PagerDuty / Opsgenie ingestion — repeated alerts are the strongest toil signal there is
- deploy→fix→deploy loop detection
- per-team and per-author views
- Prometheus metrics export for long-term trending

## License

MIT — see [LICENSE](LICENSE)
