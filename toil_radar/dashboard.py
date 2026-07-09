"""Streamlit dashboard for toil-radar.

Requires the dashboard extra: pip install toil-radar[dashboard]
Run with: toil-dashboard  (or: streamlit run toil_radar/dashboard.py)
"""

import sys

try:
    from . import git_signals, github_signals, report, store
except ImportError:
    # streamlit executes this file as a top-level script
    from toil_radar import git_signals, github_signals, report, store


def _require_streamlit():
    try:
        import pandas as pd
        import plotly.express as px
        import streamlit as st
        return st, pd, px
    except ImportError:
        print("Dashboard dependencies missing. Install with: pip install toil-radar[dashboard]")
        sys.exit(1)


def run():
    st, pd, px = _require_streamlit()

    st.set_page_config(page_title="Toil Radar", page_icon="📡", layout="wide")
    st.title("📡 Toil Radar")
    st.markdown("Quantify repetitive operational work — then automate it away.")

    st.sidebar.header("Data")
    db_path = st.sidebar.text_input("Database file", store.DEFAULT_DB)
    days = st.sidebar.slider("Days to analyze", 7, 90, 30)

    conn = store.connect(db_path)
    known_repos = store.repos(conn)
    repo_filter = st.sidebar.selectbox("Repository", ["(all)"] + known_repos)
    repo = None if repo_filter == "(all)" else repo_filter

    st.sidebar.header("Scan")
    scan_path = st.sidebar.text_input("Git repo path", ".")
    use_github = st.sidebar.checkbox("Include GitHub Actions signals", value=True)
    if st.sidebar.button("Scan for toil"):
        with st.spinner("Scanning..."):
            try:
                commits, events = git_signals.scan(scan_path, days)
                if use_github:
                    gh_events, skip = github_signals.scan(scan_path, days)
                    if skip:
                        st.sidebar.info(f"GitHub signals skipped: {skip}")
                    else:
                        events += gh_events
                from pathlib import Path
                repo_key = str(Path(scan_path).resolve())
                store.save_commits(conn, repo_key, commits)
                new = store.save_events(conn, repo_key, events)
                st.sidebar.success(f"{len(events)} events found, {new} new")
            except Exception as e:
                st.sidebar.error(f"Scan failed: {e}")

    summary = report.summarize(conn, days=days, repo=repo)
    conn.close()

    if not summary["events"]:
        st.warning("No toil data yet. Scan a repository from the sidebar to get started.")
        return

    st.header("Key metrics")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Estimated toil", f"{summary['total_hours']:.1f} h")
    col2.metric("Of one engineer's time", f"{summary['capacity_pct']:.0f}%")
    trend = summary["trend_pct"]
    col3.metric(
        "Trend vs prior period",
        f"{trend:+.0f}%" if trend is not None else "n/a",
        delta=f"{trend:+.0f}%" if trend is not None else None,
        delta_color="inverse",
    )
    col4.metric("Out-of-hours events", summary["out_of_hours"])

    df = pd.DataFrame(summary["events"])
    df["hours"] = df["minutes"] / 60
    df["signal"] = df["signal"].map(lambda s: report.SIGNAL_LABELS.get(s, s))

    st.header("Trends")
    col1, col2 = st.columns(2)
    with col1:
        daily = df.groupby("date")["hours"].sum().reset_index()
        st.plotly_chart(
            px.bar(daily, x="date", y="hours", title="Estimated toil hours per day"),
            use_container_width=True,
        )
    with col2:
        by_signal = df.groupby("signal")["hours"].sum().reset_index().sort_values("hours")
        st.plotly_chart(
            px.bar(by_signal, x="hours", y="signal", orientation="h", title="Toil hours by signal"),
            use_container_width=True,
        )

    st.header("Top automation candidates")
    for i, c in enumerate(summary["candidates"][:5], 1):
        with st.expander(f"{i}. {c['label']} — {c['count']} events, ~{c['hours']:.1f}h"):
            st.write(c["recommendation"])
            if c["example"]:
                st.caption(f"e.g. \"{c['example']['description']}\" ({c['example']['date']})")

    if summary["hotspots"]:
        st.header("Churn hotspots")
        st.dataframe(
            pd.DataFrame(summary["hotspots"], columns=["file", "commits touching it"]),
            use_container_width=True,
        )

    st.header("Recent toil events")
    st.dataframe(
        df[["date", "signal", "description", "author", "hours"]].head(25),
        use_container_width=True,
    )


def main():
    """Console entry point: launch streamlit with this module as the app."""
    _require_streamlit()
    import subprocess
    subprocess.run([sys.executable, "-m", "streamlit", "run", __file__])


if __name__ == "__main__":
    run()
