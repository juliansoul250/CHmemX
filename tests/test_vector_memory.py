from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from vector_memory import (  # noqa: E402
    EVALUATION_TYPE,
    LEGACY_INDEX_TYPE,
    VectorError,
    build_index,
    corpus_feature_weights,
    evaluate_index,
    load_evaluation_suite,
    optimize_scoring,
    ranked,
    route_upload,
    token_bucket,
)


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

    def test_index_uses_corpus_idf_and_never_copies_record_body(self) -> None:
        self.assertEqual("memorygraph-vector-pointer-v2", self.index["type"])
        self.assertTrue(self.index["vectorizer"]["corpus_adaptive"])
        self.assertTrue(self.index["vectorizer"]["feature_weights"])
        self.assertTrue(all(record["vector"] for record in self.index["records"]))
        self.assertNotIn("遇到429限额后记录断点并恢复工作", json.dumps(self.index, ensure_ascii=False))
        self.assertTrue(self.index["quality"]["generated_coverage"]["passed"])
        example = load_evaluation_suite(ROOT / "examples" / "recall-evaluation.example.json")
        self.assertEqual(EVALUATION_TYPE, example["type"])

    def test_empty_initialized_store_builds_a_valid_zero_case_index(self) -> None:
        empty = self.root / "empty-store"
        empty.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=empty, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"], cwd=empty, check=True
        )
        (empty / "catalog.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
        (empty / "global").mkdir()
        (empty / "global" / "active-index.json").write_text(
            json.dumps({"entries": {}}), encoding="utf-8"
        )
        taxonomy = self.root / "empty-taxonomy.json"
        taxonomy.write_text(
            json.dumps(
                {
                    "cells": [
                        {
                            "id": "grid-empty",
                            "title": "Empty",
                            "keywords": ["empty"],
                            "aliases": [],
                            "member_keys": [],
                            "related_cell_ids": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=empty, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "empty"], cwd=empty, check=True)
        index = build_index(empty, taxonomy, 512)
        self.assertEqual([], index["records"])
        self.assertEqual(0, index["quality"]["generated_coverage"]["case_count"])
        self.assertTrue(index["quality"]["generated_coverage"]["passed"])

    def test_idf_downweights_features_repeated_across_memory_documents(self) -> None:
        dimensions = 512
        values = dict(
            corpus_feature_weights(
                [
                    [("common unique", 1.0)],
                    [("common", 1.0)],
                    [("common", 1.0)],
                ],
                dimensions,
            )
        )
        self.assertGreater(
            values[float(token_bucket("unique", dimensions))],
            values[float(token_bucket("common", dimensions))],
        )

    def test_record_vector_disambiguates_memories_that_share_one_node(self) -> None:
        second = self.store / "global" / "active" / "memory-docs.json"
        self.write_record(
            second,
            record_id="memory-docs",
            key="workflow.quota.documentation",
            body="生成双语发布说明和文档验收清单",
            project_id=None,
            node_id="node-quota",
            title="限额中断恢复",
            keywords=["限额", "429", "断点续作"],
        )
        active_index = json.loads((self.store / "global" / "active-index.json").read_text())
        active_index["entries"]["preference:workflow.quota.documentation"] = "memory-docs"
        (self.store / "global" / "active-index.json").write_text(
            json.dumps(active_index, ensure_ascii=False), encoding="utf-8"
        )
        taxonomy = json.loads(self.taxonomy.read_text())
        taxonomy["cells"][0]["member_keys"].append("workflow.quota.documentation")
        self.taxonomy.write_text(json.dumps(taxonomy, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.store, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "second memory"], cwd=self.store, check=True)
        index = build_index(self.store, self.taxonomy, 512)
        result = ranked(index, "双语发布说明和文档验收清单", 3, self.root)
        self.assertEqual("memory-docs", result["entries"][0]["id"])

    def test_golden_evaluation_and_profile_optimization_are_deterministic(self) -> None:
        suite = {
            "schema_version": 1,
            "type": EVALUATION_TYPE,
            "suite_id": "synthetic-quality",
            "thresholds": {
                "min_top1": 1.0,
                "min_hit_at_3": 1.0,
                "min_mrr": 1.0,
                "min_precision_at_k": 0.25,
                "max_forbidden_hits": 0,
                "max_scope_violations": 0,
            },
            "cases": [
                {
                    "id": "quota",
                    "query": "429额度到了以后怎么断点恢复",
                    "expected_keys": ["workflow.quota.recovery"],
                    "relevant_keys": ["workflow.quota.recovery"],
                    "forbidden_keys": ["market.risk_gate"],
                    "top_k": 4,
                    "required_rank": 1,
                },
                {
                    "id": "market",
                    "query": "市场风险门禁",
                    "project_id": "project-market",
                    "expected_keys": ["market.risk_gate"],
                    "relevant_keys": ["market.risk_gate"],
                    "top_k": 4,
                    "required_rank": 1,
                },
            ],
        }
        report = evaluate_index(self.index, suite)
        self.assertTrue(report["passed"])
        selected, selected_report, trials = optimize_scoring(self.index, suite)
        self.assertTrue(selected_report["passed"])
        self.assertEqual(3, len(trials))
        self.assertEqual(selected["quality"]["selected_profile"], selected["scoring_profile"]["name"])

    def test_quality_failure_is_reported_and_legacy_index_remains_readable(self) -> None:
        impossible = {
            "schema_version": 1,
            "type": EVALUATION_TYPE,
            "suite_id": "impossible",
            "thresholds": {"min_hit_at_3": 1.0},
            "cases": [
                {
                    "id": "missing",
                    "query": "不存在的记忆",
                    "expected_keys": ["missing.key"],
                    "top_k": 3,
                    "required_rank": 3,
                }
            ],
        }
        self.assertFalse(evaluate_index(self.index, impossible)["passed"])

        legacy = json.loads(json.dumps(self.index))
        legacy["type"] = LEGACY_INDEX_TYPE
        legacy["schema_version"] = 1
        legacy.pop("scoring_profile", None)
        legacy["vectorizer"].pop("feature_weights", None)
        for record in legacy["records"]:
            record.pop("vector", None)
        result = ranked(legacy, "429限额断点恢复", 4, self.root)
        self.assertEqual("memory-global", result["entries"][0]["id"])

        suite_path = self.root / "impossible-suite.json"
        suite_path.write_text(json.dumps(impossible, ensure_ascii=False), encoding="utf-8")
        output = self.root / "must-not-exist.json"
        failure_report = self.root / "quality-failure.json"
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "vector_memory.py"),
                "optimize",
                "--store",
                str(self.store),
                "--taxonomy",
                str(self.taxonomy),
                "--suite",
                str(suite_path),
                "--output",
                str(output),
                "--report",
                str(failure_report),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, process.returncode)
        self.assertFalse(output.exists())
        self.assertTrue(failure_report.is_file())
        self.assertFalse(json.loads(failure_report.read_text())["index_published"])
        self.assertIn("QUALITY_GATE_FAILED", process.stdout)


if __name__ == "__main__":
    unittest.main()
