# Changelog

All notable changes to Toil Radar will be documented in this file.

## [0.4.0] - 2026-08-10

### Added
- `scan` takes any number of repository paths, so `toil-radar scan ~/code/*`
  works. A path that isn't a git repo is reported and skipped rather than
  aborting the run; the exit code is non-zero if any path failed
- `broken_main` episodes record the commit their first failing run was built
  from, and `summary` uses that to report which files keep breaking the default
  branch. Commits outside the scanned history don't contribute rather than being
  guessed at from a time window

### Fixed
- A repository with no commits yet no longer fails the scan with a raw `git log`
  error - it reports zero commits and moves on, which matters now that one
  invocation can cover a whole directory of repos

### Added
- New `broken_main` signal: the default branch going red and being fixed forward.
  Consecutive failed runs of one workflow collapse into a single episode ending
  at the next green run, costed by how long the branch stayed red (floored at
  10 minutes, capped at 2 hours) and amplified x1.5 out of hours. An episode
  that hasn't recovered yet isn't counted until it does
- `scan` now resolves the repo's default branch via `gh`; if that lookup fails
  the other GitHub signals still work

### Fixed
- `ci_rerun` and `manual_dispatch` hardcoded `out_of_hours: False`, so a 3am
  re-run was weighted the same as a Tuesday-afternoon one. Both now use the
  x1.5 multiplier like every other signal, which raises reported toil for teams
  whose CI babysitting happens at night
- Those two signals also dated events by the UTC calendar day, putting a late
  local evening on the following day. They now use local time, matching the git
  signals and `broken_main`
- Event `detail` is stored and read back as structured data rather than being
  discarded on read

## [0.2.0] - 2026-07-08

### Changed (breaking)
- Detection engine rewritten around structural signals instead of commit-message
  keywords: `revert`, `hotfix_merge`, `quick_fix` (same author, same file within
  2h), plus GitHub Actions `ci_rerun` and `manual_dispatch` via the `gh` CLI
- Keyword matching removed — "fix"/"deploy"/"config" in a message no longer
  counts as toil (it flagged most normal development work)
- New database schema: events carry an estimated cost in minutes and are
  deduplicated by commit hash / run id, so rescans never double-count
- Out-of-hours (nights/weekends) work amplifies event weight ×1.5 instead of
  being a standalone signal
- `summary` now reports estimated toil hours, trend vs the prior period, file
  churn hotspots, and ranked automation candidates with recommendations
- CLI and dashboard share one database (previously `toil.db` vs `demo.db`)
- streamlit/pandas/plotly moved to the optional `[dashboard]` extra — the core
  CLI now has zero dependencies
- `ToilDetector` class removed; public API is `toil_radar.scan_git`,
  `toil_radar.scan_github`, `toil_radar.summarize`

## [0.1.0] - 2026-02-10

### ✨ Features
- Initial CLI tool for toil detection
- Git history scanning for 5 toil pattern types
- SQLite database for tracking and trends
- Cross-platform support (Windows/Linux/Mac)
- Web dashboard with Streamlit visualizations
- Severity assessment (HIGH/MEDIUM/LOW)
- Installable via PyPI-style setup

### 🐛 Bug Fixes
- Fixed unicode issues on Windows
- Added proper path handling
- Resolved import errors in CLI

### 🔧 Development
- Added unit tests with pytest
- GitHub Actions CI/CD pipeline
- Cross-platform testing matrix
- Professional documentation and license

### 📦 Installation
```bash
pip install toil-radar
```