#!/usr/bin/env python3
"""toil-radar CLI: scan git repos for structural toil signals and report."""

import argparse
import sys
from pathlib import Path

from . import git_signals, github_signals, pagerduty_signals, prometheus, report, store


def scan_repos(repo_paths, days=30, db_path=store.DEFAULT_DB, github=True):
    """Scan several repositories into one database.

    A bad path doesn't abort the run - the rest are still scanned, and the exit
    code reports whether anything failed. That way 'toil-radar scan ~/code/*'
    does the useful thing even when the glob picks up a stray directory.
    """
    if isinstance(repo_paths, (str, Path)):
        repo_paths = [repo_paths]
    many = len(repo_paths) > 1

    failed = 0
    for i, path in enumerate(repo_paths):
        if i:
            print()
        if scan_repo(path, days, db_path, github, show_hint=not many) != 0:
            failed += 1

    if many:
        scanned = len(repo_paths) - failed
        print(f"\n{scanned} of {len(repo_paths)} repositories scanned"
              f"{f', {failed} failed' if failed else ''}.")
        if scanned:
            print(f"Run 'toil-radar summary --db {db_path}' for the full report.")
    return 1 if failed else 0


def scan_repo(repo_path, days=30, db_path=store.DEFAULT_DB, github=True, show_hint=True):
    repo = Path(repo_path)
    if not repo.exists():
        print(f"Error: repository path '{repo_path}' does not exist")
        return 1
    if not (repo / ".git").exists():
        print(f"Error: '{repo_path}' is not a Git repository")
        return 1
    repo_key = str(repo.resolve())

    print(f"Scanning {repo_key} (last {days} days)...")
    try:
        commits, events = git_signals.scan(repo_key, days)
    except Exception as e:
        print(f"Error scanning repository: {e}")
        return 1

    if github:
        gh_events, skip_reason = github_signals.scan(repo_key, days)
        if skip_reason:
            print(f"GitHub Actions signals skipped: {skip_reason}")
        else:
            events += gh_events

    conn = store.connect(db_path)
    store.save_commits(conn, repo_key, commits)
    new = store.save_events(conn, repo_key, events)
    conn.close()

    print(f"Commits scanned: {len(commits)}")
    print(f"Toil events: {len(events)} found, {new} new")
    if events and show_hint:
        print(f"\nRun 'toil-radar summary --db {db_path}' for the full report.")
    return 0


def ingest_pages(days=30, db_path=store.DEFAULT_DB, path=None):
    """Pull incidents from PagerDuty (or a JSON export) into the same database.

    Pages are stored under their service name rather than a repo path, so they
    show up in 'summary' alongside git and CI toil and can be filtered with
    --repo <service>.
    """
    print(f"Reading incidents from {path or 'the PagerDuty API'} (last {days} days)...")
    events, skip_reason = pagerduty_signals.scan(days_back=days, path=path)
    if skip_reason:
        print(f"Error: incident ingestion failed: {skip_reason}")
        if "PAGERDUTY_TOKEN" in skip_reason:
            print("Set a read-only PagerDuty API token in PAGERDUTY_TOKEN, or pass --file.")
        return 1

    conn = store.connect(db_path)
    new = store.save_events(conn, "pagerduty", events)
    conn.close()

    print(f"Incidents in window: {len(events)} found, {new} new")
    if events:
        print(f"\nRun 'toil-radar summary --db {db_path}' for the full report.")
    return 0


def export_metrics(db_path=store.DEFAULT_DB, days=30, repo=None, output=None):
    """Write stored toil metrics in Prometheus text format."""
    conn = store.connect(db_path)
    if repo:
        repo = str(Path(repo).resolve()) if Path(repo).exists() else repo
    try:
        if output:
            prometheus.write(conn, output, days=days, repo=repo)
            print(f"Wrote {output}")
        else:
            text = prometheus.render(conn, days=days, repo=repo)
            # Write bytes so Windows doesn't translate \n to \r\n on the way
            # out - Prometheus rejects CRLF, and 'export > toil.prom' is the
            # obvious way to use this.
            try:
                sys.stdout.buffer.write(text.encode("utf-8"))
                sys.stdout.buffer.flush()
            except AttributeError:      # a wrapped stdout (pytest capture)
                sys.stdout.write(text)
    except OSError as e:
        print(f"Error: could not write metrics: {e}")
        return 1
    finally:
        conn.close()
    return 0


def show_summary(db_path=store.DEFAULT_DB, days=30, repo=None):
    conn = store.connect(db_path)
    if repo:
        repo = str(Path(repo).resolve()) if Path(repo).exists() else repo
    summary = report.summarize(conn, days=days, repo=repo)
    conn.close()
    print(report.render_text(summary))
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="toil-radar",
        description="Detect and quantify SRE/DevOps toil from git history and GitHub Actions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  toil-radar scan /path/to/repo
  toil-radar scan . --days 60 --no-github
  toil-radar scan ~/code/*
  toil-radar summary
  toil-radar summary --repo /path/to/repo --days 90
  toil-radar export --output /var/lib/node_exporter/toil.prom
        """,
    )
    subparsers = parser.add_subparsers(
        dest="command", metavar="{scan,summary,export}", help="Available commands"
    )

    scan_parser = subparsers.add_parser("scan", help="Scan a repository for toil signals")
    scan_parser.add_argument(
        "repo_path", nargs="+", metavar="REPO",
        help="Path to a Git repository (repeat, or glob, to scan several)",
    )
    scan_parser.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    scan_parser.add_argument("--db", default=store.DEFAULT_DB, help=f"Database file (default: {store.DEFAULT_DB})")
    scan_parser.add_argument("--no-github", action="store_true", help="Skip GitHub Actions signals")

    # Hidden until it has run against a live PagerDuty account. The code works
    # against JSON exports and a mock, but an undocumented command in --help is
    # still a promise, so it stays out of the listing until we can back it up.
    # argparse.SUPPRESS doesn't work on subparsers - it prints the literal
    # string. Omitting help= keeps it out of the listing; the metavar above
    # keeps it out of the usage line.
    pages_parser = subparsers.add_parser(
        "pages",
        description="Unvalidated against a live PagerDuty account, and not a "
                    "supported feature yet. Reads the API token from the "
                    "PAGERDUTY_TOKEN environment variable; use --file to load a "
                    "JSON export instead.",
    )
    pages_parser.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    pages_parser.add_argument("--db", default=store.DEFAULT_DB, help=f"Database file (default: {store.DEFAULT_DB})")
    pages_parser.add_argument("--file", help="Read incidents from a JSON export instead of the API")

    export_parser = subparsers.add_parser(
        "export",
        help="Export stored metrics for Prometheus",
        description="Write stored toil metrics in Prometheus text format. With "
                    "--output, writes atomically so the node_exporter textfile "
                    "collector never scrapes a half-written file.",
    )
    export_parser.add_argument("--format", default="prometheus", choices=["prometheus"],
                               help="Output format (default: prometheus)")
    export_parser.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    export_parser.add_argument("--db", default=store.DEFAULT_DB, help=f"Database file (default: {store.DEFAULT_DB})")
    export_parser.add_argument("--repo", help="Limit to one repository (path)")
    export_parser.add_argument("--output", help="Write to this file instead of stdout")

    summary_parser = subparsers.add_parser("summary", help="Show toil summary and automation candidates")
    summary_parser.add_argument("--days", type=int, default=30, help="Days to look back (default: 30)")
    summary_parser.add_argument("--db", default=store.DEFAULT_DB, help=f"Database file (default: {store.DEFAULT_DB})")
    summary_parser.add_argument("--repo", help="Limit to one repository (path)")

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()
    if args.command == "scan":
        return scan_repos(args.repo_path, args.days, args.db, github=not args.no_github)
    elif args.command == "pages":
        return ingest_pages(args.days, args.db, path=args.file)
    elif args.command == "export":
        return export_metrics(args.db, args.days, args.repo, args.output)
    elif args.command == "summary":
        return show_summary(args.db, args.days, args.repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
