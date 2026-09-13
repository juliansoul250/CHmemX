"""Synthetic capability catalogs; never use installed skills or private memory."""

import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chmemx.skill_retrieval import IntentError, _read_json, main, recommend


def skill(identifier, capability="ui", actions=None, **kwargs):
    return dict(
        id=identifier,
        description="review UI 界面审查",
        capabilities=[capability],
        actions=actions or ["read"],
        **kwargs,
    )


def intent(**kwargs):
    return dict(
        decision="search",
        query="review UI 界面审查",
        capabilities=["ui"],
        allowed_actions=["read"],
        forbidden_actions=["generate"],
        **kwargs,
    )


class SpyEncoder:
    def __init__(self, output=None):
        self.calls = []
        self.output = output

    def encode(self, texts):
        self.calls.append(texts)
        return self.output if self.output is not None else [[1.0, 0.0] for _ in texts]


class SkillRetrievalTests(unittest.TestCase):
    def test_forbidden_generation_is_removed_before_encoding(self):
        spy = SpyEncoder()
        catalog = [skill("review"), skill("generate", actions=["generate"])]
        catalog[1]["description"] = "EXCLUDED_SECRET_MARKER"
        result = recommend(catalog, intent(), encoder=spy)
        self.assertEqual([r["id"] for r in result["candidates"]], ["review"])
        self.assertEqual(spy.calls, [["review UI 界面审查", "review UI 界面审查"]])
        self.assertEqual(result["excluded"], [{"id": "generate", "reason": "FORBIDDEN_ACTION"}])
        self.assertFalse(result["execution_authorized"])

    def test_structured_read_only_preserves_review_not_entire_ui_exclusion(self):
        result = recommend([skill("review"), skill("edit", actions=["write"])], intent())
        self.assertEqual(result["candidates"][0]["id"], "review")
        self.assertEqual(result["excluded"][0]["reason"], "ACTION_NOT_ALLOWED")

    def test_local_edit_is_distinct_from_full_generation(self):
        plan = intent()
        plan.update(allowed_actions=["edit-local"], forbidden_actions=["generate-full"])
        result = recommend(
            [
                skill("button", actions=["edit-local"]),
                skill("whole-screen", actions=["generate-full"]),
            ],
            plan,
        )
        self.assertEqual([x["id"] for x in result["candidates"]], ["button"])

    def test_mixed_actions_are_not_silently_partially_enabled(self):
        result = recommend([skill("mixed", actions=["read", "generate"])], intent())
        self.assertEqual(result["status"], "NO_MATCH")

    def test_positive_capability_excludes_negated_domain_mentions(self):
        plan = intent()
        plan.update(query="stock report; do not generate UI", capabilities=["stock-read"])
        result = recommend([skill("ui"), skill("stock", capability="stock-read")], plan)
        self.assertEqual([x["id"] for x in result["candidates"]], ["stock"])

    def test_same_map_word_does_not_cross_capability_boundary(self):
        plan = intent()
        plan.update(query="地图 map", capabilities=["game-level"])
        catalog = [skill("game", "game-level"), skill("video", "geographic-video")]
        for entry in catalog:
            entry["description"] = "地图 map"
        self.assertEqual(recommend(catalog, plan)["candidates"][0]["id"], "game")

    def test_project_and_stage_filter_before_model(self):
        catalog = [
            skill("global"),
            skill("local", projects=["demo"], stages=["review"]),
            skill("other-project", projects=["other"]),
            skill("other-stage", stages=["build"]),
        ]
        spy = SpyEncoder()
        result = recommend(catalog, intent(project="demo", stage="review"), encoder=spy)
        self.assertEqual([r["id"] for r in result["candidates"]], ["global", "local"])
        self.assertEqual(len(spy.calls[0]), 3)
        self.assertEqual(
            {r["reason"] for r in result["excluded"]}, {"PROJECT_MISMATCH", "STAGE_MISMATCH"}
        )

    def test_missing_context_cannot_admit_project_or_stage_restrictions(self):
        result = recommend(
            [skill("project", projects=["demo"]), skill("stage", stages=["review"])], intent()
        )
        self.assertEqual(result["status"], "NO_MATCH")

    def test_conflicting_actions_need_clarification_without_model(self):
        spy = SpyEncoder()
        plan = intent()
        plan["allowed_actions"].append("generate")
        result = recommend([skill("review")], plan, encoder=spy)
        self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(spy.calls, [])

    def test_abstain_and_uncertainty_skip_catalog_and_model(self):
        for decision, expected in [("abstain", "ABSTAIN"), ("clarify", "NEEDS_CLARIFICATION")]:
            with self.subTest(decision=decision):
                spy = SpyEncoder()
                self.assertEqual(
                    recommend(None, {"decision": decision}, encoder=spy)["status"], expected
                )
                self.assertEqual(spy.calls, [])

    def test_no_eligible_skills_never_calls_model(self):
        spy = SpyEncoder()
        self.assertEqual(recommend([], intent(), encoder=spy)["status"], "NO_MATCH")
        self.assertEqual(spy.calls, [])

    def test_resolved_double_negative_does_not_trigger_keyword_blacklist(self):
        # The caller resolved "not forbidden to generate UI" as a positive goal.
        plan = intent()
        plan.update(
            query="not forbidden to generate UI", allowed_actions=["generate"], forbidden_actions=[]
        )
        result = recommend([skill("generate", actions=["generate"])], plan, encoder=SpyEncoder())
        self.assertEqual(result["candidates"][0]["id"], "generate")
        self.assertFalse(result["execution_authorized"])

    def test_ranking_and_limit_apply_only_after_filter(self):
        catalog = [skill("b"), skill("a"), skill("c")]
        result = recommend(catalog, intent(), limit=2, encoder=SpyEncoder())
        self.assertEqual([r["id"] for r in result["candidates"]], ["a", "b"])
        self.assertTrue(all(r["constraint_check"] == "PASS" for r in result["candidates"]))

    def test_inputs_are_not_mutated_or_returned_as_instructions(self):
        catalog, plan = [skill("review")], intent()
        original = copy.deepcopy((catalog, plan))
        result = recommend(catalog, plan)
        self.assertEqual((catalog, plan), original)
        self.assertNotIn("description", result["candidates"][0])

    def test_ranking_cannot_change_the_validated_constraint_snapshot(self):
        catalog, plan = [skill("review")], intent()

        class MutatingEncoder:
            def encode(self, texts):
                catalog[0]["actions"].append("generate")
                plan["allowed_actions"].append("generate")
                return [[1.0] for _ in texts]

        result = recommend(catalog, plan, encoder=MutatingEncoder())
        self.assertEqual(result["candidates"][0]["actions"], ["read"])

    def test_bad_catalog_metadata(self):
        for field, value in [
            ("actions", []),
            ("actions", "read"),
            ("projects", []),
            ("stages", None),
            ("capabilities", ["UI", "ui"]),
            ("description", ""),
            ("extra", True),
        ]:
            with self.subTest(field=field, value=value):
                entry = skill("test")
                entry[field] = value
                with self.assertRaises(IntentError):
                    recommend([entry], intent())
        with self.assertRaises(IntentError):
            recommend([skill("same"), skill("SAME")], intent())

    def test_bad_intent_and_limits(self):
        for field, value in [
            ("query", ""),
            ("capabilities", []),
            ("allowed_actions", []),
            ("forbidden_actions", "generate"),
            ("decision", "guess"),
            ("unknown", True),
            ("query", "x" * 2049),
        ]:
            with self.subTest(field=field):
                plan = intent()
                plan[field] = value
                with self.assertRaises(IntentError):
                    recommend([], plan)
        for limit in (0, 21, True, 1.0):
            with self.assertRaises(IntentError):
                recommend([], intent(), limit=limit)

    def test_encoder_shape_finiteness_and_numeric_extremes(self):
        for output in (
            [],
            [[1]],
            [[1], [1, 2]],
            [[True], [1]],
            [[float("nan")], [1]],
            [[1], [float("inf")]],
            [[10**1000], [1]],
            [[], []],
        ):
            with self.subTest(output=output), self.assertRaises(IntentError):
                recommend([skill("review")], intent(), encoder=SpyEncoder(output))
        result = recommend(
            [skill("review")], intent(), encoder=SpyEncoder([[1e308, 1e308], [1e308, 1e308]])
        )
        self.assertEqual(result["candidates"][0]["score"], 1.0)
        self.assertEqual(
            recommend([skill("review")], intent(), encoder=SpyEncoder([[0, 0], [0, 0]]))["status"],
            "NO_MATCH",
        )

    def test_cli_without_a_memory_store_or_semantic_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog.json").write_text(json.dumps([skill("界面审查")]), encoding="utf-8")
            (root / "intent.json").write_text(json.dumps(intent()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "chmemx.skill_retrieval",
                    "--catalog",
                    str(root / "catalog.json"),
                    "--intent",
                    str(root / "intent.json"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "CANDIDATES")
            self.assertEqual(json.loads(result.stdout)["candidates"][0]["id"], "界面审查")
            self.assertEqual(
                sorted(p.name for p in root.iterdir()), ["catalog.json", "intent.json"]
            )

    def test_json_rejects_duplicate_fields_and_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for raw in ('{"decision":"abstain","decision":"search"}', '{"x":NaN}'):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(IntentError):
                    _read_json(path)

    def test_deep_json_returns_structured_cli_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep.json"
            path.write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
            with patch.object(
                sys, "argv", ["skills", "--catalog", str(path), "--intent", str(path)]
            ):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(), 2)
            self.assertEqual(json.loads(output.getvalue())["code"], "SKILL_INTENT_INVALID")

    def test_optional_local_model_uses_bounded_batches(self):
        counts = []

        class FakeModel:
            def __init__(self, path):
                pass

            def encode(self, texts):
                counts.append(len(texts))
                return [[1.0] for _ in texts]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog.json").write_text(
                json.dumps([skill(f"s-{i}") for i in range(22)]), encoding="utf-8"
            )
            (root / "intent.json").write_text(json.dumps(intent()), encoding="utf-8")
            argv = [
                "skills",
                "--catalog",
                str(root / "catalog.json"),
                "--intent",
                str(root / "intent.json"),
                "--model-dir",
                str(root),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch("chmemx.scripts.retrieval_v3.LocalEncoder", FakeModel),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(), 0)
        self.assertEqual(counts, [8, 8, 7])


if __name__ == "__main__":
    unittest.main()
