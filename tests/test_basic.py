"""
Basic unit tests for toil tracker
"""

import unittest
import sys
import tempfile
import subprocess
import sqlite3
import os
from pathlib import Path

# Add the package to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from toil_tracker.cli import scan_repo, show_summary

class TestToilDetector(unittest.TestCase):
    
    def setUp(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test.db")
        
        # Initialize test git repo
        subprocess.run(["git", "init"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.test_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.test_dir, check=True)
        
    def test_manual_deploy_detection(self):
        """Test detection of manual deployment patterns"""
        # Create commits with toil patterns
        test_commits = [
            "deploy to production manually",
            "hotfix database issue", 
            "restart services after crash",
            "revert broken changes"
        ]
        
        for commit in test_commits:
            # Create dummy file and commit
            dummy_file = Path(self.test_dir) / "dummy.txt"
            dummy_file.write_text(f"Content for {commit}")
            subprocess.run(["git", "add", "dummy.txt"], cwd=self.test_dir, check=True)
            subprocess.run(["git", "commit", "-m", commit], cwd=self.test_dir, check=True)
            
        # Run scan
        result = scan_repo(self.test_dir, days=30, db_path=self.test_db)
        
        # Should detect at least some toil events
        self.assertEqual(result, 0)
        
        # Verify database has entries
        conn = sqlite3.connect(self.test_db)
        cursor = conn.execute("SELECT COUNT(*) FROM toil_events")
        count = cursor.fetchone()[0]
        conn.close()
        
        self.assertGreater(count, 0, "Should detect toil events")
        
    def test_no_toil_repo(self):
        """Test repository with no toil patterns"""
        # Create normal commits
        for i in range(3):
            dummy_file = Path(self.test_dir) / f"feature{i}.txt"
            dummy_file.write_text(f"Feature {i}")
            subprocess.run(["git", "add", f"feature{i}.txt"], cwd=self.test_dir, check=True)
            subprocess.run(["git", "commit", "-m", f"Add feature {i}"], cwd=self.test_dir, check=True)
            
        # Run scan
        result = scan_repo(self.test_dir, days=30, db_path=self.test_db)
        self.assertEqual(result, 0)
        
        # Should find minimal toil
        conn = sqlite3.connect(self.test_db)
        cursor = conn.execute("SELECT COUNT(*) FROM toil_events")
        count = cursor.fetchone()[0]
        conn.close()
        
        self.assertLessEqual(count, 1, "Should find minimal or no toil")
        
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

if __name__ == "__main__":
    unittest.main()