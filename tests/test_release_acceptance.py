"""Release checks use synthetic stores, committed project sources and real stdio."""

import asyncio
import hashlib
import json
import sys
import unittest

import test_identity_boundaries as identity
import test_service as fixtures
from chmemx.runtime import simple_memory as core


class ReleaseAcceptance(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown
    seed_legacy_body = identity.IdentityBoundaries.seed_legacy_body

    def commit_source(self, path, body):
        path.write_text(body, encoding="utf-8")
        core.run_git(self.project, ["add", path.name])
        core.run_git(self.project, ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                    "commit", "-qm", "Synthetic project authority"])
        return self.service.source({"path": path.name}, "project")

    def test_project_long_decision_and_evidence_complete_owner_flow(self):
        for memory_class in ("decision", "evidence"):
            with self.subTest(memory_class=memory_class):
                key = f"contract.fixture.{memory_class}"
                body = "界" * 9000
                path = self.project / f"{memory_class}.md"
                source = self.commit_source(path, body)
                candidate = self.service.runtime.propose({
                    "scope": "project", "class": memory_class, "key": key,
                    "body": body, "source": source, "operation": "promote",
                    "source_agent": "source-alpha", "submitted_by_agent": "source-alpha",
                    "nodes": [{"id": f"node-{memory_class}", "title": "Fixture", "keywords": ["fixture"],
                               "aliases": [], "related_node_ids": []}],
                }, self.project)
                batch = self.service.runtime.create_batch([candidate["candidate_id"]])
                review = self.service.runtime.review(batch["batch_id"])
                original = self.service.runtime.approve(
                    batch["batch_id"], batch["batch_digest"], review["required_confirmation"],
                    committing_agent="source-alpha",
                )
                receipt = self.service.store / "approvals" / f"{batch['batch_id']}.json"
                receipt_bytes = receipt.read_bytes()
                short = "The project uses protocol beta."
                new_source = self.commit_source(path, short)
                upload = self.service.upload(key, short, {"path": path.name}, scope="project",
                                             memory_class=memory_class)
                self.assertEqual("CONFLICT", upload["status"])
                self.assertEqual(body, upload["current"])
                self.assertEqual(source, upload["current_source"])
                self.assertEqual(new_source, upload["incoming_source"])
                self.assertEqual(original["commit"], core.git_head(self.service.store))
                review = self.service.review(upload["upload_id"])
                with self.assertRaises(core.MemoryError) as denied:
                    self.service.approve(review["batch_id"], review["batch_digest"], "not approved")
                self.assertEqual("CONFIRMATION_REQUIRED", denied.exception.code)
                approved = self.service.approve(review["batch_id"], review["batch_digest"],
                                                review["required_confirmation"])
                self.assertEqual("ACTIVE_COMMITTED", approved["status"])
                recalled = self.service.recall(key, 1)["entries"][0]
                self.assertEqual(short, recalled["body"])
                self.assertEqual(new_source, recalled["source"])
                self.assertEqual(original["record_ids"][0], recalled["supersedes"])
                self.assertEqual(receipt_bytes, receipt.read_bytes())

    def test_large_conflict_round_trips_sdk_and_reconnects(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        body = "A" * core.MAX_BODY_BYTES
        key = "preference.fixture.large"
        original = self.seed_legacy_body(key, body)
        payload = {"key": key, "value": "Prefer concise explanations.",
                   "source": {"quote": "Prefer concise explanations.", "thread_id": "fixture-owner"},
                   "request_id": "fixture-large-reconnect"}
        params = StdioServerParameters(command=sys.executable, args=[
            "-I", "-B", "-m", "chmemx.cli", "--store", str(self.service.store),
            "--cwd", str(self.project), "--agent-id", "source-alpha", "serve",
        ])

        async def one_connection():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.call_tool("upload", payload)
                    self.assertFalse(response.isError)
                    raw = response.content[0].text
                    result = json.loads(raw)
                    self.assertGreater(len(raw.encode("utf-8")), 1024 * 1024)
                    self.assertEqual("CONFLICT", result["status"])
                    self.assertEqual(body, result["current"])
                    self.assertEqual(payload["value"], result["incoming"])
                    # Object member order may change when a job is read from disk.
                    # Reconnect guarantees the same job and complete review data,
                    # not identical JSON serialization bytes.
                    return result["upload_id"], hashlib.sha256(
                        core.canonical_json(result)
                    ).hexdigest()

        first, second = asyncio.run(one_connection()), asyncio.run(one_connection())
        self.assertEqual(first, second)
        self.assertEqual(original["commit"], core.git_head(self.service.store))
        review = self.service.review(first[0])
        self.assertEqual("pending", review["status"])
        self.assertEqual(payload["value"], review["candidates"][0]["body"])


if __name__ == "__main__":
    unittest.main()
