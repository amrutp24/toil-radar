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
from toil_radar.toil_detector import ToilDetector


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


class TestToilDetector(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test.db")
        make_git_repo(self.test_dir)
        self.detector = ToilDetector(db_path=self.test_db)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # --- init ---

    def test_init_creates_database(self):
        self.assertTrue(os.path.exists(self.test_db))

    def test_init_creates_table(self):
        conn = sqlite3.connect(self.test_db)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        self.assertIn(("toil_events",), tables)

    # --- scan_git_history ---

    def test_scan_returns_list(self):
        make_commit(self.test_dir, "f.txt", "normal commit")
        result = self.detector.scan_git_history(self.test_dir)
        self.assertIsInstance(result, list)

    def test_scan_detects_toil(self):
        make_commit(self.test_dir, "f.txt", "deploy to production")
        events = self.detector.scan_git_history(self.test_dir)
        self.assertGreater(len(events), 0)

    def test_scan_returns_correct_fields(self):
        make_commit(self.test_dir, "f.txt", "revert broken release")
        events = self.detector.scan_git_history(self.test_dir)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIn("date", event)
        self.assertIn("repo_path", event)
        self.assertIn("task_type", event)
        self.assertIn("description", event)
        self.assertIn("severity", event)

    def test_scan_empty_on_clean_commits(self):
        make_commit(self.test_dir, "f.txt", "add new feature")
        events = self.detector.scan_git_history(self.test_dir)
        self.assertEqual(len(events), 0)

    def test_scan_invalid_path_returns_empty(self):
        events = self.detector.scan_git_history("/nonexistent/path")
        self.assertEqual(events, [])

    # --- _assess_severity ---

    def test_severity_high_on_emergency(self):
        self.assertEqual(self.detector._assess_severity("emergency outage fix", "manual_fix"), "HIGH")

    def test_severity_high_on_critical(self):
        self.assertEqual(self.detector._assess_severity("critical system failure", "manual_fix"), "HIGH")

    def test_severity_medium_on_hotfix(self):
        self.assertEqual(self.detector._assess_severity("hotfix login bug", "manual_fix"), "MEDIUM")

    def test_severity_medium_on_production(self):
        self.assertEqual(self.detector._assess_severity("deploy to production", "manual_deploy"), "MEDIUM")

    def test_severity_low_on_generic(self):
        self.assertEqual(self.detector._assess_severity("revert last commit", "revert"), "LOW")

    # --- save_toil_events ---

    def test_save_persists_events(self):
        events = [{"date": "2026-01-01", "repo_path": "/repo", "task_type": "revert",
                   "description": "revert bad change", "severity": "LOW"}]
        self.detector.save_toil_events(events)
        conn = sqlite3.connect(self.test_db)
        count = conn.execute("SELECT COUNT(*) FROM toil_events").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_save_multiple_events(self):
        events = [
            {"date": "2026-01-01", "repo_path": "/repo", "task_type": "revert",
             "description": "revert", "severity": "LOW"},
            {"date": "2026-01-02", "repo_path": "/repo", "task_type": "manual_deploy",
             "description": "deploy", "severity": "MEDIUM"},
        ]
        self.detector.save_toil_events(events)
        conn = sqlite3.connect(self.test_db)
        count = conn.execute("SELECT COUNT(*) FROM toil_events").fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    # --- get_toil_summary ---

    def test_summary_empty_when_no_data(self):
        results = self.detector.get_toil_summary()
        self.assertEqual(results, [])

    def test_summary_returns_data_after_save(self):
        make_commit(self.test_dir, "f.txt", "deploy to production")
        events = self.detector.scan_git_history(self.test_dir)
        self.detector.save_toil_events(events)
        results = self.detector.get_toil_summary()
        self.assertGreater(len(results), 0)

    def test_summary_groups_by_task_and_severity(self):
        events = [
            {"date": "2026-01-01", "repo_path": "/r", "task_type": "revert", "description": "a", "severity": "LOW"},
            {"date": "2026-01-02", "repo_path": "/r", "task_type": "revert", "description": "b", "severity": "LOW"},
        ]
        self.detector.save_toil_events(events)
        results = self.detector.get_toil_summary()
        task_types = [r[0] for r in results]
        self.assertIn("revert", task_types)
        counts = {r[0]: r[2] for r in results}
        self.assertEqual(counts["revert"], 2)


if __name__ == "__main__":
    unittest.main()
