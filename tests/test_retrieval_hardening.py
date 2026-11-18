"""Frozen synthetic regressions: abstention, collision trust and snapshot safety."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import test_service as fixtures
from chmemx.runtime import simple_memory as core
from chmemx.scripts import evaluate_retrieval as evaluation
from chmemx.scripts import retrieval_v3 as v3
from chmemx.scripts import vector_memory as lexical


class EvaluationContracts(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown

    def suite(self, cases, **kwargs):
        return dict(schema_version=1, type=lexical.EVALUATION_TYPE, suite_id="fixture-evaluation",
                    thresholds={}, cases=cases, **kwargs)

    def evaluate(self, suite, outputs):
        index = {**self.service._reader().index, "type": lexical.INDEX_TYPE}
        return lexical.evaluate_index(index, suite, ranker=lambda _, query, *args: outputs[query])

    def save_suite(self, suite):
        path = self.root / "suite.json"
        path.write_text(json.dumps(suite))
        return path

    def test_explicit_abstention_loads_but_unlabelled_empty_expectation_does_not(self):
        case = dict(id="none", query="unknown", expected_keys=[], expect_empty=True)
        suite = self.suite([case])
        self.assertTrue(lexical.load_evaluation_suite(self.save_suite(suite))["cases"][0]["expect_empty"])
        for changes in ({"expect_empty": False}, {"expect_empty": "true"},
                        {"expected_keys": ["known"]}, {"expected_association_keys": ["known"]}):
            with self.subTest(changes=changes), self.assertRaises(lexical.VectorError):
                lexical.load_evaluation_suite(self.save_suite(self.suite([{**case, **changes}])))

    def test_negative_cases_do_not_dilute_positive_metrics(self):
        suite = self.suite([
            dict(id="hit", query="hit", expected_keys=["known"]),
            dict(id="none", query="none", expected_keys=[], expect_empty=True),
        ])
        report = self.evaluate(suite, {"hit": {"entries": [{"key": "known"}]},
                                      "none": {"entries": [], "associations": []}})
        self.assertTrue(report["passed"])
        self.assertEqual(1, report["metrics"]["positive_case_count"])
        self.assertEqual(1.0, report["metrics"]["top1"])
        self.assertEqual(1.0, report["metrics"]["abstention_rate"])

    def test_primary_or_association_leaks_fail_abstention(self):
        suite = self.suite([dict(id="none", query="none", expected_keys=[], expect_empty=True)])
        for field in ("entries", "associations"):
            with self.subTest(field=field):
                result = {"entries": [], "associations": [], field: [{"key": "unrelated"}]}
                report = self.evaluate(suite, {"none": result})
                self.assertFalse(report["passed"])
                self.assertEqual(0.0, report["metrics"]["abstention_rate"])
                self.assertIsNone(report["metrics"]["top1"])

    def test_negative_only_suite_has_no_positive_quality_claim(self):
        suite = self.suite([dict(id="none", query="none", expected_keys=[], expect_empty=True)])
        report = self.evaluate(suite, {"none": {"entries": []}})
        self.assertTrue(report["passed"])
        self.assertEqual(0, report["metrics"]["positive_case_count"])
        self.assertIsNone(report["metrics"]["hit_at_3"])
        self.assertEqual(1.0, report["metrics"]["abstention_rate"])

    def test_all_relevant_recall_is_not_any_expected_hit(self):
        suite = self.suite([dict(id="multi", query="multi", expected_keys=["a", "b"],
                                relevant_keys=["a", "b"], top_k=1, required_rank=1)])
        report = self.evaluate(suite, {"multi": {"entries": [{"key": "a"}, {"key": "b"}]}})
        self.assertEqual(1.0, report["metrics"]["hit_at_3"])
        self.assertEqual(0.5, report["metrics"]["mean_recall_at_k"])

    def test_rank_limits_reject_booleans_and_fractions(self):
        for value in (True, 1.5, "3"):
            with self.subTest(value=value), self.assertRaises(lexical.VectorError):
                lexical.load_evaluation_suite(self.save_suite(self.suite([
                    dict(id="hit", query="hit", expected_keys=["known"], top_k=value)])))

    def test_ranking_only_is_explicit_and_cannot_claim_current_source_validation(self):
        uploaded = self.service.upload(**fixtures.ServiceAcceptance.preference(self))
        fixtures.ServiceAcceptance.approve_upload(self, uploaded)
        self.service.rebuild()
        path = self.save_suite(self.suite([dict(id="theme", query="preference.editor.theme",
                                              expected_keys=["preference.editor.theme"])]))
        with patch.object(v3.source_freshness, "check", side_effect=ValueError("SOURCE_UNAVAILABLE")):
            report = evaluation.evaluate(self.service.index_path, path, source_checks=False)
            self.assertTrue(report["passed"])
            self.assertFalse(report["source_checks"])
            self.assertIn("ranking-only", report["evaluation_scope"])
            with self.assertRaisesRegex(ValueError, "SOURCE_UNAVAILABLE"):
                evaluation.evaluate(self.service.index_path, path)

    def test_v3_limits_are_rejected_before_model_construction(self):
        self.service.rebuild()
        path = self.save_suite(self.suite([dict(id="hit", query="hit", expected_keys=["known"],
                                              top_k=64)]))
        with patch.object(v3, "Retriever", side_effect=AssertionError("model must not load")):
            with self.assertRaisesRegex(ValueError, "V3_EVALUATION_LIMIT_EXCEEDED"):
                evaluation.evaluate(self.service.index_path, path)


class RetrievalHardening(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown

    def add(self, key, body, title=None, aliases=None):
        candidate = self.service.runtime.propose({
            "scope": "global", "class": "preference", "key": key, "body": body,
            "source": self.service.source({"quote": body}, "global"),
            "submitted_by_agent": "source-alpha",
            "nodes": [{"id": "node-" + key.replace(".", "-"), "title": title or key,
                       "keywords": [title or key], "aliases": aliases or [], "related_node_ids": []}],
        }, self.project)
        batch = self.service.runtime.create_batch([candidate["candidate_id"]])
        review = self.service.runtime.review(batch["batch_id"])
        self.service.runtime.approve(batch["batch_id"], batch["batch_digest"],
                                     review["required_confirmation"], committing_agent="source-alpha")
        self.service.rebuild()

    def redis(self):
        self.add("service.redis.expiry", "Redis session cache expires after 900 seconds.", title="Redis")

    def test_bucket_collision_without_body_or_navigation_evidence_abstains(self):
        self.redis()
        result = self.service.recall("Who owns the invoice processing service?")
        self.assertEqual([], result["entries"])
        self.assertEqual([], result["associations"])

    def test_body_only_and_approved_node_alias_hits_survive(self):
        self.add("service.redis.expiry", "Redis session cache expires after 900 seconds. " * 4,
                 title="Authentication retention", aliases=["login duration"])
        for query in ("Redis session cache expires", "login duration", "service redis expiry", "service.redis.expiry"):
            with self.subTest(query=query):
                self.assertEqual("service.redis.expiry", self.service.recall(query)["entries"][0]["key"])

    def test_hashed_collision_cannot_suppress_semantic_fallback(self):
        self.redis()
        self.add("workflow.billing.owner", "Billing operations belong to Iris.")
        reader, _ = self.semantic_reader()
        result = reader.recall("Who owns the invoice processing service?", self.project)
        self.assertEqual(["workflow.billing.owner"], [r["key"] for r in result["entries"]])

    def semantic_reader(self):
        class Array:
            def __init__(self, values):
                self.values = values

            def __matmul__(self, vector):
                return [sum(a * b for a, b in zip(row, vector.values)) for row in self.values]

        class Encoder:
            lock = {"revision": "fixture"}

            def __init__(self, _):
                self.np = self
                self.matrix_builds = 0

            def asarray(self, values):
                if values and isinstance(values[0], list):
                    self.matrix_builds += 1
                return Array(values)

            def encode(self, texts):
                return [[1.0, 0.0] for _ in texts]

        index = copy.deepcopy(self.service._reader().index)
        index["hybrid"].update(model_directory="fixture", model_lock=Encoder.lock,
                               chunks=[{"record_id": r["id"], "vector":
                                        [1.0, 0.0] if r["key"] == "workflow.billing.owner" else [0.0, 1.0]}
                                       for r in index["records"]])
        index.pop("index_digest")
        index["index_digest"] = v3.digest(index)
        with patch.object(v3, "LocalEncoder", Encoder):
            reader = v3.Retriever(index)
        return reader, reader.encoder

    def test_dense_matrix_is_reused_without_changing_results(self):
        self.add("workflow.billing.owner", "Billing operations belong to Iris.")
        reader, encoder = self.semantic_reader()
        first = reader.recall("Who owns the invoice processing service?", self.project)
        second = reader.recall("Who owns the invoice processing service?", self.project)
        self.assertEqual("workflow.billing.owner", first["entries"][0]["key"])
        self.assertEqual(first["entries"][0]["vector_pointer"], second["entries"][0]["vector_pointer"])
        self.assertEqual(1, encoder.matrix_builds)

    def test_v3_uses_only_pre_and_post_git_snapshot_checks(self):
        self.redis()
        reader = self.service._reader()
        with patch.object(subprocess, "run", wraps=subprocess.run) as calls:
            result = reader.recall("service.redis.expiry", self.project)
        self.assertEqual("service.redis.expiry", result["entries"][0]["key"])
        commands = [c.args[0] for c in calls.call_args_list if c.args[0][0] == "git"]
        self.assertEqual(2, len(commands))

    def test_mid_query_change_is_still_rejected(self):
        self.redis()
        reader = self.service._reader()
        original = reader.read

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            with (self.service.store / "catalog.json").open("a") as stream:
                stream.write("\n")
            return result

        with patch.object(reader, "read", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "STORE_DIRTY"):
                reader.recall("service.redis.expiry", self.project)

    def test_legacy_pointer_entry_still_rejects_stale_head(self):
        self.redis()
        index = self.service._reader().lexical
        core.run_git(self.service.store, ["commit", "--allow-empty", "-qm", "fixture advance"])
        with self.assertRaisesRegex(lexical.VectorError, "INDEX_STALE"):
            lexical.rank_pointers(index, "service.redis.expiry", 1, self.project)


if __name__ == "__main__":
    unittest.main()
