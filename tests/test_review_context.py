"""Reject foreign-context review before changing an upload or its sealed batch."""

import unittest

import test_service as fixtures
from chmemx.runtime import simple_memory as core
from chmemx.service import Service


class ReviewContextAcceptance(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown
    preference = fixtures.ServiceAcceptance.preference

    def write_authority(self, root):
        core.run_git(root, ["config", "user.name", "Fixture"])
        core.run_git(root, ["config", "user.email", "fixture@example.invalid"])
        core.atomic_bytes(root / "authority.md", b"Protocol Alpha.\n")
        core.run_git(root, ["add", "authority.md"])
        core.run_git(root, ["commit", "-qm", "Fixture authority"])

    def foreign_service(self):
        self.write_authority(self.project)
        other = self.root / "project-beta"
        other.mkdir()
        core.run_git(other, ["init", "-q"])
        self.write_authority(other)
        self.service.runtime.register_project(other, "project-beta", "Beta", confirmed=True)
        return Service(self.service.store, other, "source-alpha")

    def test_foreign_refresh_preserves_original_confirmation(self):
        foreign = self.foreign_service()
        upload = self.service.upload(
            "contract.protocol",
            "Protocol Alpha.",
            {"path": "authority.md"},
            scope="project",
            memory_class="decision",
        )
        review = self.service.review(upload["upload_id"])
        before = self.service.upload_status(upload["upload_id"])["job_digest"]
        for refresh in (False, True):
            with self.subTest(refresh=refresh), self.assertRaises(core.MemoryError) as err:
                foreign.review(upload["upload_id"], refresh=refresh)
            self.assertEqual("UPLOAD_CONTEXT_MISMATCH", err.exception.code)
            self.assertEqual(before, self.service.upload_status(upload["upload_id"])["job_digest"])
        self.assertEqual(
            review["batch_digest"], self.service.review(upload["upload_id"])["batch_digest"]
        )
        self.assertEqual(
            "ACTIVE_COMMITTED",
            self.service.approve(
                review["batch_id"], review["batch_digest"], review["required_confirmation"]
            )["status"],
        )

    def test_global_upload_can_be_reviewed_from_another_registered_project(self):
        foreign = self.foreign_service()
        uploaded = self.service.upload(**self.preference())
        original = self.service.review(uploaded["upload_id"])
        repeated = foreign.review(uploaded["upload_id"])
        self.assertEqual(original["batch_id"], repeated["batch_id"])
        refreshed = foreign.review(uploaded["upload_id"], refresh=True)
        self.assertNotEqual(original["batch_id"], refreshed["batch_id"])
        self.assertEqual("pending", refreshed["status"])
        self.assertEqual(
            "ACTIVE_COMMITTED",
            foreign.approve(
                refreshed["batch_id"],
                refreshed["batch_digest"],
                refreshed["required_confirmation"],
            )["status"],
        )


if __name__ == "__main__":
    unittest.main()
