#!/usr/bin/env python3
"""Isolated acceptance tests for the user-owned Git Memory Graph."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import simple_memory as memory_module

from simple_memory import (
    IMPORT_TYPE,
    MemoryError,
    SimpleMemory,
    canonical_json,
    git_head,
    load_json,
    sha256_bytes,
)


class SimpleMemoryAcceptance(unittest.TestCase):
    AGENT_ID = "test-agent"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memorygraph-simple-")
        self.root = Path(self.temporary.name)
        self.previous_memory_root = os.environ.get("MEMORY_GRAPH_CODEX_MEMORY_ROOT")
        self.memory_root = self.root / "codex-memories"
        self.memory_root.mkdir()
        (self.memory_root / "MEMORY.md").write_text("# Durable prior memory\n", encoding="utf-8")
        os.environ["MEMORY_GRAPH_CODEX_MEMORY_ROOT"] = str(self.memory_root)
        self.source = self.root / "source"
        self.source.mkdir()
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.name", "Simple Test"], check=True)
        subprocess.run(["git", "-C", str(self.source), "config", "user.email", "simple@invalid"], check=True)
        (self.source / "authority.md").write_text("# Current authority\n\nCargo uses project rules.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.source), "add", "authority.md"], check=True)
        subprocess.run(["git", "-C", str(self.source), "commit", "-q", "-m", "authority"], check=True)
        self.source_commit = subprocess.run(
            ["git", "-C", str(self.source), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        self.source_hash = "sha256:" + hashlib.sha256((self.source / "authority.md").read_bytes()).hexdigest()
        self.store_path = self.root / "store"
        self.backups = self.root / "backups"
        self.store = SimpleMemory(self.store_path)
        self.source_status_before = subprocess.run(
            ["git", "-C", str(self.source), "status", "--porcelain"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.store.init(self.source, "project-test", "Test Project", self.backups, confirmed=True)

    def tearDown(self) -> None:
        if self.previous_memory_root is None:
            os.environ.pop("MEMORY_GRAPH_CODEX_MEMORY_ROOT", None)
        else:
            os.environ["MEMORY_GRAPH_CODEX_MEMORY_ROOT"] = self.previous_memory_root
        self.temporary.cleanup()

    def _candidate(
        self,
        key: str,
        body: str,
        *,
        scope: str = "project",
        memory_class: str = "decision",
        operation: str = "promote",
        expected_current_id: str | None = None,
        agent_id: str = AGENT_ID,
    ) -> dict[str, object]:
        source = (
            {
                "kind": "git",
                "project_root": str(self.source),
                "path": "authority.md",
                "commit": self.source_commit,
                "hash": self.source_hash,
                "locator": "authority.md",
            }
            if scope == "project"
            else {
                "kind": "user-instruction",
                "thread_id": "thread-test",
                "message_digest": "sha256:" + "1" * 64,
                "locator": "owner instruction",
            }
        )
        return {
            "scope": scope,
            "class": memory_class,
            "key": key,
            "body": body,
            "source": source,
            "nodes": [
                {
                    "id": "node-" + hashlib.sha256(key.encode()).hexdigest()[:16],
                    "title": key,
                    "keywords": [key, "cargo"],
                    "aliases": [],
                    "related_node_ids": [],
                }
            ],
            "operation": operation,
            "expected_current_id": expected_current_id,
            "valid_from": "2026-08-21",
            "submitted_by_agent": agent_id,
        }

    def _approve(self, candidates: list[dict[str, object]]) -> dict[str, object]:
        proposed = [self.store.propose(value, self.source) for value in candidates]
        batch = self.store.create_batch([str(value["candidate_id"]) for value in proposed])
        text = f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}"
        agents = {str(value["submitted_by_agent"]) for value in proposed}
        self.assertEqual(len(agents), 1)
        return self.store.approve(
            str(batch["batch_id"]),
            str(batch["batch_digest"]),
            text,
            committing_agent=next(iter(agents)),
        )

    def _enable_snapshots_for_test(self) -> None:
        policy_path = self.store_path / "policy.json"
        policy = load_json(policy_path)
        policy["snapshots_enabled"] = True
        policy["backup_root"] = str(self.backups)
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(self.store_path), "add", "policy.json"], check=True)
        subprocess.run(
            ["git", "-C", str(self.store_path), "commit", "-q", "-m", "test: enable snapshots"],
            check=True,
        )

    def test_init_and_all_operations_leave_source_repository_untouched(self) -> None:
        self._approve([self._candidate("cargo.rule", "Cargo rule is current.")])
        after = subprocess.run(
            ["git", "-C", str(self.source), "status", "--porcelain"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertEqual(after, self.source_status_before)
        self.assertFalse((self.source / ".memory").exists())
        self.assertFalse((self.source / ".memory-local").exists())

    def test_candidate_is_never_recalled_before_approval(self) -> None:
        self.store.propose(self._candidate("pending.rule", "PENDING_CANARY"), self.source)
        recalled = self.store.recall(self.source, "PENDING_CANARY")
        self.assertEqual(recalled["entries"], [])
        self.assertFalse(recalled["candidate_entries_included"])

    def test_exact_owner_confirmation_is_required(self) -> None:
        candidate = self.store.propose(self._candidate("confirm.rule", "Confirmed only."), self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        with self.assertRaises(MemoryError) as error:
            self.store.approve(
                str(batch["batch_id"]),
                str(batch["batch_digest"]),
                "确认",
                committing_agent=self.AGENT_ID,
            )
        self.assertEqual(error.exception.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(self.store.recall(self.source, "Confirmed only")["entries"], [])

    def test_candidate_change_after_review_invalidates_batch(self) -> None:
        candidate = self.store.propose(self._candidate("tamper.rule", "ORIGINAL"), self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        path = self.store.queue / "candidates" / f"{candidate['candidate_id']}.json"
        changed = load_json(path)
        changed["body"] = "CHANGED"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(MemoryError) as error:
            self.store.review(str(batch["batch_id"]))
        self.assertEqual(error.exception.code, "CANDIDATE_CHANGED")

    def test_only_originating_agent_can_commit_owner_confirmed_batch(self) -> None:
        candidate = self.store.propose(
            self._candidate("agent.owner", "ORIGINATING_AGENT_ONLY", agent_id="agent-alpha"),
            self.source,
        )
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        text = f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}"
        with self.assertRaises(MemoryError) as error:
            self.store.approve(
                str(batch["batch_id"]),
                str(batch["batch_digest"]),
                text,
                committing_agent="agent-beta",
            )
        self.assertEqual(error.exception.code, "AGENT_MISMATCH")
        self.assertEqual(self.store.recall(self.source, "ORIGINATING_AGENT_ONLY")["entries"], [])
        approved = self.store.approve(
            str(batch["batch_id"]),
            str(batch["batch_digest"]),
            text,
            committing_agent="agent-alpha",
        )
        self.assertEqual(approved["committed_by_agent"], "agent-alpha")
        receipt = load_json(self.store_path / "approvals" / f"{batch['batch_id']}.json")
        self.assertEqual(receipt["submitting_agent"], "agent-alpha")
        self.assertEqual(receipt["committed_by_agent"], "agent-alpha")

    def test_candidates_from_different_agents_cannot_share_batch(self) -> None:
        first = self.store.propose(
            self._candidate("agent.first", "FIRST_AGENT", agent_id="agent-alpha"), self.source
        )
        second = self.store.propose(
            self._candidate("agent.second", "SECOND_AGENT", agent_id="agent-beta"), self.source
        )
        with self.assertRaises(MemoryError) as error:
            self.store.create_batch([str(first["candidate_id"]), str(second["candidate_id"])])
        self.assertEqual(error.exception.code, "BATCH_AGENT_MIXED")

    def test_multi_candidate_batch_commits_every_record_and_node(self) -> None:
        result = self._approve(
            [
                self._candidate("cargo.one", "First cargo decision."),
                self._candidate("cargo.two", "Second cargo decision."),
            ]
        )
        self.assertEqual(result["status"], "ACTIVE_COMMITTED")
        self.assertEqual(len(result["record_ids"]), 2)
        self.assertEqual(len(self.store.recall(self.source, "cargo")["entries"]), 2)
        approval = load_json(self.store_path / "approvals" / f"{result['batch_id']}.json")
        self.assertEqual(len(approval["records"]), 2)

    def test_active_conflict_requires_explicit_supersede(self) -> None:
        first = self._approve([self._candidate("cargo.conflict", "OLD")])
        with self.assertRaises(MemoryError) as conflict:
            self._approve([self._candidate("cargo.conflict", "NEW")])
        self.assertEqual(conflict.exception.code, "ACTIVE_CONFLICT")
        old_id = str(first["record_ids"][0])
        result = self._approve(
            [self._candidate("cargo.conflict", "NEW", operation="supersede", expected_current_id=old_id)]
        )
        recalled = self.store.recall(self.source, "cargo.conflict")
        self.assertEqual(len(recalled["entries"]), 1)
        newest = next(value for value in recalled["entries"] if value["id"] == result["record_ids"][0])
        self.assertEqual(newest["body"], "NEW")
        self.assertEqual(newest["supersedes"], old_id)

    def test_global_preference_is_visible_outside_project_but_project_memory_is_not(self) -> None:
        self._approve(
            [
                self._candidate("global.style", "GLOBAL_VISIBLE", scope="global", memory_class="preference"),
                self._candidate("project.secret", "PROJECT_ONLY"),
            ]
        )
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        subprocess.run(["git", "init", "-q", str(unrelated)], check=True)
        self.assertEqual(self.store.recall(unrelated, "PROJECT_ONLY")["entries"], [])
        self.assertEqual(self.store.recall(unrelated, "GLOBAL_VISIBLE")["entries"][0]["body"], "GLOBAL_VISIBLE")

    def test_register_second_project_and_prevent_cross_project_recall(self) -> None:
        second = self.root / "second-project"
        second.mkdir()
        subprocess.run(["git", "init", "-q", str(second)], check=True)
        subprocess.run(["git", "-C", str(second), "config", "user.name", "Second"], check=True)
        subprocess.run(["git", "-C", str(second), "config", "user.email", "second@invalid"], check=True)
        (second / "README.md").write_text("second\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(second), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(second), "commit", "-q", "-m", "init"], check=True)
        registered = self.store.register_project(second, "project-second", "Second Project", confirmed=True)
        self.assertEqual(registered["status"], "PROJECT_REGISTERED")
        self._approve([self._candidate("first.only", "FIRST_PROJECT_ONLY")])
        self.assertEqual(self.store.recall(second, "FIRST_PROJECT_ONLY")["entries"], [])

    def test_memory_file_source_is_hash_bound(self) -> None:
        path = self.memory_root / "MEMORY.md"
        raw = self._candidate("prior.preference", "PRIOR_MEMORY", scope="global", memory_class="preference")
        raw["source"] = {
            "kind": "memory-file",
            "path": str(path),
            "hash": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "locator": "MEMORY.md:1",
        }
        candidate = self.store.propose(raw, self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        path.write_text("changed\n", encoding="utf-8")
        text = f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}"
        with self.assertRaises(MemoryError) as error:
            self.store.approve(
                str(batch["batch_id"]), str(batch["batch_digest"]), text,
                committing_agent=self.AGENT_ID,
            )
        self.assertEqual(error.exception.code, "SOURCE_STALE")

    def test_project_head_drift_invalidates_pending_batch(self) -> None:
        candidate = self.store.propose(self._candidate("source.head", "HEAD_BOUND"), self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        (self.source / "later.md").write_text("later\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.source), "add", "later.md"], check=True)
        subprocess.run(["git", "-C", str(self.source), "commit", "-q", "-m", "later"], check=True)
        text = f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}"
        with self.assertRaises(MemoryError) as error:
            self.store.approve(
                str(batch["batch_id"]), str(batch["batch_digest"]), text,
                committing_agent=self.AGENT_ID,
            )
        self.assertEqual(error.exception.code, "SOURCE_STALE")
        self.assertEqual(self.store.recall(self.source, "HEAD_BOUND")["entries"], [])

    def test_secret_or_embedded_instruction_is_quarantined(self) -> None:
        raw = self._candidate("secret.rule", "password=supersecretvalue")
        candidate = self.store.propose(raw, self.source)
        self.assertEqual(candidate["status"], "quarantine")
        self.assertEqual(candidate["body"], "[REDACTED QUARANTINE]")
        with self.assertRaises(MemoryError) as error:
            self.store.create_batch([str(candidate["candidate_id"])])
        self.assertEqual(error.exception.code, "BATCH_INVALID")

    def test_unknown_node_relation_blocks_whole_batch(self) -> None:
        raw = self._candidate("node.rule", "NODE_CANARY")
        raw["nodes"][0]["related_node_ids"] = ["node-does-not-exist"]
        candidate = self.store.propose(raw, self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        text = f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}"
        with self.assertRaises(MemoryError) as error:
            self.store.approve(
                str(batch["batch_id"]), str(batch["batch_digest"]), text,
                committing_agent=self.AGENT_ID,
            )
        self.assertEqual(error.exception.code, "UNKNOWN_NODE")
        self.assertEqual(self.store.recall(self.source, "NODE_CANARY")["entries"], [])

    def test_fault_after_writes_leaves_no_active_or_git_commit(self) -> None:
        candidate = self.store.propose(self._candidate("fault.rule", "FAULT_CANARY"), self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        before = git_head(self.store_path)
        text = f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}"
        original_write = memory_module.atomic_json
        def fail_receipt(path, *args, **kwargs):
            if Path(path).parent.name == "approvals":
                raise OSError("simulated receipt write failure")
            return original_write(path, *args, **kwargs)
        with patch.object(memory_module, "atomic_json", side_effect=fail_receipt), self.assertRaises(OSError):
            self.store.approve(
                str(batch["batch_id"]), str(batch["batch_digest"]), text,
                committing_agent=self.AGENT_ID,
            )
        self.assertEqual(git_head(self.store_path), before)
        self.assertEqual(self.store.recall(self.source, "FAULT_CANARY")["entries"], [])

    def test_plain_backup_verifies_and_restores_head(self) -> None:
        self._enable_snapshots_for_test()
        approved = self._approve([self._candidate("backup.rule", "BACKUP_CANARY")])
        backup = self.store.create_backup(self.backups)
        verified = SimpleMemory.verify_backup(Path(str(backup["directory"])))
        self.assertEqual(verified["head"], approved["commit"])
        restored = self.root / "restored.git"
        subprocess.run(
            ["git", "clone", "-q", str(Path(str(backup["directory"])) / "MemoryGraph-Simple.bundle"), str(restored)],
            check=True,
        )
        self.assertEqual(
            subprocess.run(["git", "-C", str(restored), "rev-parse", "HEAD"], check=True, stdout=subprocess.PIPE, text=True).stdout.strip(),
            approved["commit"],
        )

    def test_backup_failure_is_reported_without_hiding_committed_memory(self) -> None:
        self._enable_snapshots_for_test()
        candidate = self.store.propose(self._candidate("backup.failure", "COMMIT_SURVIVES"), self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        text = f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}"
        result = self.store.approve(
            str(batch["batch_id"]), str(batch["batch_digest"]), text,
            committing_agent=self.AGENT_ID,
            backup_root=self.store_path / "invalid-overlap",
        )
        self.assertEqual(result["status"], "ACTIVE_COMMITTED_BACKUP_FAILED")
        self.assertEqual(result["backup"]["code"], "BACKUP_ROOT_INVALID")
        self.assertEqual(self.store.recall(self.source, "COMMIT_SURVIVES")["entries"][0]["body"], "COMMIT_SURVIVES")

    def test_backup_checksum_tamper_is_rejected(self) -> None:
        self._enable_snapshots_for_test()
        self._approve([self._candidate("backup.tamper", "BACKUP_TAMPER")])
        backup = self.store.create_backup(self.backups)
        sums = Path(str(backup["directory"])) / "SHA256SUMS"
        sums.write_text("0" * 64 + "  MemoryGraph-Simple.bundle\n", encoding="utf-8")
        with self.assertRaises(MemoryError) as error:
            SimpleMemory.verify_backup(Path(str(backup["directory"])))
        self.assertEqual(error.exception.code, "BACKUP_VERIFY_FAILED")

    def test_plain_backup_includes_pending_candidates_outside_git_bundle(self) -> None:
        self._enable_snapshots_for_test()
        candidate = self.store.propose(self._candidate("backup.pending", "PENDING_BACKUP"), self.source)
        batch = self.store.create_batch([str(candidate["candidate_id"])])
        backup = self.store.create_backup(self.backups)
        directory = Path(str(backup["directory"]))
        manifest = load_json(directory / "manifest.json")
        self.assertEqual(len(manifest["pending_files"]), 2)
        self.assertTrue(any(str(item["path"]).endswith(f"{candidate['candidate_id']}.json") for item in manifest["pending_files"]))
        self.assertTrue(any(str(item["path"]).endswith(f"{batch['batch_id']}.json") for item in manifest["pending_files"]))
        self.assertEqual(SimpleMemory.verify_backup(directory)["status"], "BACKUP_VERIFIED")

    def test_plain_backup_rejects_undeclared_non_appledouble_file(self) -> None:
        self._enable_snapshots_for_test()
        backup = self.store.create_backup(self.backups)
        directory = Path(str(backup["directory"]))
        (directory / "undeclared.txt").write_text("undeclared\n", encoding="utf-8")
        with self.assertRaises(MemoryError) as error:
            SimpleMemory.verify_backup(directory)
        self.assertEqual(error.exception.code, "BACKUP_VERIFY_FAILED")

    def test_snapshots_are_disabled_until_owner_enables_policy(self) -> None:
        self.assertFalse(load_json(self.store_path / "policy.json")["snapshots_enabled"])
        with self.assertRaises(MemoryError) as error:
            self.store.create_backup(self.backups)
        self.assertEqual(error.exception.code, "SNAPSHOT_DISABLED")
        self.assertFalse(self.backups.exists())

    def test_git_revert_requires_confirmation_and_preserves_history(self) -> None:
        approved = self._approve([self._candidate("revert.rule", "REVERT_CANARY")])
        with self.assertRaises(MemoryError) as error:
            self.store.revert(str(approved["commit"]), "wrong")
        self.assertEqual(error.exception.code, "CONFIRMATION_REQUIRED")
        result = self.store.revert(
            str(approved["commit"]), f"确认回滚记忆提交 {approved['commit']}"
        )
        self.assertEqual(result["status"], "REVERT_COMMITTED")
        self.assertNotEqual(result["commit"], approved["commit"])
        self.assertEqual(self.store.recall(self.source, "REVERT_CANARY")["entries"], [])

    def test_pending_inventory_import_creates_reviewable_batch_only(self) -> None:
        raw = self._candidate("import.rule", "IMPORT_PENDING")
        inventory = {
            "schema_version": 1,
            "type": IMPORT_TYPE,
            "batch_id": "source-batch-test",
            "created_at": "2026-08-21T00:00:00+00:00",
            "candidates": [raw],
        }
        inventory["batch_digest"] = sha256_bytes(canonical_json(inventory))
        path = self.root / "inventory.json"
        path.write_text(json.dumps(inventory), encoding="utf-8")
        result = self.store.import_pending(
            path, self.source, submitting_agent=self.AGENT_ID, confirmed=True
        )
        self.assertEqual(result["status"], "PENDING_IMPORTED_NOT_ACTIVE")
        self.assertEqual(result["batch"]["candidate_count"], 1)
        self.assertEqual(self.store.recall(self.source, "IMPORT_PENDING")["entries"], [])

    def test_pending_inventory_failure_leaves_no_partial_candidates(self) -> None:
        first = self._candidate("import.first", "FIRST_PENDING")
        second = self._candidate("import.second", "SECOND_PENDING")
        second["source"]["hash"] = "sha256:" + "0" * 64
        inventory = {
            "schema_version": 1,
            "type": IMPORT_TYPE,
            "batch_id": "source-batch-failure",
            "created_at": "2026-08-21T00:00:00+00:00",
            "candidates": [first, second],
        }
        inventory["batch_digest"] = sha256_bytes(canonical_json(inventory))
        path = self.root / "invalid-inventory.json"
        path.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaises(MemoryError) as error:
            self.store.import_pending(
                path, self.source, submitting_agent=self.AGENT_ID, confirmed=True
            )
        self.assertEqual(error.exception.code, "SOURCE_STALE")
        self.assertEqual(list((self.store.queue / "candidates").glob("candidate-*.json")), [])
        self.assertEqual(list((self.store.queue / "batches").glob("batch-*.json")), [])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--output":
        output = Path(sys.argv[2]).expanduser().resolve()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SimpleMemoryAcceptance)
        test_ids = [case.id() for case in suite]
        started = time.monotonic()
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        report = {
            "status": "PASS" if result.wasSuccessful() else "FAIL",
            "tests": result.testsRun,
            "duration_seconds": round(time.monotonic() - started, 3),
            "test_ids": test_ids,
            "failures": [case.id() for case, _ in result.failures],
            "errors": [case.id() for case, _ in result.errors],
            "skipped": [case.id() for case, _ in result.skipped],
            "real_project_modified": False,
            "real_system_modified": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main(verbosity=2)
