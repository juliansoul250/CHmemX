"""Fact-identity and legacy-body regressions using disposable real stores."""

import unittest

import test_service as fixtures
from chmemx import fact_keys
from chmemx.runtime import simple_memory as core


class IdentityBoundaries(unittest.TestCase):
    setUp = fixtures.ServiceAcceptance.setUp
    tearDown = fixtures.ServiceAcceptance.tearDown
    activate_personal = fixtures.ServiceAcceptance.activate_personal

    @staticmethod
    def definition(key, aliases=None, scope="global", project_id=None, classes=None):
        return {
            "key": key,
            "scope": scope,
            "project_id": project_id,
            "allowed_classes": classes or ["preference"],
            "aliases": aliases or [],
            "description": "Synthetic fact definition.",
        }

    def install_catalog(self, definitions):
        core.atomic_json(self.service.store / "fact-key-catalog.json", {"facts": definitions})
        core.run_git(self.service.store, ["add", "fact-key-catalog.json"])
        core.run_git(self.service.store, ["commit", "-qm", "Synthetic fact catalog"])

    def alias_catalog(self, count):
        return [self.definition(f"preference.ui.item{i:04d}") for i in range(count)] + [
            self.definition("preference.zz_theme", ["preference.ui"])
        ]

    def test_exact_alias_is_independent_of_display_page(self):
        for count in (0, 99, 100, 1999):
            with self.subTest(decoys=count):
                self.install_catalog(self.alias_catalog(count))
                shown = fact_keys.directory(self.service.runtime, self.project, "preference.ui", 100)
                conflicts = fact_keys.conflicts(
                    self.service.runtime, self.project, "preference.ui", "global", "preference"
                )
                self.assertEqual(min(count + 1, 100), len(shown))
                self.assertEqual(["preference.zz_theme"], [row["key"] for row in conflicts])

    def seed_legacy_body(self, key, body):
        candidate = self.service.runtime.propose(
            {
                "scope": "global",
                "class": "preference",
                "key": key,
                "body": body,
                "source": self.service.source(
                    {"quote": "Synthetic legacy preference.", "thread_id": "fixture-owner"},
                    "global",
                ),
                "nodes": [{"id": "node-legacy", "title": "Legacy", "keywords": ["legacy"],
                           "aliases": [], "related_node_ids": []}],
                "operation": "promote",
                "submitted_by_agent": "source-alpha",
                "source_agent": "source-alpha",
            },
            self.project,
        )
        batch = self.service.runtime.create_batch([candidate["candidate_id"]])
        review = self.service.runtime.review(batch["batch_id"])
        return self.service.runtime.approve(
            batch["batch_id"], batch["batch_digest"], review["required_confirmation"],
            committing_agent="source-alpha",
        )

    def test_short_correction_of_long_record_still_requires_owner(self):
        for index, body in enumerate(("A" * 8192, "A" * 8193, "界" * 9000)):
            with self.subTest(old_characters=len(body)):
                key = f"preference.legacy.body{index}"
                original = self.seed_legacy_body(key, body)
                receipt = self.service.store / "approvals" / f"{original['batch_id']}.json"
                receipt_bytes = receipt.read_bytes()
                short = "Prefer concise explanations."
                result = self.service.upload(
                    key, short, {"quote": short, "thread_id": "fixture-owner"},
                    request_id=f"fixture-short-correction-{index}",
                )
                self.assertEqual("CONFLICT", result["status"])
                self.assertEqual(body, result["current"])
                self.assertEqual(original["commit"], core.git_head(self.service.store))
                review = self.service.review(result["upload_id"])
                with self.assertRaises(core.MemoryError) as denied:
                    self.service.approve(review["batch_id"], review["batch_digest"], "not approved")
                self.assertEqual("CONFIRMATION_REQUIRED", denied.exception.code)
                self.assertEqual(original["commit"], core.git_head(self.service.store))
                self.service.approve(
                    review["batch_id"], review["batch_digest"], review["required_confirmation"]
                )
                active = [r for r in self.service.runtime.active_records(None) if r["key"] == key]
                self.assertEqual([short], [r["body"] for r in active])
                self.assertEqual(original["record_ids"][0], active[0]["supersedes"])
                self.assertEqual(receipt_bytes, receipt.read_bytes())

    def test_personal_alias_stays_pending_but_unrelated_addition_can_commit(self):
        self.activate_personal()
        self.install_catalog(self.alias_catalog(100))
        before = core.git_head(self.service.store)
        source = {"quote": "Prefer compact layouts.", "thread_id": "fixture-owner"}
        result = self.service.upload("preference.ui", source["quote"], source)
        self.assertEqual("PENDING_CURATION", result["status"])
        self.assertEqual("FACT_IDENTITY_REVIEW_REQUIRED", result["reason"])
        self.assertEqual(["preference.zz_theme"], [r["key"] for r in result["fact_key_suggestions"]])
        self.assertEqual(before, core.git_head(self.service.store))
        self.assertEqual([], self.service.runtime.active_records(None))
        unrelated = self.service.upload("preference.editor.spacing", source["quote"], source)
        self.assertEqual("ACTIVE_COMMITTED", unrelated["status"])

    def test_team_alias_does_not_commit(self):
        self.install_catalog(self.alias_catalog(100))
        before = core.git_head(self.service.store)
        result = self.service.upload(
            "preference.ui", "Prefer compact layouts.",
            {"quote": "Prefer compact layouts.", "thread_id": "fixture-owner"},
        )
        self.assertEqual("PENDING_CURATION", result["status"])
        self.assertEqual(before, core.git_head(self.service.store))
        self.assertEqual([], self.service.runtime.active_records(None))

    def test_identity_checks_preserve_scope_project_and_class_boundaries(self):
        project_id, _ = self.service.runtime.project_for_cwd(self.project)
        self.install_catalog([
            self.definition("preference.zz_theme", ["shared.ui"]),
            self.definition("project.zz_theme", ["shared.ui"], "project", project_id, ["decision"]),
            self.definition("project.foreign", ["shared.ui"], "project", "project-other", ["decision"]),
        ])
        for scope, memory_class, key, expected in (
            ("global", "preference", "shared.ui", ["preference.zz_theme"]),
            ("project", "decision", "shared.ui", ["project.zz_theme"]),
            ("project", "evidence", "project.zz_theme", ["project.zz_theme"]),
            ("project", "decision", "project.zz_theme", []),
            ("project", "decision", "project.zz", []),
            ("global", "preference", "unrelated", []),
        ):
            with self.subTest(scope=scope, memory_class=memory_class, key=key):
                rows = fact_keys.conflicts(self.service.runtime, self.project, key, scope, memory_class)
                self.assertEqual(expected, [row["key"] for row in rows])

    def test_entire_catalog_is_validated_before_personal_acceptance(self):
        self.activate_personal()
        self.install_catalog([
            self.definition("preference.zz_theme", ["preference.ui"]),
            self.definition("preference.invalid", classes=["unsupported"]),
        ])
        before = core.git_head(self.service.store)
        result = self.service.upload(
            "preference.ui", "Prefer compact layouts.",
            {"quote": "Prefer compact layouts.", "thread_id": "fixture-owner"},
        )
        self.assertEqual("PENDING_CURATION", result["status"])
        self.assertEqual("FACT_CATALOG_REVIEW_REQUIRED", result["reason"])
        self.assertEqual(before, core.git_head(self.service.store))

    def test_new_upload_limit_and_legacy_byte_limit_are_unchanged(self):
        body = "界" * (core.MAX_BODY_BYTES // 3) + "AB"
        self.assertEqual(core.MAX_BODY_BYTES, len(body.encode("utf-8")))
        original = self.seed_legacy_body("preference.legacy.maximum", body)
        result = self.service.upload(
            "preference.legacy.maximum", "Prefer concise explanations.",
            {"quote": "Prefer concise explanations.", "thread_id": "fixture-owner"},
        )
        self.assertEqual("CONFLICT", result["status"])
        self.assertEqual(body, result["current"])
        with self.assertRaisesRegex(ValueError, "VALUE_INVALID"):
            self.service.upload(
                "preference.new.large", "A" * 8193,
                {"quote": "Synthetic preference.", "thread_id": "fixture-owner"},
            )
        with self.assertRaises(core.MemoryError) as oversized:
            self.seed_legacy_body("preference.legacy.oversized", body + "A")
        self.assertEqual("SCHEMA_INVALID", oversized.exception.code)
        self.assertEqual(original["commit"], core.git_head(self.service.store))

    def test_display_query_validation_is_preserved(self):
        for query, limit in ((None, 1), ("A" * 8193, 1), ("ui", 0), ("ui", 101), ("ui", True)):
            with self.subTest(query_type=type(query).__name__, limit=limit):
                with self.assertRaisesRegex(ValueError, "FACT_QUERY_INVALID"):
                    fact_keys.directory(self.service.runtime, self.project, query, limit)
        for key in (None, "A" * 8193):
            with self.assertRaisesRegex(ValueError, "FACT_QUERY_INVALID"):
                fact_keys.conflicts(self.service.runtime, self.project, key, "global", "preference")
