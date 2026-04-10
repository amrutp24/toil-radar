"""
Unit tests for toil-radar
"""

import unittest
import sys
import tempfile
import subprocess
import sqlite3
import os
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from toil_radar.cli import scan_repo, show_summary


def make_git_repo(path):
    """Helper: initialize a git repo with a test user."""
    subprocess.run(["git", "init"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)


def make_commit(path, filename, message):
    """Helper: create a file and commit it."""
    (Path(path) / filename).write_text(message)
    subprocess.run(["git", "add", filename], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=path, check=True)


class TestScanRepo(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test.db")
        make_git_repo(self.test_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # --- happy path ---

    def test_detects_toil_commits(self):
        for i, msg in enumerate(["deploy to production manually", "hotfix database issue",
                                  "restart services after crash", "revert broken changes"]):
            make_commit(self.test_dir, f"f{i}.txt", msg)

        result = scan_repo(self.test_dir, days=30, db_path=self.test_db)

        self.assertEqual(result, 0)
        conn = sqlite3.connect(self.test_db)
        count = conn.execute("SELECT COUNT(*) FROM toil_events").fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)

    def test_clean_repo_has_no_toil(self):
        for i in range(3):
            make_commit(self.test_dir, f"feature{i}.txt", f"add feature {i}")

        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        conn = sqlite3.connect(self.test_db)
        count = conn.execute("SELECT COUNT(*) FROM toil_events").fetchone()[0]
        conn.close()
        self.assertLessEqual(count, 1)

    def test_returns_zero_on_success(self):
        make_commit(self.test_dir, "f.txt", "normal commit")
        self.assertEqual(scan_repo(self.test_dir, days=30, db_path=self.test_db), 0)

    def test_creates_database(self):
        make_commit(self.test_dir, "f.txt", "deploy to prod")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)
        self.assertTrue(os.path.exists(self.test_db))

    # --- severity ---

    def test_high_severity_on_emergency(self):
        make_commit(self.test_dir, "f.txt", "emergency fix critical outage")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        conn = sqlite3.connect(self.test_db)
        row = conn.execute("SELECT severity FROM toil_events").fetchone()
        conn.close()
        self.assertEqual(row[0], "HIGH")

    def test_medium_severity_on_hotfix(self):
        make_commit(self.test_dir, "f.txt", "hotfix production issue")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        conn = sqlite3.connect(self.test_db)
        row = conn.execute("SELECT severity FROM toil_events").fetchone()
        conn.close()
        self.assertEqual(row[0], "MEDIUM")

    def test_low_severity_on_generic_toil(self):
        make_commit(self.test_dir, "f.txt", "revert last change")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        conn = sqlite3.connect(self.test_db)
        row = conn.execute("SELECT severity FROM toil_events").fetchone()
        conn.close()
        self.assertEqual(row[0], "LOW")

    # --- task types ---

    def test_detects_manual_deploy(self):
        make_commit(self.test_dir, "f.txt", "deploy to staging")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        conn = sqlite3.connect(self.test_db)
        row = conn.execute("SELECT task_type FROM toil_events").fetchone()
        conn.close()
        self.assertEqual(row[0], "manual_deploy")

    def test_detects_revert(self):
        make_commit(self.test_dir, "f.txt", "revert bad release")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        conn = sqlite3.connect(self.test_db)
        row = conn.execute("SELECT task_type FROM toil_events").fetchone()
        conn.close()
        self.assertEqual(row[0], "revert")

    def test_detects_restart(self):
        make_commit(self.test_dir, "f.txt", "restart service after crash")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        conn = sqlite3.connect(self.test_db)
        row = conn.execute("SELECT task_type FROM toil_events").fetchone()
        conn.close()
        self.assertEqual(row[0], "restart")

    # --- error cases ---

    def test_invalid_path_returns_error(self):
        result = scan_repo("/nonexistent/path", days=30, db_path=self.test_db)
        self.assertEqual(result, 1)

    def test_non_git_directory_returns_error(self):
        non_git = tempfile.mkdtemp()
        try:
            result = scan_repo(non_git, days=30, db_path=self.test_db)
            self.assertEqual(result, 1)
        finally:
            import shutil
            shutil.rmtree(non_git)


class TestShowSummary(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test.db")
        make_git_repo(self.test_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_summary_empty_db(self):
        with patch('sys.stdout', new_callable=StringIO) as mock_out:
            show_summary(db_path=self.test_db, days=30)
            output = mock_out.getvalue()
        self.assertIn("No toil data", output)

    def test_summary_shows_data_after_scan(self):
        make_commit(self.test_dir, "f.txt", "deploy to production")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        with patch('sys.stdout', new_callable=StringIO) as mock_out:
            show_summary(db_path=self.test_db, days=30)
            output = mock_out.getvalue()

        self.assertIn("manual_deploy", output)

    def test_summary_shows_severity_breakdown(self):
        make_commit(self.test_dir, "f.txt", "emergency deploy to production")
        scan_repo(self.test_dir, days=30, db_path=self.test_db)

        with patch('sys.stdout', new_callable=StringIO) as mock_out:
            show_summary(db_path=self.test_db, days=30)
            output = mock_out.getvalue()

        self.assertIn("HIGH", output)


if __name__ == "__main__":
    unittest.main()
