from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from chmemx.knowledge_graph import ExperimentalGraphRetriever, TypedGraph
from chmemx.scripts import retrieval_v3 as v3


def graph_fixture():
    records = [
        {"id": "project", "project_id": "alpha", "record_sha256": "1" * 64},
        {"id": "tool", "project_id": "alpha", "record_sha256": "2" * 64},
        {"id": "use", "project_id": "alpha", "record_sha256": "3" * 64},
    ]
    graph = {
        "schema_version": 1,
        "entities": [
            {
                "id": "alpha:project",
                "type": "Project",
                "name": "Project Alpha",
                "project_id": "alpha",
                "record_ids": ["project"],
                "aliases": ["Alpha"],
            },
            {
                "id": "alpha:tool",
                "type": "Tool",
                "name": "Workshop Tool",
                "project_id": "alpha",
                "record_ids": ["tool"],
            },
        ],
        "relations": [
            {
                "id": "use-edge",
                "type": "uses",
                "from": "alpha:project",
                "to": "alpha:tool",
                "evidence_record_id": "use",
                "evidence_sha256": "3" * 64,
                "valid_from": "2026-01-01",
            },
        ],
    }
    return graph, records


class TypedGraphTests(unittest.TestCase):
    def test_same_alias_in_another_project_is_not_merged(self):
        graph, records = graph_fixture()
        records.append({"id": "other-project", "project_id": "beta", "record_sha256": "4" * 64})
        graph["entities"].append(
            {
                "id": "beta:project",
                "type": "Project",
                "name": "Project Alpha",
                "aliases": ["Alpha"],
                "project_id": "beta",
                "record_ids": ["other-project"],
            }
        )
        reader = TypedGraph(graph, records)
        self.assertEqual("alpha:project", reader.paths("Alpha", "alpha", "uses")["anchor_id"])
        self.assertEqual("NO_ENTITY", reader.paths("beta:project", "alpha", "uses")["status"])
        graph["relations"][0]["to"] = "beta:project"
        with self.assertRaisesRegex(ValueError, "GRAPH_SCOPE_MISMATCH"):
            TypedGraph(graph, records)

    def test_dependency_paths_are_bounded_and_not_new_transitive_facts(self):
        graph, records = graph_fixture()
        for identifier in ("backend", "helper"):
            records.append({"id": identifier, "project_id": "alpha", "record_sha256": "4" * 64})
            graph["entities"].append(
                {
                    "id": f"alpha:{identifier}",
                    "type": "Tool",
                    "name": identifier,
                    "project_id": "alpha",
                    "record_ids": [identifier],
                }
            )
        for n, (source, target) in enumerate(
            (("tool", "backend"), ("backend", "helper"), ("helper", "tool"))
        ):
            graph["relations"].append(
                {
                    "id": f"dep-{n}",
                    "type": "depends_on",
                    "from": f"alpha:{source}",
                    "to": f"alpha:{target}",
                    "evidence_record_id": "use",
                    "evidence_sha256": "3" * 64,
                    "valid_from": "2026-01-01",
                }
            )
        reader = TypedGraph(graph, records)
        result = reader.paths("Workshop Tool", "alpha", "depends_on", max_hops=2)
        self.assertEqual(
            [["alpha:tool", "alpha:backend"], ["alpha:tool", "alpha:backend", "alpha:helper"]],
            [p["entity_ids"] for p in result["paths"]],
        )
        self.assertEqual(["dep-0", "dep-1"], [e["id"] for e in result["paths"][1]["edges"]])
        self.assertFalse(result["execution_authorized"])
        self.assertTrue(
            reader.paths("Workshop Tool", "alpha", "depends_on", max_hops=2, max_edges=1)[
                "truncated"
            ]
        )

    def test_bad_json_field_types_are_structured_validation_errors(self):
        for field in ("type", "from", "to"):
            graph, records = graph_fixture()
            graph["relations"][0][field] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                TypedGraph(graph, records)
        graph, records = graph_fixture()
        with self.assertRaises(ValueError):
            TypedGraph(graph, records).paths("Alpha", [], "uses")

    def test_directed_relationship_is_not_automatically_reversed(self):
        graph, records = graph_fixture()
        reader = TypedGraph(graph, records)
        forward = reader.paths("Alpha", "alpha", "uses", at="2026-01-02")
        reverse = reader.paths("Workshop Tool", "alpha", "uses", at="2026-01-02")
        incoming = reader.paths(
            "Workshop Tool", "alpha", "uses", direction="incoming", at="2026-01-02"
        )
        self.assertEqual(["alpha:tool"], [p["entity_ids"][-1] for p in forward["paths"]])
        self.assertEqual([], reverse["paths"])
        self.assertEqual(["alpha:project"], [p["entity_ids"][-1] for p in incoming["paths"]])
        self.assertEqual("alpha:project", incoming["paths"][0]["edges"][0]["from"])

    def test_foreign_name_does_not_become_a_fallback_hit(self):
        graph, records = graph_fixture()
        result = TypedGraph(graph, records).paths("Alpha", "beta", "uses")
        self.assertEqual("NO_ENTITY", result["status"])
        self.assertEqual([], result["paths"])

    def test_expired_or_future_relation_is_not_returned(self):
        graph, records = graph_fixture()
        graph["relations"][0]["valid_until"] = "2026-02-01"
        reader = TypedGraph(graph, records)
        for when in ("2025-12-31", "2026-02-01"):
            with self.subTest(when=when):
                self.assertEqual([], reader.paths("Alpha", "alpha", "uses", at=when)["paths"])

    def test_ambiguous_alias_requires_an_entity_id(self):
        graph, records = graph_fixture()
        extra = copy.deepcopy(graph["entities"][0])
        extra["id"] = "alpha:other-project"
        graph["entities"].append(extra)
        reader = TypedGraph(graph, records)
        self.assertEqual("ENTITY_AMBIGUOUS", reader.paths("Alpha", "alpha", "uses")["status"])
        self.assertEqual("OK", reader.paths("alpha:project", "alpha", "uses")["status"])

    def test_graph_query_budget_and_relation_are_checked(self):
        graph, records = graph_fixture()
        reader = TypedGraph(graph, records)
        for kwargs in (
            {"max_hops": 3},
            {"max_hops": True},
            {"limit": 0},
            {"direction": "both"},
            {"max_edges": 513},
        ):
            with (
                self.subTest(kwargs=kwargs),
                self.assertRaisesRegex(ValueError, "GRAPH_QUERY_INVALID"),
            ):
                reader.paths("Alpha", "alpha", "uses", **kwargs)
        with self.assertRaisesRegex(ValueError, "GRAPH_RELATION_INVALID"):
            reader.paths("Alpha", "alpha", "approved_to_execute")

    def test_wrong_endpoint_type_is_rejected(self):
        graph, records = graph_fixture()
        graph["entities"][1]["type"] = "Role"
        with self.assertRaisesRegex(ValueError, "GRAPH_ENDPOINT_TYPE_INVALID"):
            TypedGraph(graph, records)

    def test_invalid_evidence_and_scope_cannot_enter_graph(self):
        cases = [
            ("unknown-evidence", "GRAPH_RECORD_UNKNOWN"),
            ("wrong-sha", "GRAPH_EVIDENCE_MISMATCH"),
            ("foreign-record", "GRAPH_SCOPE_MISMATCH"),
            ("missing-endpoint", "GRAPH_ENTITY_UNKNOWN"),
            ("namespace", "GRAPH_NAMESPACE_INVALID"),
            ("duplicate-entity", "GRAPH_DUPLICATE"),
            ("duplicate-edge", "GRAPH_DUPLICATE"),
            ("unknown-relation", "GRAPH_RELATION_INVALID"),
            ("authorization-field", "GRAPH_FIELDS_INVALID"),
            ("invalid-date", "GRAPH_DATE_INVALID"),
            ("unknown-condition", "GRAPH_RECORD_UNKNOWN"),
        ]
        for name, code in cases:
            with self.subTest(name=name):
                graph, records = graph_fixture()
                edge = graph["relations"][0]
                if name == "unknown-evidence":
                    edge["evidence_record_id"] = "pending-not-in-accepted-index"
                elif name == "wrong-sha":
                    edge["evidence_sha256"] = "0" * 64
                elif name == "foreign-record":
                    records[1]["project_id"] = "beta"
                elif name == "missing-endpoint":
                    edge["to"] = "alpha:absent"
                elif name == "namespace":
                    graph["entities"][1]["id"] = "beta:tool"
                elif name == "duplicate-entity":
                    graph["entities"].append(copy.deepcopy(graph["entities"][0]))
                elif name == "duplicate-edge":
                    graph["relations"].append(copy.deepcopy(edge))
                elif name == "unknown-relation":
                    edge["type"] = "approved_to_execute"
                elif name == "authorization-field":
                    edge["execution_authorized"] = True
                elif name == "invalid-date":
                    edge["valid_from"] = "2026-02-30"
                elif name == "unknown-condition":
                    edge["condition_record_ids"] = ["not-in-index"]
                with self.assertRaisesRegex(ValueError, code):
                    TypedGraph(graph, records)


class GraphRecallIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chmemx-graph-")
        self.root = Path(self.temp.name).resolve()
        self.store = self.root / "memory"
        self.project = self.root / "alpha"
        self.store.mkdir()
        self.project.mkdir()
        for path in (self.store, self.project):
            self.git(path, "init", "-q")
            self.git(path, "config", "user.name", "Fixture")
            self.git(path, "config", "user.email", "fixture@example.invalid")
        bodies = {
            "project": "Project Alpha is the synthetic project.",
            "tool": "Workshop Tool is the synthetic tool.",
            "use": "Project Alpha uses Workshop Tool.",
            "condition": "Production publication is forbidden; visual approval is required.",
        }
        self.write(
            self.store / "catalog.json",
            {
                "projects": {
                    "alpha": {"project_id": "alpha", "root": str(self.project), "title": "Alpha"}
                }
            },
        )
        entries = {}
        for identifier, body in bodies.items():
            source = self.project / f"{identifier}.md"
            source.write_text(body)
            self.git(self.project, "add", source.name)
            self.git(self.project, "commit", "-qm", "synthetic source")
            record = {
                "id": identifier,
                "key": f"alpha.{identifier}",
                "project_id": "alpha",
                "scope": "project",
                "class": "decision",
                "authority": "accepted",
                "status": "active",
                "body": body,
                "source": {
                    "kind": "git",
                    "project_root": str(self.project),
                    "path": source.name,
                    "hash": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
                    "commit": self.git(self.project, "rev-parse", "HEAD").strip(),
                },
                "nodes": [
                    {
                        "id": f"node-{identifier}",
                        "title": body,
                        "keywords": [identifier],
                        "aliases": [],
                        "related_node_ids": [],
                    }
                ],
            }
            self.write(self.store / f"projects/alpha/active/{identifier}.json", record)
            entries[f"decision:alpha.{identifier}"] = identifier
        self.write(self.store / "projects/alpha/active-index.json", {"entries": entries})
        self.git(self.store, "add", ".")
        self.git(self.store, "commit", "-qm", "synthetic accepted fixture")
        taxonomy = self.root / "taxonomy.json"
        self.write(
            taxonomy,
            {
                "cells": [
                    {
                        "id": "alpha-grid",
                        "title": "Alpha",
                        "keywords": [],
                        "aliases": [],
                        "project_id": "alpha",
                        "member_key_prefixes": ["alpha."],
                        "related_cell_ids": [],
                    }
                ]
            },
        )
        self.index = v3.build(self.store, taxonomy)
        self.taxonomy = taxonomy
        self.graph, _ = graph_fixture()
        evidence = next(r for r in self.index["records"] if r["id"] == "use")
        self.graph["relations"][0]["evidence_sha256"] = evidence["record_sha256"]
        self.graph["relations"][0]["condition_record_ids"] = ["condition"]
        self.reader = ExperimentalGraphRetriever(self.index, self.graph)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))

    @staticmethod
    def git(path, *args):
        return subprocess.run(
            ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
        ).stdout

    def query(self, **options):
        return self.reader.recall(
            "Alpha tools",
            self.project,
            graph_query={
                "entity": "Alpha",
                "relation": "uses",
                **options,
            },
        )

    def test_complete_condition_bundle_and_no_authorization(self):
        result = self.query()
        graph = result["graph"]
        self.assertEqual(["alpha:project", "alpha:tool"], graph["paths"][0]["entity_ids"])
        self.assertEqual(["condition", "project", "tool", "use"], sorted(graph["records"]))
        self.assertEqual(
            "Production publication is forbidden; visual approval is required.",
            graph["records"]["condition"]["body"],
        )
        self.assertEqual("candidate_projection", graph["authority"])
        self.assertFalse(graph["execution_authorized"])

    def test_explicit_condition_link_repairs_the_demonstrated_fragment_gap(self):
        ordinary = v3.Retriever(self.index).recall(
            "Project Alpha uses Workshop Tool", self.project, limit=1, association_limit=0
        )
        self.assertNotIn("condition", [r["id"] for r in ordinary["entries"]])
        self.assertIn("condition", self.query()["graph"]["records"])

    def test_stale_condition_blocks_the_whole_path(self):
        (self.project / "condition.md").write_text("Changed condition, unreviewed.")
        result = self.query()["graph"]
        self.assertEqual([], result["paths"])
        self.assertEqual({}, result["records"])
        self.assertEqual("SOURCE_NOT_CURRENT", result["blocked_paths"][0]["reason"])

    def test_stale_relation_evidence_blocks_the_whole_path(self):
        (self.project / "use.md").write_text("The use relationship has changed.")
        self.assertEqual([], self.query()["graph"]["paths"])

    def test_condition_changed_during_source_reads_invalidates_the_path(self):
        original = Path.read_bytes
        changed = False

        def source_bytes(path):
            nonlocal changed
            if path == self.project / "tool.md" and not changed:
                (self.project / "condition.md").write_text("Condition changed during retrieval.")
                changed = True
            return original(path)

        with patch.object(Path, "read_bytes", new=source_bytes):
            graph = self.query()["graph"]
        self.assertEqual([], graph["paths"])
        self.assertEqual({}, graph["records"])
        self.assertEqual("SOURCE_CHANGED_DURING_QUERY", graph["blocked_paths"][0]["reason"])

    def test_bundle_budget_does_not_drop_only_the_exception(self):
        graph = self.query(max_bundle_bytes=32)["graph"]
        self.assertEqual([], graph["paths"])
        self.assertEqual("BUNDLE_BUDGET_EXCEEDED", graph["blocked_paths"][0]["reason"])

    def test_normal_recall_keeps_existing_primary_results(self):
        old = v3.Retriever(self.index).recall("alpha.tool", self.project)
        new = self.reader.recall("alpha.tool", self.project)
        self.assertEqual([r["id"] for r in old["entries"]], [r["id"] for r in new["entries"]])
        self.assertNotIn("graph", new)

    def test_unknown_entity_does_not_fall_back_to_similar_memory(self):
        graph = self.query(entity="Nonexistent but similar tool")["graph"]
        self.assertEqual("NO_ENTITY", graph["status"])
        self.assertEqual([], graph["paths"])

    def test_dirty_store_is_not_hidden_by_graph_navigation(self):
        (self.store / "catalog.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "STORE_DIRTY"):
            self.query()

    def test_candidate_changes_do_not_modify_canonical_memory(self):
        before = self.git(self.store, "rev-parse", "HEAD")
        for _ in range(2):
            self.query()
        self.assertEqual(before, self.git(self.store, "rev-parse", "HEAD"))
        self.assertEqual("", self.git(self.store, "status", "--porcelain"))

    def test_tampered_index_fails_before_graph_query(self):
        changed = copy.deepcopy(self.index)
        changed["records"][0]["key"] = "injected.key"
        with self.assertRaisesRegex(ValueError, "INDEX_DIGEST_MISMATCH"):
            ExperimentalGraphRetriever(changed, self.graph)

    def test_unbound_pending_record_cannot_be_used_as_graph_evidence(self):
        pending = self.store / "projects/alpha/pending/not-active.json"
        self.write(pending, {"id": "pending", "status": "PENDING_CURATION"})
        graph = copy.deepcopy(self.graph)
        graph["relations"][0]["evidence_record_id"] = "pending"
        with self.assertRaisesRegex(ValueError, "GRAPH_RECORD_UNKNOWN"):
            ExperimentalGraphRetriever(self.index, graph)

    def test_source_read_budget_does_not_emit_an_incomplete_bundle(self):
        graph = self.query(max_evidence_reads=1)["graph"]
        self.assertEqual([], graph["paths"])
        self.assertEqual({}, graph["records"])
        self.assertEqual("EVIDENCE_READ_BUDGET_EXCEEDED", graph["blocked_paths"][0]["reason"])

    def test_historical_lesson_cannot_support_a_current_relation(self):
        path = self.store / "projects/alpha/active/use.json"
        record = json.loads(path.read_text())
        record["class"] = "lesson"
        self.write(path, record)
        active = self.store / "projects/alpha/active-index.json"
        values = json.loads(active.read_text())
        del values["entries"]["decision:alpha.use"]
        values["entries"]["lesson:alpha.use"] = "use"
        self.write(active, values)
        self.git(self.store, "add", ".")
        self.git(self.store, "commit", "-qm", "synthetic lesson fixture")
        index = v3.build(self.store, self.taxonomy)
        graph = copy.deepcopy(self.graph)
        graph["relations"][0]["evidence_sha256"] = next(
            r["record_sha256"] for r in index["records"] if r["id"] == "use"
        )
        (self.project / "use.md").write_text("Relationship changed after the lesson.")
        ordinary = v3.Retriever(index).recall("alpha.use", self.project)
        self.assertEqual("HISTORICAL", ordinary["entries"][0]["source_freshness"]["status"])
        reader = ExperimentalGraphRetriever(index, graph)
        result = reader.recall(
            "tools", self.project, graph_query={"entity": "Alpha", "relation": "uses"}
        )
        self.assertEqual([], result["graph"]["paths"])

    def test_utf8_aliases_work_without_changing_namespace(self):
        graph = copy.deepcopy(self.graph)
        graph["entities"][0]["aliases"] = ["阿尔法", "ALPHA"]
        reader = ExperimentalGraphRetriever(self.index, graph)
        result = reader.recall(
            "工具", self.project, graph_query={"entity": "阿尔法", "relation": "uses"}
        )
        self.assertEqual("alpha:project", result["graph"]["anchor_id"])
        self.assertEqual(1, len(result["graph"]["paths"]))


if __name__ == "__main__":
    unittest.main()
