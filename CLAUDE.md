# toil-radar

CLI that estimates SRE/DevOps toil from git history and GitHub Actions.

Detection must stay structural. Never add commit-message keyword matching as a
standalone signal — that's what 0.1.x did and it flagged half of normal
development. Keywords are only acceptable as a qualifier on top of a structural
condition (see CORRECTIVE_RE in toil_radar/git_signals.py).

## Commands

- tests: `pytest`
- dev install: `pip install -e ".[dashboard,dev]"`
- run without installing: `python -m toil_radar.cli scan . --no-github`

## Constraints

- The core package has zero runtime dependencies and must stay that way.
  streamlit/pandas/plotly live only under the `[dashboard]` extra and are
  imported lazily.
- Python 3.9+ support: no match statements, no `X | Y` union syntax, and
  `datetime.fromisoformat` can't parse a trailing 'Z' before 3.11
  (see Commit.authored_dt).
- CI tests on Windows and macOS too — keep subprocess and path handling
  portable, and keep CLI output plain ASCII (Unicode dashes garble on
  Windows consoles).
- Version comes from git tags via setuptools-scm; never hardcode it.

## Docs and releases

- Public-facing text (README, release notes) in plain maintainer voice —
  no bold-label bullet templates, no over-explaining.
- Pushing a `v*` tag publishes to PyPI automatically; the GitHub release is
  created manually. Write release notes for first-time visitors, not just
  upgraders.
