from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assemble_inventory.py"
SPEC = importlib.util.spec_from_file_location("assemble_inventory", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def user_item(key: str = "workflow.example") -> dict:
    return {
        "export_item_id": "item-example-preference",
        "scope": "global",
        "class": "preference",
        "key": key,
        "body": "Use a verified example workflow.",
        "source": {
            "kind": "user-instruction",
            "thread_id": "thread-example",
            "message_digest": "sha256:" + "1" * 64,
            "locator": "original user instruction",
        },
        "nodes": [
            {
                "id": "node-example-workflow",
                "title": "Example workflow",
                "keywords": ["example", "workflow"],
                "aliases": [],
                "related_node_ids": [],
            }
        ],
        "operation": "promote",
        "expected_current_id": None,
        "valid_from": "2026-08-23",
        "stale_when": "Owner changes the workflow.",
    }


def export_payload(items: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "type": "memorygraph-agent-export-v1",
        "export_id": "export-test-global",
        "agent_id": "test-source-agent",
        "tool": "test-agent",
        "created_at": "2026-08-23T00:00:00+00:00",
        "items": items,
        "rejected": [],
    }


class AssembleInventoryTests(unittest.TestCase):
    def test_global_preference_seals_inventory(self) -> None:
        inventory, report = module.assemble(export_payload([user_item()]))
        self.assertIsNotNone(inventory)
        assert inventory is not None
        self.assertEqual(report["status"], "SEALED")
        self.assertEqual(inventory["type"], "memorygraph-pending-import-v1")
        self.assertEqual(inventory["candidates"][0]["submitted_by_agent"], "test-source-agent")
        core = {key: value for key, value in inventory.items() if key != "batch_digest"}
        self.assertEqual(inventory["batch_digest"], module.sha256_bytes(module.canonical_json(core)))

    def test_secret_item_is_rejected_without_body_in_report(self) -> None:
        item = user_item()
        item["body"] = "api_key=supersecretvalue"
        inventory, report = module.assemble(export_payload([item]))
        self.assertIsNone(inventory)
        self.assertEqual(report["status"], "NO_IMPORTABLE_CANDIDATES")
        serialized = json.dumps(report)
        self.assertNotIn("supersecretvalue", serialized)
        self.assertIn("possible-secret", serialized)

    def test_duplicate_identity_keeps_first_and_rejects_second(self) -> None:
        first = user_item()
        second = user_item()
        second["export_item_id"] = "item-duplicate-preference"
        inventory, report = module.assemble(export_payload([first, second]))
        self.assertIsNotNone(inventory)
        assert inventory is not None
        self.assertEqual(len(inventory["candidates"]), 1)
        self.assertEqual(report["status"], "SEALED_WITH_REJECTIONS")
        self.assertEqual(report["rejected_count"], 1)

    def test_project_git_source_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            source = root / "decision.md"
            source.write_text("# Decision\n\nUse bounded queues.\n", encoding="utf-8")
            subprocess.run(["git", "add", "decision.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "decision"], cwd=root, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            blob = subprocess.check_output(["git", "show", f"{commit}:decision.md"], cwd=root)
            digest = "sha256:" + hashlib.sha256(blob).hexdigest()
            item = user_item("project.queue.policy")
            item.update(
                {
                    "scope": "project",
                    "class": "decision",
                    "source": {
                        "kind": "git",
                        "project_root": str(root),
                        "path": "decision.md",
                        "commit": commit,
                        "hash": digest,
                        "locator": "Decision section",
                        "require_current_head": True,
                    },
                }
            )
            inventory, report = module.assemble(export_payload([item]))
            self.assertIsNotNone(inventory)
            self.assertEqual(report["project_root"], str(root.resolve()))


if __name__ == "__main__":
    unittest.main()
