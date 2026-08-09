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
| `broken_main` | the default branch went red and someone fixed it forward |

The git signals work offline. The three GitHub Actions signals use the `gh` CLI if it's installed and authenticated, and are skipped quietly if not.

`broken_main` counts episodes, not failures. Consecutive failed runs of one workflow on the default branch collapse into a single event that ends at the next green run — three failed pushes in ten minutes is one person fixing one mistake, not three incidents. It's costed by how long the branch stayed red (floored at 10 minutes, capped at 2 hours), and an episode still red at scan time isn't counted until it recovers.

Every other event gets a rough cost in minutes (weights are in [`toil_radar/git_signals.py`](toil_radar/git_signals.py), deliberately conservative). Anything that started on a night or weekend counts 1.5x. The total isn't meant to be payroll-accurate — it's a consistent number you can trend over time and use to decide what to automate first. Rescanning never double-counts: events are deduplicated by commit hash or run id.

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
Toil Radar - last 180 days - all repos
============================================================
Estimated toil: 6.1h (~1% of one engineer's time)
Out-of-hours events: 12 (nights/weekends)

signal                        events  est. hours
------------------------------------------------
rapid follow-up fixes             12         4.1
broken default branch              4         1.6
CI re-runs                         1         0.3

Churn hotspots (same file touched in many commits):
  pyproject.toml  (12 commits)
  README.md  (7 commits)
  tests/test_basic.py  (7 commits)
  setup.py  (5 commits)

Top automation candidates:
1. rapid follow-up fixes: 12 events, ~4.1h
   Fix-the-fix churn means feedback arrives too late - add the missing
   linter, type check, or test that would catch these pre-push.
   e.g. "Fix author date parsing on Python < 3.11 (trailing Z)" (2026-07-09)
2. broken default branch: 4 events, ~1.6h
   Breakage is landing on the default branch and being fixed forward under
   pressure - gate merges on the checks that failed, so the build breaks in
   a PR instead of on main.
   e.g. "CI red on main for 15 min (1 failed run)" (2026-07-09)
3. CI re-runs: 1 events, ~0.3h
   Re-run workflows usually mean flaky tests or flaky infra - quarantine
   the flakiest jobs and fix them; every re-run is babysitting.
   e.g. "Publish to PyPI re-run (attempt 3)" (2026-03-30)
```

The verdict on our own history was fair. The packaging churn in April really was toil, that PyPI publish really did take three attempts, and all four red-main episodes were real — twelve failed runs on `main` that resolved into four separate scrambles, three of them after hours.

## Development

```bash
git clone https://github.com/amrutp24/toil-radar
cd toil-radar
pip install -e ".[dashboard,dev]"
pytest
```

## Roadmap

- incident ingestion (PagerDuty, Opsgenie) — repeated alerts are the strongest toil signal there is. There's unadvertised groundwork for this in `toil_radar/pagerduty_signals.py`, but nobody here has an account to validate it against, so it stays off the feature list until someone does
- deploy→fix→deploy loop detection
- per-team and per-author views
- Prometheus metrics export for long-term trending

## License

MIT — see [LICENSE](LICENSE)
