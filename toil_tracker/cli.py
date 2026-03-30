#!/usr/bin/env python3

import argparse
import subprocess
import sqlite3
import sys
import os
from pathlib import Path

def scan_repo(repo_path, days=30, db_path="toil.db"):
    """Scan repository for toil patterns"""
    if not Path(repo_path).exists():
        print(f"Error: Repository path '{repo_path}' does not exist")
        return 1
        
    if not (Path(repo_path) / ".git").exists():
        print(f"Error: '{repo_path}' is not a Git repository")
        return 1
        
    print(f"Scanning {repo_path} for toil patterns...")
    
    # Initialize database
    conn = sqlite3.connect(db_path)
    conn.execute('''
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
    
    # Patterns to detect
    patterns = {
        "manual_deploy": ["deploy", "deployment", "production deploy"],
        "manual_fix": ["fix", "hotfix", "emergency", "manual"],
        "revert": ["revert", "rollback", "backed out"],
        "env_setup": ["env", "environment", "setup", "config"],
        "restart": ["restart", "reboot", "restart service"]
    }
    
    try:
        result = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--pretty=format:%h|%s|%ad", "--date=short"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        
        toil_found = []
        for line in result.stdout.split('\n'):
            if not line.strip():
                continue
                
            commit_hash, message, date = line.split('|', 2)
            message_lower = message.lower()
            
            for task_type, keywords in patterns.items():
                if any(keyword in message_lower for keyword in keywords):
                    # Assess severity
                    if any(word in message_lower for word in ["emergency", "critical", "urgent"]):
                        severity = "HIGH"
                    elif any(word in message_lower for word in ["hotfix", "production", "manual"]):
                        severity = "MEDIUM"
                    else:
                        severity = "LOW"
                        
                    toil_found.append({
                        'date': date,
                        'repo_path': str(repo_path),
                        'task_type': task_type,
                        'description': message,
                        'severity': severity
                    })
                    break
        
        if toil_found:
            # Save to database
            for event in toil_found:
                conn.execute('''
                    INSERT INTO toil_events (date, repo_path, task_type, description, severity)
                    VALUES (?, ?, ?, ?, ?)
                ''', (event['date'], event['repo_path'], event['task_type'], 
                      event['description'], event['severity']))
            conn.commit()
            
            print(f"Found {len(toil_found)} toil events")
            
            # Show summary
            by_type = {}
            for event in toil_found:
                task_type = event['task_type']
                if task_type not in by_type:
                    by_type[task_type] = 0
                by_type[task_type] += 1
                
            print("\nBreakdown:")
            for task_type, count in by_type.items():
                print(f"  {task_type}: {count}")
        else:
            print("No toil patterns detected")
            
    except Exception as e:
        print(f"Error scanning repository: {e}")
        return 1
    
    conn.close()
    return 0

def show_summary(db_path="toil.db", days=30):
    """Show toil summary"""
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(f'''
        SELECT task_type, severity, COUNT(*) as count
        FROM toil_events 
        WHERE date >= date('now', '-{days} days')
        GROUP BY task_type, severity
        ORDER BY count DESC
    ''')
    
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        print("No toil data found. Run 'toil-radar scan' first.")
        return
        
    print(f"\nToil Summary (last {days} days):")
    print("-" * 40)
    
    by_type = {}
    for task_type, severity, count in results:
        if task_type not in by_type:
            by_type[task_type] = {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
        by_type[task_type][severity] += count
        
    for task_type, severities in by_type.items():
        total = sum(severities.values())
        print(f"{task_type}: {total} total")
        for severity, count in severities.items():
            if count > 0:
                print(f"  {severity}: {count}")

def main():
    parser = argparse.ArgumentParser(
        description="Detect and track DevOps toil in Git repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  toil-radar scan /path/to/repo
  toil-radar scan . --days 60
  toil-radar summary
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Scan command
    scan_parser = subparsers.add_parser('scan', help='Scan repository for toil')
    scan_parser.add_argument('repo_path', help='Path to Git repository')
    scan_parser.add_argument('--days', type=int, default=30, help='Days to look back (default: 30)')
    scan_parser.add_argument('--db', default='toil.db', help='Database file (default: toil.db)')
    
    # Summary command
    summary_parser = subparsers.add_parser('summary', help='Show toil summary')
    summary_parser.add_argument('--days', type=int, default=30, help='Days to look back (default: 30)')
    summary_parser.add_argument('--db', default='toil.db', help='Database file (default: toil.db)')
    
    if len(sys.argv) == 1:
        parser.print_help()
        return
    
    args = parser.parse_args()
    
    if args.command == 'scan':
        return scan_repo(args.repo_path, args.days, args.db)
    elif args.command == 'summary':
        show_summary(args.db, args.days)

if __name__ == "__main__":
    sys.exit(main())