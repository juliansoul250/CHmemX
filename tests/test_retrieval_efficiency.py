"""Public recall boundaries, using only disposable synthetic accepted records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chmemx.mcp_server import dispatch
from chmemx.runtime import simple_memory as core
from chmemx.scripts import retrieval_v3 as v3
from chmemx.scripts import vector_memory as lexical
from chmemx.service import Service


def fixture(root: Path, count: int = 4):
    project = root / "fictional-project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    service = Service(root / "memory", project, "fixture-source")
    service.initialize("project-fixture", "team")
    candidates = []
    for number in range(count):
        key = f"preference.topic{number:03d}.colour"
        body = f"Fixture {number:03d} uses cobalt. " + "Synthetic reference text. " * 180
        body += "末尾条件：仅供测试，不能用于正式环境。"
        result = service.runtime.propose(
            {
                "scope": "global",
                "class": "preference",
                "key": key,
                "body": body,
                "source": service.source({"quote": body, "thread_id": "fixture-owner"}, "global"),
                "submitted_by_agent": "fixture-source",
                "nodes": [{"id": f"node-{number:03d}", "title": f"Topic {number:03d}",
                           "keywords": [f"topic{number:03d}"], "aliases": [],
                           "related_node_ids": ["node-001"] if number == 0 and count > 1 else []}],
            },
            project,
        )
        candidates.append(result["candidate_id"])
    batch = service.runtime.create_batch(candidates)
    review = service.runtime.review(batch["batch_id"])
    service.runtime.approve(batch["batch_id"], batch["batch_digest"],
                            review["required_confirmation"], committing_agent="fixture-source")
    service.rebuild()
    return service


def lookup_plan():
    return {"version": 1, "query": "preference.topic000.colour", "decision": "search",
            "goal": "Look up the named colour preference.", "entities": ["preference.topic000.colour"],
            "constraints": [], "facets": []}


class RetrievalEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chmemx-retrieval-efficiency-")
        self.service = fixture(Path(self.temp.name).resolve())
        self.index = core.load_json(self.service.index_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_cell_contribution_does_not_rescan_members_for_each_record(self):
        class NoMembershipProbe(list):
            def __contains__(self, value):
                raise AssertionError("Repeated linear scan of cell members")

        # Overlap matters: the component is the maximum of all member cells,
        # not the first, last, or only the three cells used for navigation.
        target, other = self.index["records"][:2]
        query = target["key"]
        vector = lexical.sparse_vector([(query, 1.0)], self.index["vectorizer"]["dimensions"],
                                       lexical.feature_weight_map(self.index))
        def scaled(factor):
            return [[i, factor * weight] for i, weight in vector]

        cells = self.index["cells"]
        template = cells[0]
        cells[:] = [{**template, "id": f"test-{n}", "vector": scaled(factor),
                     "member_record_ids": NoMembershipProbe([record["id"]])}
                    for n, (record, factor) in enumerate([(other, 1.0)] * 3 +
                                                        [(target, 0.2), (target, 0.8), (target, 0.4)])]
        result = lexical.score_pointers(self.index, query, 4, self.service.cwd)
        pointer = next(row["vector_pointer"] for row in result["entries"] if row["id"] == target["id"])
        self.assertEqual([], pointer["matched_cells"])
        self.assertEqual(round(lexical.cosine(vector, scaled(0.8)), 8), pointer["score_components"]["cell"])

    def test_exact_recall_does_not_open_unselected_record_bodies(self):
        reader = v3.Retriever(self.index)
        forbidden = {
            self.service.store / row["record_path"]
            for row in self.index["records"] if row["key"] != "preference.topic000.colour"
        }
        real_open = Path.open

        def scoped_open(path, *args, **kwargs):
            if isinstance(path, (str, Path)) and Path(path) in forbidden:
                raise AssertionError("Unselected record body was opened")
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", scoped_open):
            result = reader.recall("preference.topic000.colour", self.service.cwd,
                                   limit=1, association_limit=0)
        self.assertEqual(["preference.topic000.colour"], [r["key"] for r in result["entries"]])
        self.assertIn("末尾条件", result["entries"][0]["body"])

    def test_preview_is_explicit_and_full_recall_preserves_tail(self):
        full = self.service.recall("preference.topic000.colour", 1)
        preview = self.service.recall("preference.topic000.colour", 1, body_limit=256)
        original, short = full["entries"][0], preview["entries"][0]
        self.assertEqual(original["body"][:256], short["body"])
        self.assertTrue(short["body_preview"]["truncated"])
        self.assertEqual(len(original["body"]), short["body_preview"]["total_chars"])
        self.assertEqual("sha256:" + hashlib.sha256(original["body"].encode()).hexdigest(),
                         short["body_preview"]["body_sha256"])
        self.assertIn("not complete", preview["body_delivery"]["notice"])
        # Freshness is checked on every call; checked_at must not be cached with the body.
        self.assertEqual(
            {k: v for k, v in original["source_freshness"].items() if k != "checked_at"},
            {k: v for k, v in short["source_freshness"].items() if k != "checked_at"},
        )
        self.assertEqual(original["vector_pointer"], short["vector_pointer"])
        again = self.service.recall("preference.topic000.colour", 1)
        self.assertEqual(original["body"], again["entries"][0]["body"])
        self.assertNotIn("body_preview", again["entries"][0])

    def test_invalid_preview_budget_is_rejected_at_mcp_boundary(self):
        for budget in (True, 0, 255, 16385, "1024"):
            with self.subTest(budget=budget):
                response, _ = dispatch(self.service, {
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "recall", "arguments": {
                        "query": "preference.topic000.colour", "body_limit": budget,
                    }},
                }, True)
                result = response["result"]
                self.assertTrue(result["isError"])
                self.assertIn("BODY_LIMIT_INVALID", result["content"][0]["text"])

    def test_mcp_preview_returns_budget_and_provenance(self):
        response, _ = dispatch(self.service, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "recall", "arguments": {
                "query": "preference.topic000.colour", "query_plan": lookup_plan(),
                "limit": 1, "body_limit": 512,
            }},
        }, True)
        self.assertFalse(response["result"]["isError"])
        result = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(512, len(result["entries"][0]["body"]))
        self.assertEqual("untrusted_historical_memory", result["content_type"])
        self.assertTrue(result["entries"][0]["source_badge"]["accepted_record"])

    def test_preview_still_rejects_uncommitted_record_changes(self):
        self.service.recall("preference.topic000.colour", 1, body_limit=256)
        meta = next(r for r in self.index["records"] if r["key"] == "preference.topic000.colour")
        path = self.service.store / meta["record_path"]
        record = core.load_json(path)
        record["body"] = "Unapproved replacement."
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "STORE_DIRTY"):
            self.service.recall("preference.topic000.colour", 1, body_limit=256)

    def test_complete_preview_is_not_falsely_marked_truncated(self):
        result = self.service.recall("preference.topic000.colour", 1, body_limit=16384)
        record = result["entries"][0]
        self.assertFalse(record["body_preview"]["truncated"])
        self.assertTrue(record["body"].endswith("不能用于正式环境。"))

    def test_sdk_exposes_preview_without_adding_execution_tools(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def run():
            params = StdioServerParameters(command=sys.executable, args=[
                "-I", "-B", "-m", "chmemx.cli", "--store", str(self.service.store),
                "--cwd", str(self.service.cwd), "--agent-id", "fixture-reader", "serve",
            ])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertEqual({"start", "recall", "upload"}, {t.name for t in listing.tools})
                    schema = next(t.inputSchema for t in listing.tools if t.name == "recall")
                    self.assertEqual(256, schema["properties"]["body_limit"]["minimum"])
                    self.assertEqual(0, schema["properties"]["association_limit"]["minimum"])
                    result = await session.call_tool("recall", {
                        "query": "preference.topic000.colour", "query_plan": lookup_plan(),
                        "limit": 1, "body_limit": 256,
                        "association_limit": 0,
                    })
                    self.assertFalse(result.isError)
                    data = json.loads(result.content[0].text)
                    self.assertEqual(256, len(data["entries"][0]["body"]))
                    self.assertTrue(data["entries"][0]["body_preview"]["truncated"])
                    self.assertEqual([], data["associations"])

        asyncio.run(run())

    def test_cli_preview_preserves_full_read_option(self):
        plan_path = Path(self.temp.name) / "query-plan.json"
        plan_path.write_text(json.dumps(lookup_plan()))
        output = subprocess.run([
            sys.executable, "-I", "-B", "-m", "chmemx.cli", "--store", str(self.service.store),
            "--cwd", str(self.service.cwd), "recall", "preference.topic000.colour",
            "--limit", "1", "--body-limit", "256", "--association-limit", "0",
            "--query-plan", str(plan_path),
        ], check=True, capture_output=True, text=True)
        result = json.loads(output.stdout)
        self.assertEqual(256, len(result["entries"][0]["body"]))
        self.assertIn("Omit body_limit", result["body_delivery"]["notice"])
        self.assertEqual([], result["associations"])

    def test_legacy_full_reader_keeps_home_relative_store_paths(self):
        index = {**self.index, "type": v3.lexical.INDEX_TYPE, "memory_graph_store": "~/memory"}
        with patch.dict(os.environ, {"HOME": str(self.service.store.parent)}):
            result = v3.lexical.ranked(index, "preference.topic000.colour", 1, self.service.cwd)
        self.assertEqual("preference.topic000.colour", result["entries"][0]["key"])

    def test_association_budget_is_explicit_without_changing_primary_ranking(self):
        default = self.service.recall("preference.topic000.colour", 1)
        self.assertEqual(["preference.topic001.colour"],
                         [r["key"] for r in default["associations"]])
        direct = self.service.recall("preference.topic000.colour", 1, association_limit=0)
        self.assertEqual([], direct["associations"])
        self.assertEqual(default["entries"][0]["vector_pointer"],
                         direct["entries"][0]["vector_pointer"])

    def test_graph_evaluation_has_real_positive_and_unrelated_exclusions(self):
        suite = {"schema_version": 1, "type": lexical.EVALUATION_TYPE, "suite_id": "fixture-graph",
                 "thresholds": {"min_association_hit_rate": 1},
                 "cases": [{"id": "directed-relation", "query": "preference.topic000.colour",
                            "expected_keys": ["preference.topic000.colour"],
                            "expected_association_keys": ["preference.topic001.colour"],
                            "forbidden_association_keys": ["preference.topic002.colour",
                                                           "preference.topic003.colour"]}]}
        result = lexical.evaluate_index(
            {**self.index, "type": lexical.INDEX_TYPE}, suite,
            ranker=lambda _, query, limit, cwd: self.service.recall(query, 1))
        self.assertTrue(result["passed"])
        self.assertEqual(result["metrics"]["association_hit_rate"], 1)
        self.assertEqual(result["diagnostics"]["positive_association_coverage"], "PRESENT")
        self.assertEqual(result["cases"][0]["returned_association_keys"],
                         ["preference.topic001.colour"])

    def test_invalid_association_budget_keeps_structured_error(self):
        for value in (True, -1, 9, "0"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ASSOCIATION_LIMIT_INVALID"):
                self.service.recall("preference.topic000.colour", association_limit=value)


if __name__ == "__main__":
    unittest.main()
