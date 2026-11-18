from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chmemx.scripts.retrieval_v3 import clean_head


class StoreSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chmemx-snapshot-")
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.record = self.root / "record.txt"
        self.record.write_text("fixture\n")
        self.git("add", "record.txt")
        self.git("commit", "-qm", "fixture")
        self.head = self.git("rev-parse", "HEAD").decode().strip()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True
        ).stdout

    def test_clean_snapshot_has_single_process_budget(self):
        with patch("subprocess.run", wraps=subprocess.run) as run:
            self.assertEqual(self.head, clean_head(self.root))
        self.assertEqual(1, run.call_count)

    def test_tracked_changes_still_block_snapshot(self):
        self.record.write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "STORE_DIRTY"):
            clean_head(self.root)
        self.git("add", "record.txt")
        with self.assertRaisesRegex(ValueError, "STORE_DIRTY"):
            clean_head(self.root)

    def test_untracked_fixture_does_not_block_snapshot(self):
        (self.root / "untracked.txt").write_text("temporary\n")
        self.assertEqual(self.head, clean_head(self.root))

    def test_detached_head_is_supported(self):
        self.git("checkout", "--detach", "-q", self.head)
        self.assertEqual(self.head, clean_head(self.root))

    def test_new_commit_is_observed_without_reusing_old_head(self):
        self.record.write_text("next\n")
        self.git("add", "record.txt")
        self.git("commit", "-qm", "next")
        current = self.git("rev-parse", "HEAD").decode().strip()
        self.assertNotEqual(self.head, current)
        self.assertEqual(current, clean_head(self.root))

    def test_unborn_repository_keeps_git_error(self):
        unborn = self.root / "unborn"
        unborn.mkdir()
        subprocess.run(["git", "init", "-q", str(unborn)], check=True, capture_output=True)
        with self.assertRaises(subprocess.CalledProcessError):
            clean_head(unborn)


if __name__ == "__main__":
    unittest.main()
