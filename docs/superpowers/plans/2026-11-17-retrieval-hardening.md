# Retrieval hardening implementation plan

Approved scope: Owner's project-wide audit request and subsequent approval to proceed in evaluation, lexical-trust, performance order. Baseline/spec: [audit](../../audits/2026-11-17-retrieval-baseline.md).

## Constraints

- Preserve pre-existing work. No live memory edits, index publication, deployment, deletion or approval-policy changes.
- Freeze source snapshots, suites and model before changes; compare identical inputs. Public reports contain synthetic examples and aggregate private-corpus metrics only.
- Real-corpus evaluation is ranking-only: external project authorities are outside this task's read scope. Never count that evaluation as current-source validation or a new blind holdout.
- Retain pre/post store verification, selected-record identity/hash/Active checks, scope boundaries, source freshness and bounded graph traversal.

## Changes and verification

1. `scripts/vector_memory.py`, `schemas/recall-evaluation-v1.schema.json`, `scripts/evaluate_retrieval.py`: explicit `expect_empty` cases, separate answerable/abstention denominators, all-relevant recall metric, v3 limit validation before inference, honest nearest-rank P95. Preserve existing positive-case gates. Add failing behavior tests first.
2. `scripts/retrieval_v3.py`: require actual lexical evidence before a hashed similarity can select the lexical channel or expand unknown-context scope. Reuse existing body/key term counts and canonical node/hint metadata; no body reads during candidate scoring and no new index format. Exact keys remain exact. Test collision rejection, valid body/node hits and semantic fallback.
3. `scripts/vector_memory.py`, `scripts/retrieval_v3.py`: separate pure pointer scoring from legacy freshness wrapper. Only the already-verified v3 caller uses pure scoring. Cache the immutable dense matrix, not source freshness. Test dirty/stale/mid-query changes, pointer parity and bounded reads. Defer service index-file caching until a safe invalidation design earns its complexity.
4. Full unit suites, Ruff, isolated wheel build/install and stdio smoke; repeat frozen synthetic and real ranking suites, scale/performance checks. Record remaining failures instead of changing labels or thresholds.

## Review focus

- Negative queries must not inflate positive retrieval metrics or hide association leaks.
- Hash evidence must not accidentally reject node aliases, spaced exact keys or legitimate body-only matches.
- Query scope must be resolved before relative thresholds, and collisions must not enlarge scope.
- Removing duplicate Git work must leave both v3 snapshot checks intact, including failures mid-query.
- Existing semantic fallback/graph limitations are design issues, not permission to broaden retrieval, edit live graph edges or claim complete quality.

Lifecycle audit found no evidence requiring an approval/queue/maintenance rewrite. Keep these mechanisms; prioritize measurable retrieval quality and explainability.
