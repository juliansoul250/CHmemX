"""A request's lifecycle must survive storage transitions and interrupted writeback."""

import unittest
from unittest.mock import patch

import test_service as fixtures
from chmemx.runtime import simple_memory as core
from chmemx.service import Service


class CommitProofAcceptance(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown
    preference = fixtures.ServiceAcceptance.preference

    def approved_without_writeback(self):
        uploaded = self.service.upload(**self.preference(), request_id="fixture-stable-request")
        review = self.service.review(uploaded["upload_id"])
        target = self.service.state / "uploads" / f"{uploaded['upload_id']}.json"
        write = core.atomic_bytes

        def fail_writeback(path, data, *args, **kwargs):
            if path == target and b'"status": "ACTIVE_COMMITTED"' in data:
                raise OSError("Fixture post-commit writeback interruption.")
            return write(path, data, *args, **kwargs)

        with patch.object(core, "atomic_bytes", fail_writeback), self.assertRaises(OSError):
            self.service.approve(
                review["batch_id"], review["batch_digest"], review["required_confirmation"]
            )
        committed = self.service.runtime.approval_result(review["batch_id"], review["batch_digest"])
        self.assertEqual("ACTIVE_COMMITTED", committed["status"])
        return uploaded, review, committed["commit"]

    def maintain(self, action):
        plan = self.service.maintenance_plan(action, 0)
        return self.service.maintenance_apply(plan, plan["digest"])

    def test_reverted_archived_upload_never_reenters_review_after_writeback_failure(self):
        uploaded, review, commit = self.approved_without_writeback()
        uid = uploaded["upload_id"]
        self.maintain("archive")
        self.service.runtime.revert(commit, f"确认回滚记忆提交 {commit}")
        head = core.git_head(self.service.store)
        self.assertEqual("COMMIT_NOT_CURRENT", self.service.upload_status(uid)["status"])
        self.assertEqual("COMMIT_NOT_CURRENT", self.service.review(uid)["status"])
        self.assertEqual("archive", self.service.upload_status(uid)["storage"])
        self.assertEqual(
            "COMMIT_NOT_CURRENT",
            self.service.upload(**self.preference(), request_id="fixture-stable-request")["status"],
        )
        self.assertEqual(head, core.git_head(self.service.store))
        self.assertEqual([], self.service.recall("preference.editor.theme")["entries"])

    def test_reverted_hot_upload_recovers_proof_without_a_prior_status_read(self):
        uploaded, review, commit = self.approved_without_writeback()
        self.service.runtime.revert(commit, f"确认回滚记忆提交 {commit}")
        self.assertEqual(
            "COMMIT_NOT_CURRENT", self.service.upload_status(uploaded["upload_id"])["status"]
        )
        self.assertEqual("COMMIT_NOT_CURRENT", self.service.review(uploaded["upload_id"])["status"])

    def test_maintenance_can_archive_reverted_hot_proof_before_any_status_read(self):
        uploaded, review, commit = self.approved_without_writeback()
        self.service.runtime.revert(commit, f"确认回滚记忆提交 {commit}")
        self.maintain("archive")
        result = self.service.upload_status(uploaded["upload_id"])
        self.assertEqual("archive", result["storage"])
        self.assertEqual("COMMIT_NOT_CURRENT", result["status"])

    def test_storage_form_cannot_change_terminal_lifecycle(self):
        for transition in ("revert", "revoke", "supersede"):
            for storage in ("hot", "archive", "receipt"):
                with self.subTest(transition=transition, storage=storage):
                    self.service = Service(
                        self.root / f"{transition}-{storage}", self.project, "source-alpha"
                    )
                    self.service.initialize("project-fixture", "team")
                    uploaded, _, commit = self.approved_without_writeback()
                    if storage != "hot":
                        self.maintain("archive")
                    if storage == "receipt":
                        self.maintain("purge")
                    if transition == "revert":
                        self.service.runtime.revert(commit, f"确认回滚记忆提交 {commit}")
                    elif transition == "revoke":
                        plan = self.service.revoke_plan("source-alpha")
                        self.service.revoke_apply("source-alpha", plan["digest"])
                    else:
                        replacement = self.service.upload(
                            **self.preference(value="The editor theme is green.")
                        )
                        review = self.service.review(replacement["upload_id"])
                        self.service.approve(
                            review["batch_id"],
                            review["batch_digest"],
                            review["required_confirmation"],
                        )
                    expected = (
                        "COMMIT_NOT_CURRENT" if transition == "revert" else "COMMITTED_NOT_ACTIVE"
                    )
                    uid = uploaded["upload_id"]
                    head = core.git_head(self.service.store)
                    self.assertEqual(expected, self.service.upload_status(uid)["status"])
                    self.assertEqual(expected, self.service.review(uid)["status"])
                    self.assertEqual(storage, self.service.upload_status(uid)["storage"])
                    self.assertEqual(head, core.git_head(self.service.store))

    def test_approval_retry_reads_cold_terminal_proof_without_recreating_hot_job(self):
        uploaded, review, commit = self.approved_without_writeback()
        self.maintain("archive")
        self.service.runtime.revert(commit, f"确认回滚记忆提交 {commit}")
        head = core.git_head(self.service.store)
        result = self.service.approve(
            review["batch_id"], review["batch_digest"], review["required_confirmation"]
        )
        self.assertEqual("COMMIT_NOT_CURRENT", result["status"])
        self.assertEqual("archive", self.service.upload_status(uploaded["upload_id"])["storage"])
        self.assertEqual(head, core.git_head(self.service.store))

    def test_archive_identity_mismatch_is_never_merged(self):
        uploaded, _, _ = self.approved_without_writeback()
        self.maintain("archive")
        uid = uploaded["upload_id"]
        path = self.service.state / "receipts" / f"{uid}.json"
        original = core.load_json(path)
        for field, value in (
            ("source_agent", "source-other"),
            ("input_digest", "sha256:" + "1" * 64),
            ("context", {"scope": "project", "project_id": "project-other"}),
            ("batch_id", "batch-other"),
            ("batch_digest", "sha256:" + "2" * 64),
        ):
            with self.subTest(field=field):
                core.atomic_json(path, {**original, field: value})
                with self.assertRaises(core.MemoryError) as err:
                    self.service.upload_status(uid)
                self.assertEqual("ARCHIVE_RECEIPT_MISMATCH", err.exception.code)
        core.atomic_json(path, original)
        self.assertEqual("ACTIVE_COMMITTED", self.service.upload_status(uid)["status"])

    def test_failed_approval_preserves_unrelated_concurrent_worktree_edits(self):
        uploaded = self.service.upload(**self.preference())
        review = self.service.review(uploaded["upload_id"])
        unrelated = self.service.store / "catalog.json"
        original = core.load_json(unrelated)
        run = core.run_git

        def fail_commit(root, arguments, **kwargs):
            if root == self.service.store and arguments[0] == "commit":
                core.atomic_json(unrelated, {**original, "fixture_unrelated": "preserve"})
                raise RuntimeError("Fixture commit failed after an unrelated edit.")
            return run(root, arguments, **kwargs)

        with patch.object(core, "run_git", fail_commit), self.assertRaises(RuntimeError):
            self.service.approve(
                review["batch_id"], review["batch_digest"], review["required_confirmation"]
            )
        self.assertEqual("preserve", core.load_json(unrelated).get("fixture_unrelated"))
        self.assertEqual([], self.service.runtime.active_records(None))

    def test_historical_approval_still_requires_the_exact_batch_digest(self):
        _, review, commit = self.approved_without_writeback()
        self.service.runtime.revert(commit, f"确认回滚记忆提交 {commit}")
        with self.assertRaises(core.MemoryError) as err:
            self.service.runtime.approval_result(review["batch_id"], "sha256:" + "0" * 64)
        self.assertEqual("BATCH_CHANGED", err.exception.code)

    def test_purge_cannot_remove_evidence_of_an_archive_identity_mismatch(self):
        uploaded, _, _ = self.approved_without_writeback()
        self.maintain("archive")
        uid = uploaded["upload_id"]
        receipt_path = self.service.state / "receipts" / f"{uid}.json"
        original = core.load_json(receipt_path)
        archive_path = self.service.state / original["archive_path"]
        archive_bytes = archive_path.read_bytes()
        core.atomic_json(receipt_path, {**original, "input_digest": "sha256:" + "f" * 64})
        with self.assertRaises(core.MemoryError) as err:
            self.maintain("purge")
        self.assertEqual("ARCHIVE_RECEIPT_MISMATCH", err.exception.code)
        self.assertEqual(archive_bytes, archive_path.read_bytes())
        self.assertIn("archive_path", core.load_json(receipt_path))

    def test_receipt_only_job_cannot_claim_another_uploads_approval(self):
        uploaded, _, _ = self.approved_without_writeback()
        self.maintain("archive")
        self.maintain("purge")
        other = self.service.upload(**self.preference(key="preference.editor.other"))
        review = self.service.review(other["upload_id"])
        self.service.approve(
            review["batch_id"], review["batch_digest"], review["required_confirmation"]
        )
        uid = uploaded["upload_id"]
        path = self.service.state / "receipts" / f"{uid}.json"
        core.atomic_json(
            path,
            {
                **core.load_json(path),
                "batch_id": review["batch_id"],
                "batch_digest": review["batch_digest"],
            },
        )
        with self.assertRaises(core.MemoryError) as err:
            self.service.upload_status(uid)
        self.assertEqual("APPROVAL_UPLOAD_MISMATCH", err.exception.code)


if __name__ == "__main__":
    unittest.main()
