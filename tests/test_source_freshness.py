import hashlib
import shlex
import subprocess
import sys
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
        self.git("config", "core.autocrlf", "false")
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

    def pin_text(self, attributes, body=b"Protocol Alpha.\nSecond line.\n"):
        self.path.write_bytes(body)
        (self.root / ".gitattributes").write_text(attributes, encoding="utf-8")
        self.git("add", "contract.md", ".gitattributes")
        self.git("commit", "-qm", "Fixture declared text conversion")
        raw = subprocess.check_output(["git", "-C", str(self.root), "show", "HEAD:contract.md"])
        self.record["source"].update(
            commit=self.git("rev-parse", "HEAD"), hash="sha256:" + hashlib.sha256(raw).hexdigest()
        )

    def test_clean_declared_crlf_is_current_without_hiding_content_changes(self):
        self.pin_text("contract.md text eol=crlf\n")
        self.path.unlink()
        self.git("checkout", "--", "contract.md")
        self.assertIn(b"\r\n", self.path.read_bytes())
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertEqual("CURRENT", check(self.record)["status"])
        self.path.write_bytes(b"Protocol Beta.\r\nSecond line.\r\n")
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])

    def test_auto_text_and_autocrlf_are_read_only_supported_rules(self):
        self.pin_text("contract.md text=auto eol=crlf\n")
        self.path.write_bytes(b"Protocol Alpha.\r\nSecond line.\r\n")
        self.assertEqual("CURRENT", check(self.record)["status"])
        self.pin_text("")
        self.git("config", "core.autocrlf", "true")
        self.path.unlink()
        self.git("checkout", "--", "contract.md")
        config = (self.root / ".git/config").read_bytes()
        self.assertEqual("CURRENT", check(self.record)["status"])
        self.assertEqual(config, (self.root / ".git/config").read_bytes())
        self.git("config", "core.autocrlf", "false")
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])

    def test_unset_text_and_nul_bytes_are_not_normalized(self):
        self.pin_text("contract.md -text\n")
        self.path.write_bytes(b"Protocol Alpha.\r\nSecond line.\r\n")
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])
        self.pin_text("contract.md text=auto eol=crlf\n", b"Alpha\0Beta\n")
        self.assertEqual("CURRENT", check(self.record)["status"])
        self.path.write_bytes(b"Alpha\0Beta\r\n")
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])

    def test_uncommitted_attributes_cannot_hide_a_byte_change(self):
        self.pin_text("contract.md -text\n")
        (self.root / ".gitattributes").write_text("contract.md text eol=crlf\n", encoding="utf-8")
        self.path.write_bytes(b"Protocol Alpha.\r\nSecond line.\r\n")
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])

    def test_custom_filter_is_never_executed_by_source_validation(self):
        self.pin_text("contract.md text eol=crlf filter=fixture-probe\n")
        script = self.root / "fixture_filter.py"
        marker = self.root / "filter-ran"
        script.write_text(
            "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('ran')\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n",
            encoding="utf-8",
        )
        command = " ".join(
            shlex.quote(Path(p).as_posix()) for p in (sys.executable, script, marker)
        )
        self.git("config", "filter.fixture-probe.clean", command)
        subprocess.run(
            ["git", "-C", str(self.root), "hash-object", "--path=contract.md", "--stdin"],
            input=b"fixture\n",
            capture_output=True,
            check=True,
        )
        self.assertTrue(marker.exists(), "The harmless test filter must be executable.")
        marker.unlink()
        self.path.write_bytes(b"Protocol Alpha.\r\nSecond line.\r\n")
        self.assertFalse(check(self.record)["current_use_allowed"])
        self.assertFalse(marker.exists())

    def test_normalized_progress_still_requires_its_original_revision(self):
        self.pin_text("contract.md text eol=crlf\n")
        self.path.write_bytes(b"Protocol Alpha.\r\nSecond line.\r\n")
        self.record["class"] = "evidence"
        self.assertEqual("CURRENT", check(self.record)["status"])
        (self.root / "unrelated").write_bytes(b"other")
        self.git("add", "unrelated")
        self.git("commit", "-qm", "Fixture unrelated revision")
        self.assertEqual("REVIEW_REQUIRED", check(self.record)["status"])
