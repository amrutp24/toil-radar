"""
Toil Tracker - Simple MVP
Detect DevOps toil from Git history and manual patterns
"""

import subprocess
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import re

class ToilDetector:
    def __init__(self, db_path="toil.db"):
        self.db_path = db_path
        self.init_db()
        
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
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
        conn.close()
    
    def scan_git_history(self, repo_path, days_back=30):
        """Scan git commits for toil indicators"""
        patterns = {
            "manual_deploy": ["deploy", "deployment", "production deploy"],
            "manual_fix": ["fix", "hotfix", "emergency", "manual"],
            "revert": ["revert", "rollback", "backed out"],
            "env_setup": ["env", "environment", "setup", "config"],
            "restart": ["restart", "reboot", "restart service"]
        }
        
        try:
            repo_path = Path(repo_path).resolve()
            result = subprocess.run(
                ["git", "log", f"--since={days_back} days ago", "--pretty=format:%h|%s|%ad", "--date=short"],
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
                        severity = self._assess_severity(message, task_type)
                        toil_found.append({
                            'date': date,
                            'repo_path': str(repo_path),
                            'task_type': task_type,
                            'description': message,
                            'severity': severity
                        })
                        break
                        
            return toil_found
            
        except Exception as e:
            print(f"Error scanning {repo_path}: {e}")
            return []
    
    def _assess_severity(self, message, task_type):
        """Assess severity based on message content"""
        high_severity = ["emergency", "critical", "urgent", "outage", "downtime"]
        medium_severity = ["hotfix", "production", "manual"]
        
        message_lower = message.lower()
        if any(word in message_lower for word in high_severity):
            return "HIGH"
        elif any(word in message_lower for word in medium_severity):
            return "MEDIUM"
        else:
            return "LOW"
    
    def save_toil_events(self, events):
        """Save detected toil events to database"""
        conn = sqlite3.connect(self.db_path)
        for event in events:
            conn.execute('''
                INSERT INTO toil_events (date, repo_path, task_type, description, severity)
                VALUES (?, ?, ?, ?, ?)
            ''', (event['date'], event['repo_path'], event['task_type'], 
                  event['description'], event['severity']))
        conn.commit()
        conn.close()
    
    def get_toil_summary(self, days_back=30):
        """Get summary of toil events"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('''
            SELECT task_type, severity, COUNT(*) as count, date
            FROM toil_events
            WHERE date >= date('now', ? || ' days')
            GROUP BY task_type, severity
            ORDER BY count DESC
        ''', (f'-{int(days_back)}',))
        
        results = cursor.fetchall()
        conn.close()
        return results

def main():
    detector = ToilDetector()
    
    # Example: Scan current directory
    print("🔍 Scanning for DevOps toil patterns...")
    
    # Test with current directory if it's a git repo
    if Path(".git").exists():
        events = detector.scan_git_history(".", days_back=30)
        detector.save_toil_events(events)
        print(f"✅ Found {len(events)} toil events")
        
        summary = detector.get_toil_summary()
        print("\n📊 Toil Summary (last 30 days):")
        for task_type, severity, count, date in summary:
            print(f"  {task_type}: {count} ({severity})")
    else:
        print("❌ Not a git repository. Run from a git repo directory.")
        print("Usage: python toil_detector.py")

if __name__ == "__main__":
    main()