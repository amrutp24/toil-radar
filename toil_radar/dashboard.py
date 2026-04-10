"""
Streamlit Dashboard for Toil Tracker
Visualize DevOps toil patterns and trends
"""

import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
from .toil_detector import ToilDetector

def init_database():
    """Initialize database if not exists"""
    conn = sqlite3.connect('demo.db')
    cursor = conn.execute('''
        CREATE TABLE IF NOT EXISTS toil_events (
            id INTEGER PRIMARY KEY,
            date TEXT,
            repo_path TEXT,
            task_type TEXT,
            description TEXT,
            severity TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def load_data(days_back=30):
    """Load toil data from database"""
    conn = sqlite3.connect('demo.db')
    
    query = '''
        SELECT date, task_type, severity, description, repo_path
        FROM toil_events
        WHERE date >= date('now', ? || ' days')
        ORDER BY date DESC
    '''

    df = pd.read_sql_query(query, conn, params=(f'-{int(days_back)}',))
    conn.close()
    return df

def main():
    st.set_page_config(
        page_title="DevOps Toil Dashboard",
        page_icon="🔧",
        layout="wide"
    )
    
    st.title("🔧 DevOps Toil Dashboard")
    st.markdown("Track and reduce repetitive operational work")
    
    # Initialize database
    init_database()
    
    # Sidebar controls
    st.sidebar.header("Controls")
    days_back = st.sidebar.slider("Days to analyze", 7, 90, 30)
    
    # Manual scan section
    st.sidebar.header("🔍 Scan Repository")
    repo_path = st.sidebar.text_input("Git repo path", ".")
    
    if st.sidebar.button("Scan for Toil"):
        if repo_path:
            with st.spinner("Scanning repository..."):
                detector = ToilDetector('demo.db')
                events = detector.scan_git_history(repo_path, days_back)
                detector.save_toil_events(events)
                st.sidebar.success(f"Found {len(events)} toil events!")
        else:
            st.sidebar.error("Please enter a repository path")
    
    # Load and display data
    df = load_data(days_back)
    
    if df.empty:
        st.warning("📊 No toil data found. Scan a repository to get started!")
        return
    
    # Key metrics
    st.header("📊 Key Metrics")
    col1, col2, col3, col4 = st.columns(4)
    
    total_toil = len(df)
    high_severity = len(df[df['severity'] == 'HIGH'])
    unique_task_types = df['task_type'].nunique()
    
    col1.metric("Total Toil Events", total_toil)
    col2.metric("High Severity", high_severity, delta=f"{high_severity/total_toil*100:.1f}%")
    col3.metric("Task Types", unique_task_types)
    col4.metric("Daily Average", round(total_toil/days_back, 1))
    
    # Charts
    st.header("📈 Trends")
    
    # Toil over time
    col1, col2 = st.columns(2)
    
    with col1:
        daily_counts = df.groupby('date').size().reset_index(name='count')
        fig_trend = px.line(daily_counts, x='date', y='count', 
                           title='Toil Events Over Time')
        st.plotly_chart(fig_trend, use_container_width=True)
    
    with col2:
        severity_counts = df['severity'].value_counts()
        fig_severity = px.pie(values=severity_counts.values, 
                             names=severity_counts.index, 
                             title='Severity Distribution')
        st.plotly_chart(fig_severity, use_container_width=True)
    
    # Task type breakdown
    st.header("🔍 Task Analysis")
    
    col1, col2 = st.columns(2)
    
    with col1:
        task_counts = df['task_type'].value_counts()
        fig_tasks = px.bar(x=task_counts.index, y=task_counts.values,
                          title='Toil by Task Type')
        fig_tasks.update_xaxes(tickangle=45)
        st.plotly_chart(fig_tasks, use_container_width=True)
    
    with col2:
        severity_by_task = df.groupby(['task_type', 'severity']).size().unstack(fill_value=0)
        fig_heatmap = px.imshow(severity_by_task.T, 
                               title='Severity Heatmap by Task Type',
                               color_continuous_scale='Reds')
        st.plotly_chart(fig_heatmap, use_container_width=True)
    
    # Recent events table
    st.header("📋 Recent Toil Events")
    st.dataframe(
        df[['date', 'task_type', 'severity', 'description']].head(10),
        use_container_width=True
    )
    
    # Recommendations
    st.header("💡 Recommendations")
    
    high_count_tasks = task_counts[task_counts > task_counts.quantile(0.75)]
    if not high_count_tasks.empty:
        st.warning("⚠️ High-frequency toil detected:")
        for task_type, count in high_count_tasks.items():
            st.write(f"• {task_type}: {count} events - Consider automation")
    
    if high_severity > 0:
        st.error("🚨 High severity toil found - Review emergency procedures")

if __name__ == "__main__":
    main()