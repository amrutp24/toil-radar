# Changelog

All notable changes to Toil Radar will be documented in this file.

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

## [Unreleased]

### 🚀 Planned Features
- Integration with CI/CD platforms
- More detection patterns
- Enhanced visualizations
- API for integrations