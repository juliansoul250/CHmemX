# Understanding-first Implementation Plan

> **For agentic workers:** Native continuation; use executing-plans, TDD and verification-before-completion. Parent is sole writer. Independent read-only design review completed.

**Goal:** Enforce caller understanding before public retrieval and execute bounded multi-facet plans without scope drift or answer-label leakage.

**Architecture:** A shared plan validator gates public endpoints before model loading. Existing ranking is separated from delivery so all facets share an immutable scope and merge pointers before reading bodies. Literal compatibility stays explicit and separately evaluated.

**Tech Stack:** Python 3.10+, unittest, existing optional local encoder; no new dependency.

**Spec:** [Understanding-first design](../specs/2026-11-17-understanding-first-design.md).

## Global Constraints

- Original query at most 8192 characters; plan version 1; at most 3 facets plus original query; at most 12 entities and 12 constraints.
- Existing result limit 1–20, associations 0–8 and scan limit min(100, max(20, limit*5)) stay aggregate, not per facet body budgets.
- Preserve source, Active, store, model, scope and approval checks. No live writes/deployment/commits.
- No expected answers, key-specific rewrites, domain keyword lists, new implicit models or automatic graph fact creation.

## Review Focus

1. Missing/clarify plans must not instantiate encoder; include start(query), public CLI and standalone v3 CLI.
2. Subqueries may not widen original scope, including unknown-context fallback.
3. Constraints/compound entities remain complete verbatim spans; reject mismatches, duplicate/oversized facets and unknown answer fields.
4. Merge before bodies; deduplicate reads and bound total scans across all facets, including stale candidate backfill.
5. Facet hits are candidate evidence only; no claim of validated understanding, full answers or graph inference.

## Tasks

- [x] Freeze phase-3 source baseline and new synthetic cases; write behavior tests and observe RED.
- [x] Add `src/chmemx/query_plan.py`: `validate(query, plan)`, early decision results and public schema. Missing plan is not automatic search.
- [x] Refactor `scripts/retrieval_v3.py`: pure pointer ranking, shared bounded delivery, `recall(..., query_plan=None)` literal compatibility and planned execution. Preserve per-query legacy ranking.
- [x] Gate service/MCP/CLI before `_reader()`; migrate existing transport tests to explicit plans without changing expected retrieval assertions. Reject unlisted MCP arguments to prevent compatibility bypass.
- [x] Keep graph/skill structured contracts; document legacy lexical routing and no automatic semantic interpreter. Remove unsupported understanding claims.
- [x] Run unchanged historical/synthetic regression plus frozen plan scenarios; record literal and planned results separately, preserve failures and conflicts.
- [x] Final evidence closure: full unit tests on Python 3.10/3.14, lint, build, installed MCP smoke, independent read-only review, source/live integrity verification and report.

## Observed results

293 tests pass on each Python version; wheel/sdist and external installed lifecycle smoke pass. Independent implementation review found a scan-exhaustion telemetry bug, reproduced RED and fixed without increasing scan budgets. Original 162+10 and 43 literal suites retain identical output. Frozen12 candidate-label pass count8->9 with fixture plans and independently generated question-only plans; 3 failures remain. This small synthetic experiment is not a human-annotated blind benchmark. Existing historical6 failed cases remain. See [full report](../../audits/2026-11-17-understanding-first.md).

No deployment: callers must migrate to query_plan before server upgrade. No implicit NLP service, canonical rule/graph write or answer-entailment claim was added.
