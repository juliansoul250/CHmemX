"""Behavioral regressions for the second, explicitly authorized repair pass."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_service as fixtures
import test_retrieval_hardening as hardening
from chmemx.scripts import evaluate_retrieval as evaluation
from chmemx.scripts import retrieval_v3 as v3
from chmemx.scripts import vector_memory as lexical


def suite(case, name="test"):
    return dict(schema_version=1, type=lexical.EVALUATION_TYPE, suite_id=name,
                cases=[dict(id="case", query="delivery policy", expected_keys=["delivery"],
                            **case)])


class LabelContracts(unittest.TestCase):
    def test_identical_inputs_cannot_require_and_forbid_same_association(self):
        positive = suite({"expected_association_keys": ["review"]}, "positive")
        negative = suite({"forbidden_keys": ["review"]}, "negative")
        with self.assertRaisesRegex(lexical.VectorError, "EVALUATION_LABEL_CONFLICT"):
            lexical.validate_evaluation_suites([positive, negative])

    def test_query_whitespace_matches_recall_normalization(self):
        positive = suite({"expected_association_keys": ["review"]})
        negative = suite({"forbidden_keys": ["review"]})
        negative["cases"][0]["query"] = "  delivery policy\n"
        with self.assertRaisesRegex(lexical.VectorError, "EVALUATION_LABEL_CONFLICT"):
            lexical.validate_evaluation_suites([positive, negative])

    def test_console_summary_excludes_cases_from_nested_reports(self):
        report = {"passed": False, "reports": [{"passed": True, "cases": [{"query": "private"}]}]}
        self.assertEqual({"passed": False, "reports": [{"passed": True}]},
                         evaluation.console_summary(report))
        self.assertIn("cases", report["reports"][0])

    def test_different_scope_or_association_limit_is_not_a_contradiction(self):
        positive = suite({"expected_association_keys": ["review"]})
        for changes in ({"project_id": "another"}, {"association_limit": 0}):
            with self.subTest(changes=changes):
                negative = suite({"forbidden_keys": ["review"], **changes})
                lexical.validate_evaluation_suites([positive, negative])

    def test_conflict_in_one_case_and_impossible_zero_budget_are_rejected(self):
        for changes in ({"forbidden_association_keys": ["review"]}, {"association_limit": 0}):
            with self.subTest(changes=changes), self.assertRaises(lexical.VectorError):
                lexical.validate_evaluation_suites([
                    suite({"expected_association_keys": ["review"], **changes})])

    def test_association_limit_is_a_bounded_integer(self):
        for value in (True, -1, 9, 1.5, "2"):
            with self.subTest(value=value), self.assertRaises(lexical.VectorError):
                lexical.validate_evaluation_suite(suite({"association_limit": value}))

    def test_negative_and_positive_labels_for_same_inputs_conflict(self):
        negative = suite({})
        negative["cases"][0].update(expected_keys=[], expect_empty=True)
        with self.assertRaisesRegex(lexical.VectorError, "EVALUATION_LABEL_CONFLICT"):
            lexical.validate_evaluation_suites([negative, suite({})])


class FollowupRetrieval(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown
    add = hardening.RetrievalHardening.add

    def test_topic_overlap_does_not_answer_missing_price(self):
        self.add("preference.editor.theme", "Use the dark theme in the editor.", title="Editor theme")
        self.assertEqual([], self.service.recall("How much does the editor subscription cost?")["entries"])
        self.assertEqual("preference.editor.theme",
                         self.service.recall("What editor theme should I use?")["entries"][0]["key"])

    def test_evaluator_forwards_zero_association_budget_before_expansion(self):
        self.add("workflow.delivery", "Delivery requires peer review.")
        path = self.root / "suite.json"
        data = suite({"association_limit": 0})
        data["cases"][0].update(query="workflow.delivery", expected_keys=["workflow.delivery"])
        path.write_text(json.dumps(data))
        original = v3.Retriever.recall
        calls = []

        def recall(reader, query, cwd, limit=5, association_limit=3):
            calls.append(association_limit)
            return original(reader, query, cwd, limit, association_limit)

        with patch.object(v3.Retriever, "recall", recall):
            report = evaluation.evaluate(self.service.index_path, path)
        self.assertTrue(report["passed"])
        self.assertEqual([0], calls)

    def test_multi_suite_conflicts_are_rejected_before_reader_load(self):
        paths = []
        for n, data in enumerate((suite({"expected_association_keys": ["review"]}),
                                  suite({"forbidden_keys": ["review"]}))):
            path = self.root / f"suite-{n}.json"
            path.write_text(json.dumps(data))
            paths.append(path)
        with patch.object(v3, "Retriever", side_effect=AssertionError("model must not load")):
            with self.assertRaisesRegex(lexical.VectorError, "EVALUATION_LABEL_CONFLICT"):
                evaluation.evaluate_suites(self.service.index_path, paths)

    def test_compatible_suites_keep_separate_gates_and_failures(self):
        self.add("workflow.delivery", "Delivery requires peer review.")
        good = suite({})
        good["cases"][0].update(query="workflow.delivery", expected_keys=["workflow.delivery"])
        bad = suite({"top_k": 1, "required_rank": 1}, "second")
        bad["cases"][0].update(query="no matching answer", expected_keys=["missing"])
        paths = []
        for n, data in enumerate((good, bad)):
            path = self.root / f"compatible-{n}.json"
            path.write_text(json.dumps(data))
            paths.append(path)
        report = evaluation.evaluate_suites(self.service.index_path, paths)
        self.assertFalse(report["passed"])
        self.assertEqual([True, False], [r["passed"] for r in report["reports"]])
        self.assertEqual(2, report["case_count"])
        self.assertEqual(1, report["reports"][1]["cases"][0]["required_rank"])

    def test_embedding_reuse_is_bound_to_passage_recipe(self):
        self.add("workflow.delivery", "Delivery requires peer review.")

        class Encoder:
            lock = {"revision": "fake-test-only"}

            def __init__(self, _):
                pass

            def chunks(self, text):
                return [text]

            def encode(self, texts):
                return [[1.0, 0.0] for _ in texts]

        with patch.object(v3, "LocalEncoder", Encoder):
            taxonomy = self.service.state / "taxonomy.json"
            first = v3.build(self.service.store, taxonomy, Path("fixture"))
            old = copy.deepcopy(first)
            for record in old["records"]:
                raw = json.loads((self.service.store / record["record_path"]).read_text())
                record["embedding_input_digest"] = v3.digest({"body": raw["body"], "model_lock": Encoder.lock})
            old.pop("index_digest")
            old["index_digest"] = v3.digest(old)
            refreshed = v3.build(self.service.store, taxonomy, Path("fixture"), previous=old)
            reused = v3.build(self.service.store, taxonomy, Path("fixture"), previous=refreshed)
        self.assertGreater(refreshed["build_statistics"]["embedding_chunks_computed"], 0)
        self.assertEqual(0, reused["build_statistics"]["embedding_chunks_computed"])


class PassageBoundaries(unittest.TestCase):
    def test_sentences_stay_separate_and_long_sentence_keeps_overflow(self):
        class Tokenizer:
            def encode(self, text):
                pieces = [text[i:i + 32] for i in range(0, len(text), 32)]
                return SimpleNamespace(ids=pieces[0], overflowing=[SimpleNamespace(ids=p) for p in pieces[1:]])

            def decode(self, ids, **_):
                return ids

        encoder = object.__new__(v3.LocalEncoder)
        encoder.tokenizer = Tokenizer()
        self.assertEqual(["First rule.", "Second rule。", "最后条件；", "保留末尾"],
                         encoder.chunks("First rule. Second rule。最后条件；保留末尾"))
        long = "Long condition " * 20 + "TAIL_EVIDENCE"
        self.assertEqual(long, "".join(encoder.chunks(long)))


if __name__ == "__main__":
    unittest.main()
