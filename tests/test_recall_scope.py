"""Scope selection uses synthetic stores and public upload/recall boundaries."""

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from chmemx.runtime import simple_memory as core
from chmemx.service import Service


class RecallScopeAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chmemx-recall-scope-")
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "atlas"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.service = Service(self.root / "memory", self.project, "fixture-source")
        self.service.initialize("project-atlas", "team")
        self.outside = self.root / "unregistered"
        self.outside.mkdir()
        self.reader = Service(self.service.store, self.outside, "fixture-reader")
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def remember(self, key, body, scope, keywords):
        self.sequence += 1
        if scope == "project":
            path = self.project / f"source-{self.sequence}.md"
            path.write_text(body, encoding="utf-8")
            core.run_git(self.project, ["add", path.name])
            core.run_git(
                self.project,
                [
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-qm",
                    "Synthetic source",
                ],
            )
            source = self.service.source({"path": path.name}, scope)
        else:
            source = self.service.source({"quote": body, "thread_id": "fixture-owner"}, scope)
        candidate = self.service.runtime.propose(
            {
                "scope": scope,
                "class": "preference",
                "key": key,
                "body": body,
                "source": source,
                "submitted_by_agent": "fixture-source",
                "nodes": [
                    {
                        "id": f"node-fixture-{self.sequence}",
                        "title": keywords[0],
                        "keywords": keywords,
                        "aliases": [],
                        "related_node_ids": [],
                    }
                ],
            },
            self.project,
        )
        batch = self.service.runtime.create_batch([candidate["candidate_id"]])
        review = self.service.runtime.review(batch["batch_id"])
        return self.service.runtime.approve(
            batch["batch_id"],
            batch["batch_digest"],
            review["required_confirmation"],
            committing_agent="fixture-source",
        )

    def seed_review_rules(self):
        self.remember(
            "preference.review.workflow",
            "Evaluate external code review against evidence. Choose necessary fixes and keep "
            "code simple. Preserve behavior, approval requirements and regression tests.",
            "global",
            ["review evidence", "necessary fixes", "code simplicity"],
        )
        self.remember(
            "atlas.preference.modules",
            "Atlas prefers simple code.",
            "project",
            ["simple code", "code review"],
        )

    def test_unknown_context_keeps_global_guidance_free_of_unrequested_projects(self):
        self.seed_review_rules()
        result = self.reader.recall("external code review necessary fixes simple code", 5)
        self.assertEqual(["preference.review.workflow"], [r["key"] for r in result["entries"]])
        self.assertTrue(all(r.get("project_id") is None for r in result["associations"]))
        self.assertTrue(result["source_checks"])

    def test_explicit_project_name_keeps_requested_project_matches(self):
        self.seed_review_rules()
        result = self.reader.recall("atlas external code review simple code", 5)
        record = next(r for r in result["entries"] if r["key"] == "atlas.preference.modules")
        self.assertEqual("project-atlas", record["project_id"])
        self.assertTrue(record["vector_pointer"]["cross_project_reference"])

    def test_exact_key_selects_project_without_its_name(self):
        key = "preference.components.compactness"
        self.remember(key, "Compact components are preferred.", "project", ["compact components"])
        result = self.reader.recall(key, 1)
        self.assertEqual(key, result["entries"][0]["key"])
        self.assertEqual("project-atlas", result["entries"][0]["project_id"])

    def test_project_fallback_remains_when_no_scoped_record_matches(self):
        self.seed_review_rules()
        self.remember(
            "preference.recovery.system",
            "Nickel vapor recovery uses a condenser.",
            "project",
            ["nickel vapor recovery"],
        )
        result = self.reader.recall("nickel vapor recovery condenser", 3)
        self.assertEqual(["preference.recovery.system"], [r["key"] for r in result["entries"]])
        self.assertEqual("project-atlas", result["entries"][0]["project_id"])
        self.assertTrue(result["entries"][0]["vector_pointer"]["cross_project_reference"])

    def test_named_empty_project_never_falls_back_to_unrequested_project(self):
        self.remember(
            "preference.recovery.system",
            "Nickel vapor recovery uses a condenser.",
            "project",
            ["nickel vapor recovery"],
        )
        other = self.root / "borealis"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.service.runtime.register_project(other, "project-borealis", "Borealis", confirmed=True)
        result = self.reader.recall("borealis nickel vapor recovery condenser", 5)
        self.assertEqual([], result["entries"])
        self.assertEqual([], result["associations"])

    def test_explicit_project_stale_fact_still_requires_review(self):
        self.remember(
            "preference.recovery.system",
            "Nickel vapor recovery uses a condenser.",
            "project",
            ["nickel vapor recovery"],
        )
        (self.project / "source-1.md").write_text(
            "The recovery specification changed.", encoding="utf-8"
        )
        result = self.reader.recall("atlas nickel vapor recovery condenser", 5)
        self.assertEqual([], result["entries"])
        self.assertEqual("preference.recovery.system", result["needs_review"][0]["key"])
        self.assertFalse(result["needs_review"][0]["source_freshness"]["current_use_allowed"])

    def test_stdio_recall_preserves_global_scope_selection(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self.seed_review_rules()

        async def run():
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-I",
                    "-B",
                    "-m",
                    "chmemx.cli",
                    "--store",
                    str(self.service.store),
                    "--cwd",
                    str(self.outside),
                    "--agent-id",
                    "fixture-reader",
                    "serve",
                ],
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.call_tool(
                        "recall",
                        {
                            "query": "external code review necessary fixes simple code",
                            "limit": 5,
                        },
                    )
                    self.assertFalse(response.isError)
                    result = json.loads(response.content[0].text)
                    self.assertEqual(
                        ["preference.review.workflow"], [r["key"] for r in result["entries"]]
                    )
                    self.assertTrue(result["source_checks"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
