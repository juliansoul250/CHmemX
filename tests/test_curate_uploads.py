from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from curate_uploads import curate  # noqa: E402


def export(agent_id: str, export_id: str, body: str, key: str = "workflow.shared.rule") -> dict[str, object]:
    return {
        "schema_version": 1,
        "type": "memorygraph-agent-export-v1",
        "export_id": export_id,
        "agent_id": agent_id,
        "tool": agent_id,
        "created_at": "2026-08-23T00:00:00+00:00",
        "items": [
            {
                "export_item_id": f"item-{agent_id}",
                "scope": "global",
                "class": "preference",
                "key": key,
                "body": body,
                "source": {
                    "kind": "user-instruction",
                    "thread_id": "thread-shared",
                    "message_digest": "sha256:" + "1" * 64,
                    "locator": "owner instruction",
                },
                "nodes": [
                    {
                        "id": "node-shared-rule",
                        "title": "Shared rule",
                        "keywords": ["shared", "rule", "memory"],
                        "aliases": [],
                        "related_node_ids": [],
                    }
                ],
                "operation": "promote",
                "expected_current_id": None,
                "valid_from": "2026-08-23",
                "stale_when": "Owner changes the rule",
            }
        ],
        "rejected": [],
    }


class CurateUploadsAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_export(self, name: str, value: dict[str, object]) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def write_active_store(self, body: str) -> Path:
        store = self.root / "store"
        active = store / "global" / "active"
        active.mkdir(parents=True)
        (store / "catalog.json").write_text(
            json.dumps({"projects": {}}, ensure_ascii=False), encoding="utf-8"
        )
        item = export("historical-agent", "export-historical", body)["items"][0]
        record = {
            **item,
            "id": "memory-existing-record",
            "authority": "accepted",
            "status": "active",
            "project_id": None,
            "submitted_by_agent": "historical-agent",
            "committed_by_agent": "historical-agent",
        }
        record.pop("export_item_id")
        record.pop("operation")
        record.pop("expected_current_id")
        (active / "memory-existing-record.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        (store / "global" / "active-index.json").write_text(
            json.dumps(
                {"entries": {"preference:workflow.shared.rule": "memory-existing-record"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return store

    def test_exact_duplicates_collapse_and_preserve_origins(self) -> None:
        first = self.write_export("one.json", export("pi-main", "export-pi-one", "Same rule"))
        second = self.write_export(
            "two.json", export("claude-main", "export-claude-one", "Same rule")
        )
        inventory, report = curate(
            [first, second], "main-memory-curator", "curation-shared-one"
        )
        self.assertIsNotNone(inventory)
        self.assertEqual(1, report["accepted_count"])
        self.assertEqual(0, report["rejected_count"])
        candidate = inventory["candidates"][0]
        self.assertEqual("main-memory-curator", candidate["submitted_by_agent"])
        self.assertEqual(["claude-main", "pi-main"], report["origins"][0]["origin_agents"])
        self.assertEqual(1, report["origins"][0]["exact_duplicates_collapsed"])

    def test_conflicting_identity_is_blocked(self) -> None:
        first = self.write_export("one.json", export("pi-main", "export-pi-two", "Rule A"))
        second = self.write_export(
            "two.json", export("zcode-main", "export-zcode-two", "Rule B")
        )
        inventory, report = curate(
            [first, second], "main-memory-curator", "curation-conflict"
        )
        self.assertIsNone(inventory)
        self.assertEqual("NO_IMPORTABLE_CANDIDATES", report["status"])
        self.assertEqual(1, report["conflicting_identity_count"])
        self.assertEqual(2, report["rejected_count"])

    def test_distinct_candidates_share_one_curator_batch(self) -> None:
        first = self.write_export("one.json", export("pi-main", "export-pi-three", "Rule A"))
        second = self.write_export(
            "two.json",
            export("zcode-main", "export-zcode-three", "Rule B", "workflow.shared.other"),
        )
        payload = json.loads(second.read_text(encoding="utf-8"))
        payload["items"][0]["nodes"][0]["id"] = "node-shared-other"
        second.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        inventory, report = curate(
            [first, second], "main-memory-curator", "curation-distinct"
        )
        self.assertIsNotNone(inventory)
        self.assertEqual(2, report["accepted_count"])
        self.assertEqual(
            {"main-memory-curator"},
            {value["submitted_by_agent"] for value in inventory["candidates"]},
        )

    def test_conflict_with_active_produces_owner_diff_and_blocks_write(self) -> None:
        store = self.write_active_store("Current rule")
        incoming = self.write_export(
            "incoming.json", export("pi-main", "export-pi-active-conflict", "Changed rule")
        )
        inventory, report = curate(
            [incoming], "main-memory-curator", "curation-active-conflict", store
        )
        self.assertIsNone(inventory)
        self.assertEqual(1, report["active_conflict_count"])
        self.assertTrue(report["bodies_included"])
        conflict = report["active_conflicts"][0]
        self.assertEqual("Current rule", conflict["current_active"]["body"])
        self.assertEqual("Changed rule", conflict["incoming_pending"]["body"])
        self.assertTrue(conflict["write_blocked_until_owner_decision"])
        self.assertIn("MANUAL_ANALYSIS_REQUIRED", conflict["curator_recommendation"])
        self.assertTrue(conflict["conflict_id"].startswith("conflict-"))
        self.assertTrue(report["review_digest"].startswith("sha256:"))
        self.assertIn("确认记忆冲突", report["owner_decision_format"])

    def test_exact_active_duplicate_is_skipped(self) -> None:
        store = self.write_active_store("Same active rule")
        incoming = self.write_export(
            "incoming.json", export("pi-main", "export-pi-active-duplicate", "Same active rule")
        )
        inventory, report = curate(
            [incoming], "main-memory-curator", "curation-active-duplicate", store
        )
        self.assertIsNone(inventory)
        self.assertEqual(["workflow.shared.rule"], report["duplicate_active_keys"])
        self.assertEqual(0, report["active_conflict_count"])


if __name__ == "__main__":
    unittest.main()
