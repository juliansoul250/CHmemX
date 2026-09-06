"""Maintenance touches only disposable fixtures; production cleanup is never a test."""

import base64
import datetime as dt
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

import test_service as fixtures
from chmemx.runtime import simple_memory as core
from chmemx.service import Service


class MaintenanceAcceptance(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown
    preference = fixtures.ServiceAcceptance.preference
    set_policy = fixtures.ServiceAcceptance.set_policy
    approve_upload = fixtures.ServiceAcceptance.approve_upload

    def apply(self, action="archive", days=0):
        plan = self.service.maintenance_plan(action, days)
        return self.service.maintenance_apply(plan, plan["digest"])

    def closed(self, request_id="fixture-closed"):
        job = self.service.upload(**self.preference(), request_id=request_id)
        state = self.service.upload_status(job["upload_id"])
        self.service.close_upload(
            job["upload_id"], "cancel", "Fixture no longer needed.", state["job_digest"]
        )
        return job

    def test_full_queue_drains_and_idempotency_survives_archive_and_purge(self):
        self.set_policy(queue_limits={"max_uploads": 1})
        upload = self.service.upload(**self.preference(), request_id="first")
        self.approve_upload(upload)
        before = core.git_head(self.service.store)
        self.assertEqual("MAINTENANCE_COMPLETE", self.apply()["status"])
        self.assertEqual(0, self.service.queue.maintenance_plan()["uploads"])
        state = self.service.upload_status(upload["upload_id"])
        self.assertEqual("ACTIVE_COMMITTED", state["status"])
        self.assertEqual("archive", state["storage"])
        self.apply("purge")
        self.assertEqual("receipt", self.service.upload_status(upload["upload_id"])["storage"])
        with self.assertRaises(core.MemoryError) as err:
            self.service.upload(**self.preference(value="Changed."), request_id="first")
        self.assertEqual("IDEMPOTENCY_CONFLICT", err.exception.code)
        self.assertEqual(
            "PENDING_CURATION",
            self.service.upload(**self.preference(key="preference.other"), request_id="new")[
                "status"
            ],
        )
        self.assertEqual(before, core.git_head(self.service.store))

    def test_unreviewed_jobs_are_not_archived(self):
        upload = self.service.upload(**self.preference())
        plan = self.service.maintenance_plan("archive", 0)
        self.assertEqual([], plan["targets"]["jobs"])
        self.service.maintenance_apply(plan, plan["digest"])
        self.assertEqual("hot", self.service.upload_status(upload["upload_id"])["storage"])

    def test_refresh_history_compacts_without_deleting_current_review(self):
        upload = self.service.upload(**self.preference())
        for _ in range(5):
            review = self.service.review(upload["upload_id"], refresh=True)
        self.apply()
        q = self.service.runtime.queue
        self.assertEqual(1, len(list((q / "candidates").glob("*.json"))))
        self.assertEqual(1, len(list((q / "batches").glob("*.json"))))
        self.assertEqual(1, len(list((self.service.state / "history").glob("*.gz"))))
        self.assertEqual(
            review["batch_digest"], self.service.review(upload["upload_id"])["batch_digest"]
        )
        self.service.review(upload["upload_id"], refresh=True)
        self.apply()
        self.assertEqual(1, len(list((self.service.state / "history").glob("*.gz"))))

    def test_state_drift_rejects_plan(self):
        self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        self.service.upload(**self.preference(key="preference.later"))
        with self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_apply(plan, plan["digest"])
        self.assertEqual("MAINTENANCE_PLAN_CHANGED", err.exception.code)

    def test_digest_change_and_symlink_are_rejected(self):
        upload = self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        with self.assertRaises(core.MemoryError):
            self.service.maintenance_apply(plan, "sha256:" + "0" * 64)
        link = self.service.state / "uploads" / "upload-link.json"
        try:
            link.symlink_to(self.service.state / "uploads" / f"{upload['upload_id']}.json")
        except OSError:
            self.skipTest("Host does not grant symlink creation to the test process")
        with self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_plan("archive", 0)
        self.assertEqual("MAINTENANCE_PATH_INVALID", err.exception.code)

    def test_maintenance_apply_is_idempotent(self):
        self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        first = self.service.maintenance_apply(plan, plan["digest"])
        self.assertEqual(first, self.service.maintenance_apply(plan, plan["digest"]))

    def test_missing_recovery_transaction_has_a_domain_not_found_code(self):
        head = core.git_head(self.service.store)
        with self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_recover("txn-absent")
        self.assertEqual("TRANSACTION_NOT_FOUND", err.exception.code)
        self.assertEqual(head, core.git_head(self.service.store))

    def test_canonical_proofs_use_one_batch_reader(self):
        for i in range(3):
            self.approve_upload(
                self.service.upload(**self.preference(key=f"preference.batch.item{i}"))
            )
        real = subprocess.Popen
        with patch.object(subprocess, "Popen", wraps=real) as calls:
            plan = self.service.maintenance_plan("archive", 0)
        commands = [call.args[0] for call in calls.call_args_list]
        self.assertEqual(1, sum("cat-file" in cmd and "--batch" in cmd for cmd in commands))
        self.assertFalse(any("show" in cmd for cmd in commands))
        self.assertEqual(3, len(plan["targets"]["jobs"]))

    def test_pending_reviews_share_one_git_history_walk(self):
        for i in range(5):
            upload = self.service.upload(**self.preference(key=f"preference.pending.item{i}"))
            self.service.review(upload["upload_id"])
        real = subprocess.Popen
        with patch.object(subprocess, "Popen", wraps=real) as calls:
            plan = self.service.maintenance_plan("archive", 0, limit=1)
        commands = [call.args[0] for call in calls.call_args_list]
        self.assertEqual([], plan["targets"]["jobs"])
        self.assertLessEqual(sum("log" in command for command in commands), 1)
        self.assertLessEqual(len(commands), 10, "No per-Pending Git process fanout.")

    def test_cannot_close_active_memory(self):
        upload = self.service.upload(**self.preference())
        self.approve_upload(upload)
        state = self.service.upload_status(upload["upload_id"])
        with self.assertRaises(core.MemoryError) as err:
            self.service.close_upload(
                upload["upload_id"], "cancel", "Fixture.", state["job_digest"]
            )
        self.assertEqual("UPLOAD_ALREADY_TERMINAL", err.exception.code)

    def test_unindexed_payload_can_be_reconciled_without_content_loss(self):
        upload = self.service.upload(**self.preference())
        with self.service.queue.locked() as state:
            state["uploads"].pop(upload["upload_id"])
        plan = self.service.maintenance_plan("archive", 0)
        self.assertEqual(["QUEUE_RECONCILIATION_REQUIRED"], plan["blockers"])
        self.apply("reconcile")
        self.assertEqual(1, self.service.queue.maintenance_plan()["uploads"])
        self.assertEqual(
            "PENDING_CURATION", self.service.upload_status(upload["upload_id"])["status"]
        )

    def test_missing_committed_payload_recovers_proof_not_missing_label(self):
        upload = self.service.upload(**self.preference())
        self.approve_upload(upload)
        (self.service.state / "uploads" / f"{upload['upload_id']}.json").unlink()
        self.apply("reconcile")
        self.assertEqual(
            "ACTIVE_COMMITTED", self.service.upload_status(upload["upload_id"])["status"]
        )

    def interrupted(self):
        self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        original = core.atomic_bytes

        def fail_state(path, *args, **kwargs):
            if path == self.service.queue.path:
                raise OSError("Fixture disk interruption.")
            return original(path, *args, **kwargs)

        with (
            patch.object(core, "atomic_bytes", side_effect=fail_state),
            self.assertRaises(core.MemoryError) as err,
        ):
            self.service.maintenance_apply(plan, plan["digest"])
        self.assertEqual("MAINTENANCE_INTERRUPTED", err.exception.code)
        return err.exception.details["transaction_id"]

    def test_interruption_blocks_mutation_then_rolls_back(self):
        tx = self.interrupted()
        self.assertEqual(
            "MAINTENANCE_RECOVERY_REQUIRED", self.service.start()["queue_health"]["status"]
        )
        with self.assertRaises(core.MemoryError) as err:
            self.service.upload(**self.preference(key="preference.blocked"))
        self.assertEqual("MAINTENANCE_RECOVERY_REQUIRED", err.exception.code)
        self.assertEqual("MAINTENANCE_ROLLED_BACK", self.service.maintenance_recover(tx)["status"])
        self.assertEqual(1, self.service.queue.maintenance_plan()["uploads"])
        self.assertEqual("MAINTENANCE_COMPLETE", self.apply()["status"])

    def test_interruption_can_complete_without_half_commit(self):
        tx = self.interrupted()
        self.assertEqual(
            "MAINTENANCE_COMPLETE", self.service.maintenance_recover(tx, "complete")["status"]
        )
        self.assertEqual(0, self.service.queue.maintenance_plan()["uploads"])
        self.assertEqual(
            "MAINTENANCE_COMPLETE", self.service.maintenance_recover(tx, "complete")["status"]
        )

    def test_interrupted_preparation_is_tracked_and_can_rollback(self):
        self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        original = core.atomic_bytes

        def stop_staging(path, *args, **kwargs):
            if path.name.startswith("part-") and path.name.endswith("-after"):
                raise OSError("Fixture preparation interruption.")
            return original(path, *args, **kwargs)

        with (
            patch.object(core, "atomic_bytes", side_effect=stop_staging),
            self.assertRaises(core.MemoryError) as err,
        ):
            self.service.maintenance_apply(plan, plan["digest"])
        tx = err.exception.details["transaction_id"]
        self.assertEqual(
            "PREPARING", core.load_json(self.service.state / "maintenance/active.json")["phase"]
        )
        self.assertEqual("MAINTENANCE_ROLLED_BACK", self.service.maintenance_recover(tx)["status"])
        self.assertEqual(1, self.service.queue.maintenance_plan()["uploads"])

    def test_recovery_preserves_out_of_band_edits(self):
        tx = self.interrupted()
        journal = core.load_json(self.service.state / "maintenance/active.json")
        rel = next(r for r in journal["entries"] if r.startswith("chmemx/uploads/"))
        changed = self.service.runtime.queue / rel
        core.atomic_bytes(changed, b'{"external_edit":true}\n')
        with self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_recover(tx)
        self.assertEqual("RECOVERY_CONFLICT", err.exception.code)
        self.assertEqual(b'{"external_edit":true}\n', changed.read_bytes())

    def test_post_commit_cleanup_failure_can_finish_without_reapplying(self):
        self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        unlink = Path.unlink

        def stop_cleanup(path, *args, **kwargs):
            if path.name.startswith("part-"):
                raise OSError("Fixture cleanup interruption.")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", stop_cleanup), self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_apply(plan, plan["digest"])
        tx = err.exception.details["transaction_id"]
        self.assertEqual(
            "COMPLETED", core.load_json(self.service.state / "maintenance/active.json")["phase"]
        )
        with self.assertRaises(core.MemoryError) as rollback:
            self.service.maintenance_recover(tx, "rollback")
        self.assertEqual("RECOVERY_ALREADY_COMMITTED", rollback.exception.code)
        self.assertEqual(
            "MAINTENANCE_COMPLETE", self.service.maintenance_recover(tx, "complete")["status"]
        )

    def test_backup_is_blocked_during_queue_recovery(self):
        self.set_policy(snapshots_enabled=True)
        self.interrupted()
        with self.assertRaises(core.MemoryError) as err:
            self.service.runtime.create_backup(self.root / "backups")
        self.assertEqual("MAINTENANCE_RECOVERY_REQUIRED", err.exception.code)

    def test_completed_cleanup_survives_a_later_source_registration(self):
        self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        unlink = Path.unlink

        def stop_cleanup(path, *args, **kwargs):
            if path.name.startswith("part-"):
                raise OSError("Fixture cleanup interruption.")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", stop_cleanup), self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_apply(plan, plan["digest"])
        tx = err.exception.details["transaction_id"]
        self.service.trust_source("source-beta", base64.b64encode(bytes(range(32))).decode())
        head = core.git_head(self.service.store)
        self.assertEqual(
            "MAINTENANCE_COMPLETE", self.service.maintenance_recover(tx, "complete")["status"]
        )
        self.assertEqual(head, core.git_head(self.service.store))
        self.assertEqual("READY", self.service.start()["queue_health"]["status"])
        self.assertEqual(
            "PENDING_CURATION",
            self.service.upload(**self.preference(key="preference.next"))["status"],
        )

    def test_unfinished_maintenance_blocks_git_only_administration(self):
        tx = self.interrupted()
        head = core.git_head(self.service.store)
        with self.assertRaises(core.MemoryError) as err:
            self.service.trust_source("source-beta", base64.b64encode(bytes(range(32))).decode())
        self.assertEqual("MAINTENANCE_RECOVERY_REQUIRED", err.exception.code)
        plan = self.service.revoke_plan("source-alpha")
        with self.assertRaises(core.MemoryError) as err:
            self.service.revoke_apply("source-alpha", plan["digest"])
        self.assertEqual("MAINTENANCE_RECOVERY_REQUIRED", err.exception.code)
        with self.assertRaises(core.MemoryError) as err:
            self.service.runtime.register_project(
                self.project, "project-other", "Fixture", confirmed=True
            )
        self.assertEqual("MAINTENANCE_RECOVERY_REQUIRED", err.exception.code)
        self.assertEqual(head, core.git_head(self.service.store))
        self.assertEqual("MAINTENANCE_ROLLED_BACK", self.service.maintenance_recover(tx)["status"])

    def test_completed_cleanup_keeps_byte_checks_after_head_change(self):
        self.closed()
        plan = self.service.maintenance_plan("archive", 0)
        unlink = Path.unlink

        def stop_cleanup(path, *args, **kwargs):
            if path.name.startswith("part-"):
                raise OSError("Fixture cleanup interruption.")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", stop_cleanup), self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_apply(plan, plan["digest"])
        tx = err.exception.details["transaction_id"]
        self.service.trust_source("source-beta", base64.b64encode(bytes(range(32))).decode())
        state_path = self.service.queue.path
        changed = {**core.load_json(state_path), "fixture_external_edit": True}
        core.atomic_json(state_path, changed)
        with self.assertRaises(core.MemoryError) as err:
            self.service.maintenance_recover(tx, "complete")
        self.assertEqual("RECOVERY_CONFLICT", err.exception.code)
        self.assertEqual(changed, core.load_json(state_path))

    def test_reconcile_deleted_payload_keeps_tombstone_and_releases_slot(self):
        self.set_policy(queue_limits={"max_uploads": 1})
        upload = self.service.upload(**self.preference(), request_id="missing")
        (self.service.state / "uploads" / f"{upload['upload_id']}.json").unlink()
        self.apply("reconcile")
        self.assertEqual(0, self.service.queue.maintenance_plan()["uploads"])
        self.assertEqual(
            "UPLOAD_DATA_MISSING", self.service.upload_status(upload["upload_id"])["status"]
        )
        self.assertEqual(
            "UPLOAD_DATA_MISSING",
            self.service.upload(**self.preference(), request_id="missing")["status"],
        )
        self.assertEqual(
            "PENDING_CURATION",
            self.service.upload(**self.preference(key="preference.new"))["status"],
        )

    def test_new_events_do_not_reuse_retained_filenames(self):
        self.closed()
        self.apply()
        before = core.load_json(self.service.queue.path)["event_sequence"]
        self.service.upload(**self.preference(key="preference.next"))
        self.assertGreater(core.load_json(self.service.queue.path)["event_sequence"], before)

    def test_each_maintenance_operation_hashes_untargeted_archives_once(self):
        self.closed()
        self.apply()
        witness = next((self.service.state / "archives/uploads").glob("*.gz"))
        opened = []
        original = Path.open

        def observe(path, *args, **kwargs):
            if path == witness and (args[0] if args else kwargs.get("mode")) == "rb":
                opened.append(path)
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", observe):
            plan = self.service.maintenance_plan("archive", 30, limit=1)
        self.assertEqual(1, len(opened), "Plan must not repeat the full archive hash pass.")
        opened.clear()
        with patch.object(Path, "open", observe):
            self.service.maintenance_apply(plan, plan["digest"])
        self.assertEqual(
            1, len(opened), "Apply must take one fresh inventory, not reuse the old plan."
        )

    def test_event_write_interruption_is_visible_until_explicit_reconciliation(self):
        write = core.atomic_bytes

        def fail_event(path, *args, **kwargs):
            if path.parent == self.service.state / "events":
                raise OSError("Fixture event write interruption.")
            return write(path, *args, **kwargs)

        with patch.object(core, "atomic_bytes", fail_event), self.assertRaises(OSError):
            self.service.event("TEST_EVENT", upload_id="fixture-event")
        accounting = self.service.start()["queue_health"]["event_accounting"]
        self.assertEqual("RECONCILIATION_RECOMMENDED", accounting["status"])
        self.assertEqual(1, accounting["reserved_files"])
        self.service.event("TEST_EVENT", upload_id="fixture-next-event")
        self.assertEqual(
            "RECONCILIATION_RECOMMENDED",
            self.service.start()["queue_health"]["event_accounting"]["status"],
        )
        self.apply("reconcile")
        accounting = self.service.start()["queue_health"]["event_accounting"]
        self.assertEqual("NO_INTERRUPTED_WRITE_RECORDED", accounting["status"])
        self.assertEqual(1, accounting["reserved_files"])

    def test_archive_and_receipts_are_restorable(self):
        self.set_policy(snapshots_enabled=True)
        upload = self.service.upload(**self.preference(), request_id="restore")
        self.approve_upload(upload)
        self.apply()
        backup = self.service.runtime.create_backup(self.root / "backups")
        dest = self.root / "restored"
        core.SimpleMemory.restore_backup(Path(backup["directory"]), dest)
        other = Service(dest, self.project, "source-alpha")
        self.assertEqual("archive", other.upload_status(upload["upload_id"])["storage"])
        self.assertEqual(1, len(other.recall("preference.editor.theme")["entries"]))

    def test_receipt_expiry_is_separate_explicit_and_age_gated(self):
        self.approve_upload(self.service.upload(**self.preference()))
        receipt = self.service.upload(**self.preference(), request_id="old-no-op")
        with self.assertRaises(core.MemoryError):
            self.service.maintenance_plan("expire-receipts", 0)
        path = self.service.state / "receipts" / f"{receipt['upload_id']}.json"
        data = core.load_json(path)
        data["created_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=200)).isoformat()
        core.atomic_json(path, data)
        plan = self.service.maintenance_plan("expire-receipts", 180)
        self.assertEqual(1, len(plan["targets"]["receipts"]))
        before = core.git_head(self.service.store)
        self.service.maintenance_apply(plan, plan["digest"])
        self.assertFalse(path.exists())
        self.assertEqual(before, core.git_head(self.service.store))


if __name__ == "__main__":
    unittest.main()
