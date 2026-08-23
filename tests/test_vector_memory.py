from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from vector_memory import VectorError, build_index, ranked, route_upload  # noqa: E402


class VectorMemoryAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = self.root / "store"
        self.store.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.store, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.store, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.store, check=True)
        project_root = self.root / "market"
        project_root.mkdir()
        catalog = {
            "projects": {
                "project-market": {
                    "project_id": "project-market",
                    "root": str(project_root),
                    "title": "Market",
                }
            }
        }
        (self.store / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        self.write_record(
            self.store / "global" / "active" / "memory-global.json",
            record_id="memory-global",
            key="workflow.quota.recovery",
            body="遇到429限额后记录断点并恢复工作",
            project_id=None,
            node_id="node-quota",
            title="限额中断恢复",
            keywords=["限额", "429", "断点续作"],
        )
        self.write_record(
            self.store / "projects" / "project-market" / "active" / "memory-market.json",
            record_id="memory-market",
            key="market.risk_gate",
            body="市场风险升高时执行项目门禁",
            project_id="project-market",
            node_id="node-market-gate",
            title="市场风险门禁",
            keywords=["市场", "风险", "门禁"],
        )
        (self.store / "global" / "active-index.json").write_text(
            json.dumps(
                {"entries": {"preference:workflow.quota.recovery": "memory-global"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.store / "projects" / "project-market" / "active-index.json").write_text(
            json.dumps(
                {"entries": {"decision:market.risk_gate": "memory-market"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.taxonomy = self.root / "taxonomy.json"
        self.taxonomy.write_text(
            json.dumps(
                {
                    "cells": [
                        {
                            "id": "grid-workflow",
                            "title": "工作流",
                            "keywords": ["限额", "恢复", "工作流"],
                            "aliases": [],
                            "member_keys": ["workflow.quota.recovery"],
                            "related_cell_ids": [],
                        },
                        {
                            "id": "grid-market",
                            "title": "市场项目",
                            "keywords": ["市场", "风险", "门禁"],
                            "aliases": [],
                            "project_id": "project-market",
                            "member_key_prefixes": ["market."],
                            "member_keys": [],
                            "related_cell_ids": [],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.store, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=self.store, check=True)
        self.index = build_index(self.store, self.taxonomy, 512)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_record(
        self, path: Path, *, record_id: str, key: str, body: str,
        project_id: str | None, node_id: str, title: str, keywords: list[str]
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": record_id,
            "authority": "accepted",
            "status": "active",
            "scope": "project" if project_id else "global",
            "project_id": project_id,
            "class": "decision" if project_id else "preference",
            "key": key,
            "body": body,
            "source": {
                "kind": "git" if project_id else "user-instruction",
                "locator": "fixture",
            },
            "nodes": [
                {
                    "id": node_id,
                    "title": title,
                    "keywords": keywords,
                    "aliases": [],
                    "related_node_ids": [],
                }
            ],
            "valid_from": "2026-08-23",
            "stale_when": "authority changes",
            "submitted_by_agent": "source-agent",
            "committed_by_agent": "curator-agent",
        }
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def test_query_hits_global_memory_by_vector_pointer(self) -> None:
        result = ranked(self.index, "429额度到了以后怎么断点恢复", 4, self.root)
        self.assertEqual("memory-global", result["entries"][0]["id"])
        self.assertFalse(result["candidate_entries_included"])

    def test_query_can_cross_tool_and_project_boundary_with_scope_marker(self) -> None:
        result = ranked(self.index, "市场风险门禁", 4, self.root)
        entry = next(value for value in result["entries"] if value["id"] == "memory-market")
        self.assertTrue(entry["vector_pointer"]["cross_project_reference"])
        self.assertEqual("project-market", entry["project_id"])

    def test_index_staleness_blocks_recall(self) -> None:
        (self.store / "later.txt").write_text("later", encoding="utf-8")
        subprocess.run(["git", "add", "later.txt"], cwd=self.store, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "later"], cwd=self.store, check=True)
        with self.assertRaisesRegex(VectorError, "INDEX_STALE"):
            ranked(self.index, "限额恢复", 4, self.root)

    def test_route_upload_detects_active_identity(self) -> None:
        upload = self.root / "upload.json"
        upload.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "type": "memorygraph-agent-export-v1",
                    "export_id": "export-test-upload",
                    "agent_id": "pi-main",
                    "tool": "pi",
                    "created_at": "2026-08-23T00:00:00+00:00",
                    "items": [
                        {
                            "export_item_id": "item-test-upload",
                            "scope": "global",
                            "class": "preference",
                            "key": "workflow.quota.recovery",
                            "body": "新版本限额规则",
                            "source": {
                                "kind": "user-instruction",
                                "thread_id": "thread",
                                "message_digest": "sha256:" + "1" * 64,
                                "locator": "owner message",
                            },
                            "nodes": [
                                {
                                    "id": "node-upload-quota",
                                    "title": "限额恢复",
                                    "keywords": ["限额", "恢复", "429"],
                                    "aliases": [],
                                    "related_node_ids": [],
                                }
                            ],
                            "operation": "promote",
                            "expected_current_id": None,
                            "valid_from": "2026-08-23",
                            "stale_when": "Owner changes it",
                        }
                    ],
                    "rejected": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = route_upload(self.index, upload)
        self.assertEqual("CONFLICT_REVIEW_REQUIRED", result["routes"][0]["recommendation"])
        self.assertFalse(result["routes"][0]["automatic_write_allowed"])


if __name__ == "__main__":
    unittest.main()
