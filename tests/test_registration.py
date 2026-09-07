"""Registration failure is retryable without damaging the existing memory store."""

import base64
import subprocess
import unittest
from unittest.mock import patch

import test_service as fixtures
from chmemx.runtime import simple_memory as core


class RegistrationAcceptance(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown

    def reject_commits(self):
        hook = self.service.store / ".git/hooks/pre-commit"
        core.run_git(self.service.store, ["config", "core.hooksPath", str(hook.parent)])
        hook.write_bytes(b"#!/bin/sh\nexit 1\n")
        hook.chmod(0o700)
        return hook

    def second_project(self):
        path = self.root / "project-beta"
        path.mkdir()
        core.run_git(path, ["init", "-q"])
        return path

    def test_project_registration_commit_failure_preserves_clean_retry(self):
        other = self.second_project()
        head = core.git_head(self.service.store)
        hook = self.reject_commits()
        with self.assertRaises(core.MemoryError):
            self.service.runtime.register_project(other, "project-beta", "Beta", confirmed=True)
        hook.unlink()
        self.assertEqual(head, core.git_head(self.service.store))
        self.assertFalse(core.run_git(self.service.store, ["status", "--porcelain"]).stdout)
        self.assertFalse((self.service.store / "projects/project-beta").exists())
        self.assertEqual(1, self.service.start()["memory"]["project_count"])
        result = self.service.runtime.register_project(
            other, "project-beta", "Beta", confirmed=True
        )
        self.assertEqual("PROJECT_REGISTERED", result["status"])

    def test_source_registration_commit_failure_preserves_clean_retry(self):
        key = base64.b64encode(bytes(range(32))).decode("ascii")
        head = core.git_head(self.service.store)
        hook = self.reject_commits()
        with self.assertRaises(core.MemoryError):
            self.service.trust_source("source-beta", key)
        hook.unlink()
        self.assertEqual(head, core.git_head(self.service.store))
        self.assertFalse(core.run_git(self.service.store, ["status", "--porcelain"]).stdout)
        self.assertEqual(
            "SOURCE_KEY_REGISTERED", self.service.trust_source("source-beta", key)["status"]
        )

    def test_registration_does_not_remove_foreign_content_in_a_new_directory(self):
        other = self.second_project()
        hook = self.reject_commits()
        hook.write_bytes(
            b"#!/bin/sh\nprintf preserve > projects/project-beta/foreign.txt\nexit 1\n"
        )
        with self.assertRaises(core.MemoryError) as err:
            self.service.runtime.register_project(other, "project-beta", "Beta", confirmed=True)
        self.assertEqual("ROLLBACK_DIRECTORY_REMAINS", err.exception.code)
        self.assertEqual(
            "preserve", (self.service.store / "projects/project-beta/foreign.txt").read_text()
        )
        self.assertEqual(1, self.service.start()["memory"]["project_count"])

    def test_registration_partial_staging_failure_is_retryable(self):
        other = self.second_project()
        run = subprocess.run

        def fail_add(command, **kwargs):
            if command[1:4] == ["-C", str(self.service.store), "add"]:
                run([*command[:3], "add", "--", "catalog.json"], **kwargs)
                return subprocess.CompletedProcess(command, 1, b"", b"Fixture staging failure.")
            return run(command, **kwargs)

        with patch.object(subprocess, "run", fail_add), self.assertRaises(core.MemoryError):
            self.service.runtime.register_project(other, "project-beta", "Beta", confirmed=True)
        self.assertFalse(core.run_git(self.service.store, ["status", "--porcelain"]).stdout)
        self.assertEqual(
            "PROJECT_REGISTERED",
            self.service.runtime.register_project(other, "project-beta", "Beta", confirmed=True)[
                "status"
            ],
        )

    def test_successful_registration_is_not_rolled_back_after_a_lost_reply(self):
        other = self.second_project()
        run = subprocess.run

        def lost_reply(command, **kwargs):
            result = run(command, **kwargs)
            if command[1:4] == ["-C", str(self.service.store), "commit"] and result.returncode == 0:
                raise OSError("Fixture lost commit response.")
            return result

        with patch.object(subprocess, "run", lost_reply), self.assertRaises(OSError):
            self.service.runtime.register_project(other, "project-beta", "Beta", confirmed=True)
        self.assertFalse(core.run_git(self.service.store, ["status", "--porcelain"]).stdout)
        self.assertEqual(2, self.service.start()["memory"]["project_count"])


if __name__ == "__main__":
    unittest.main()
