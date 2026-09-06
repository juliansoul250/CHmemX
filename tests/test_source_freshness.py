import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from chmemx.scripts.source_freshness import check


class SourceFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.path = self.root / "contract.md"
        self.path.write_text("fixture contract", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.record = {
            "scope": "project",
            "class": "decision",
            "source": {
                "kind": "git",
                "project_root": str(self.root),
                "path": "contract.md",
                "commit": self.git("rev-parse", "HEAD"),
                "hash": "sha256:" + hashlib.sha256(self.path.read_bytes()).hexdigest(),
            },
        }

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def test_current_and_dirty_dependency(self):
        self.assertEqual("CURRENT", check(self.record)["status"])
        self.path.write_text("changed", encoding="utf-8")
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])

    def test_unrelated_commit_preserves_contract_not_progress(self):
        (self.root / "other.txt").write_text("unrelated", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "unrelated")
        self.assertEqual("CURRENT", check(self.record)["status"])
        self.record["class"] = "evidence"
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])

    def test_changed_lesson_remains_historical(self):
        self.path.write_text("changed", encoding="utf-8")
        self.record["class"] = "lesson"
        self.assertEqual("HISTORICAL", check(self.record)["status"])

    def test_missing_source_is_not_current(self):
        self.path.unlink()
        self.assertEqual("UNVERIFIED", check(self.record)["status"])
