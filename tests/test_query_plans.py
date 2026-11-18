"""Understanding is caller-supplied; these test the plan/execution contract, not NLP."""
import copy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_service as fixtures
import test_retrieval_hardening as hardening
import test_recall_scope as scope_fixtures
import test_retrieval_efficiency as efficiency_fixtures
from chmemx.scripts import retrieval_v3 as v3
from chmemx.mcp_server import dispatch


def plan(query, *, facets=(), entities=(), constraints=(), decision="search"):
    return {"version": 1, "query": query, "decision": decision,
            "goal": "Find evidence for the requested information, retaining all qualifications.",
            "entities": list(entities), "constraints": list(constraints),
            "facets": [{"id": f"part-{n}", "query": text} for n, text in enumerate(facets)]}


class QueryPlanContract(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown
    add = hardening.RetrievalHardening.add

    def call(self, name="recall", **arguments):
        response, _ = dispatch(self.service, {"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": name, "arguments": arguments}}, True)
        return response["result"], json.loads(response["result"]["content"][0]["text"])

    def test_missing_plan_does_not_load_reader_even_through_start(self):
        with patch.object(self.service, "_reader", side_effect=AssertionError("no model before understanding")):
            for name in ("recall", "start"):
                response, result = self.call(name, query="Who approves restart after inspection?")
                self.assertFalse(response["isError"])
                result = result.get("recall", result)
                self.assertEqual("NEEDS_UNDERSTANDING", result["status"])
                self.assertEqual([], result["entries"])

    def test_clarify_and_abstain_do_not_search(self):
        with patch.object(self.service, "_reader", side_effect=AssertionError("no inference")):
            for decision, status in (("clarify", "NEEDS_CLARIFICATION"), ("abstain", "ABSTAIN")):
                response, result = self.call(query="Which one?", query_plan=plan("Which one?", decision=decision))
                self.assertFalse(response["isError"])
                self.assertEqual(status, result["status"])

    def test_invalid_plans_fail_before_reader_loading(self):
        query = "Aster cold-chain restart must not bypass inspection"
        base = plan(query, entities=["Aster", "cold-chain"], constraints=["must not bypass inspection"])
        variants = [dict(base, version=True), dict(base, query="different"), dict(base, expected_keys=["answer"]),
                    dict(base, entities=["invented"]), dict(base, facets=[{"id": "one", "query": "Aster restart"}]),
                    dict(base, facets=[{"id": str(i), "query": query} for i in range(4)])]
        with patch.object(self.service, "_reader", side_effect=AssertionError("no inference")):
            for value in variants:
                with self.subTest(value=value):
                    response, _ = self.call(query=query, query_plan=value)
                    self.assertTrue(response["isError"])

    def test_compound_and_negative_spans_retained_with_candidate_only_claim(self):
        self.add("lab.restart", "Aster cold-chain restart must not bypass inspection.")
        query = "Aster cold-chain restart must not bypass inspection"
        response, result = self.call(query=query, query_plan=plan(query, facets=[query + " requirements"],
                                    entities=["Aster", "cold-chain"], constraints=["must not bypass inspection"]))
        self.assertFalse(response["isError"])
        self.assertEqual("caller_interpreted", result["understanding"]["status"])
        self.assertEqual("not_assessed", result["understanding"]["answer_support"])
        self.assertEqual(["must not bypass inspection"], result["understanding"]["constraints"])
        self.assertFalse(result["understanding"]["interpretation_verified"])

    def test_facets_merge_before_one_body_read_and_share_budget(self):
        self.add("lab.inspection", "Fume hood inspection is performed by the laboratory technician.", title="Fume hood inspection")
        self.add("lab.restart", "Fume hood restart requires the safety officer's approval.", title="Fume hood restart")
        query = "Who inspects the fume hood and who approves restart?"
        value = plan(query, facets=["fume hood inspection performed by", "fume hood restart approval"], entities=["fume hood"])
        reader = self.service._reader()
        with patch.object(reader, "read", wraps=reader.read) as reads:
            response, result = self.call(query=query, query_plan=value, limit=2, association_limit=0)
        self.assertFalse(response["isError"])
        self.assertEqual({"lab.inspection", "lab.restart"}, {r["key"] for r in result["entries"]})
        ids = [call.args[0] for call in reads.call_args_list]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(ids), 20)
        self.assertEqual(3, len(result["understanding"]["facets"]))
        self.assertTrue(all(r["vector_pointer"]["query_ids"] for r in result["entries"]))

    def test_subquery_cannot_widen_original_scope(self):
        self.add("global.format", "Keep the answer concise.")
        reader = self.service._reader()
        query = "answer format"
        value = plan(query, facets=["project-fixture deployment rules"])
        original = reader.allowed
        calls = []
        def allowed(text, current):
            calls.append(text)
            return original(text, current)
        with patch.object(reader, "allowed", allowed):
            reader.recall(query, self.project, query_plan=value)
        self.assertEqual([query], calls)

    def test_planned_reads_still_fail_closed_if_store_changes(self):
        self.add("lab.inspection", "Inspection is required.")
        reader = self.service._reader()
        original = reader.read
        def change(*args, **kwargs):
            record = original(*args, **kwargs)
            with (self.service.store / "catalog.json").open("a") as f:
                f.write("\n")
            return record
        with patch.object(reader, "read", change), self.assertRaisesRegex(ValueError, "STORE_DIRTY"):
            reader.recall("lab.inspection", self.project, query_plan=plan("lab.inspection"))

    def test_literal_compatibility_does_not_claim_understanding(self):
        result = self.service.recall("unknown")
        self.assertEqual("literal_compatibility", result["understanding"]["status"])

    def test_mcp_cannot_inject_internal_compatibility_switch(self):
        response, _ = self.call(query="unknown", require_understanding=False)
        self.assertTrue(response["isError"])

    def test_public_and_standalone_cli_gate_before_index_model_loading(self):
        commands = [["-m", "chmemx.cli", "--store", str(self.service.store), "--cwd", str(self.project),
                     "recall", "who may restart?"],
                    ["-m", "chmemx.scripts.retrieval_v3", "recall", "--index", str(self.root / "absent.json"),
                     "--query", "who may restart?"]]
        for args in commands:
            result = subprocess.run([sys.executable, "-B", *args], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual("NEEDS_UNDERSTANDING", json.loads(result.stdout)["status"])

    def test_encoder_window_cannot_silently_drop_late_constraints(self):
        self.add("lab.inspection", "Inspection is required.")
        reader = self.service._reader()
        reader.encoder = SimpleNamespace(tokenizer=SimpleNamespace(
            encode=lambda text: SimpleNamespace(overflowing=[object()])),
            encode=lambda texts: self.fail("truncated input must not enter encoder"))
        result = reader.recall("lab.inspection", self.project, query_plan=plan("lab.inspection"))
        self.assertTrue(result["understanding"]["facets"][0]["dense_window_exceeded"])
        self.assertEqual("lab.inspection", result["entries"][0]["key"])

    def test_stale_candidates_exhaust_shared_scan_budget_visibly(self):
        root = self.root / "scan-budget"
        root.mkdir()
        service = efficiency_fixtures.fixture(root, count=21)
        reader = service._reader()
        query = "Synthetic reference text"
        ranked = reader._rank_pointers(query, service.cwd, allowed_projects={None})
        self.assertEqual(21, len(ranked["order"]))
        stale = set(ranked["order"][:20])
        original = v3.source_freshness.check
        def freshness(record):
            result = original(record)
            if record["id"] in stale:
                result.update(current_use_allowed=False, status="REVIEW_REQUIRED")
            return result
        with patch.object(v3.source_freshness, "check", freshness), patch.object(reader, "read", wraps=reader.read) as reads:
            result = reader.recall(query, service.cwd, 1, 0, query_plan=plan(query))
        self.assertEqual([], result["entries"])
        self.assertEqual(20, len(result["needs_review"]))
        self.assertEqual(20, reads.call_count)
        self.assertTrue(result["understanding"]["candidate_union_truncated"])
        self.assertTrue(result["retrieval"]["scan_budget_exhausted"])

    def test_explicit_plan_does_not_modify_input(self):
        self.add("lab.inspection", "Inspection is required.")
        value = plan("lab.inspection")
        before = copy.deepcopy(value)
        self.call(query="lab.inspection", query_plan=value)
        self.assertEqual(before, value)


class PlannedScopeBoundaries(unittest.TestCase):
    setUp = scope_fixtures.RecallScopeAcceptance.setUp
    tearDown = scope_fixtures.RecallScopeAcceptance.tearDown
    remember = scope_fixtures.RecallScopeAcceptance.remember

    def test_unknown_context_facets_cannot_enable_foreign_fallback(self):
        self.remember("preference.recovery.system", "Nickel vapor recovery uses a condenser.",
                      "project", ["nickel vapor recovery"])
        query = "nickel vapor recovery condenser"
        # Establish that the old literal fallback really would return this record.
        self.assertEqual("preference.recovery.system", self.reader.recall(query)["entries"][0]["key"])
        value = plan(query, facets=["atlas preference.recovery.system"])
        result = self.reader.recall_understood(query, query_plan=value)
        self.assertEqual([], result["entries"])
        self.assertEqual([], result["associations"])

    def test_explicit_original_scope_survives_and_stale_fact_is_not_current(self):
        self.remember("preference.recovery.system", "Nickel vapor recovery uses a condenser.",
                      "project", ["nickel vapor recovery"])
        query = "atlas nickel vapor recovery condenser"
        value = plan(query, facets=["atlas preference.recovery.system"], entities=["atlas"])
        result = self.reader.recall_understood(query, query_plan=value)
        self.assertEqual("preference.recovery.system", result["entries"][0]["key"])
        (self.project / "source-1.md").write_text("The recovery requirements changed.")
        result = self.reader.recall_understood(query, query_plan=value)
        self.assertEqual([], result["entries"])
        self.assertEqual("preference.recovery.system", result["needs_review"][0]["key"])
        self.assertFalse(result["needs_review"][0]["source_freshness"]["current_use_allowed"])


if __name__ == "__main__":
    unittest.main()
