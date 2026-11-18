# Retrieval follow-up implementation plan

> **For agentic workers:** Use superpowers:executing-plans. Native continuation authorized by the Owner; no new delegation or deployment.

**Goal:** Fix reproducible remaining retrieval defects without relabeling historical tests or weakening source/approval boundaries.

**Architecture:** Keep the existing model and canonical store. Separate contradictory evaluation contracts from retrieval failures. Make body passages sentence-aware. Test semantic fallback alternatives against the full frozen corpus; retain RRF after the semantic-score-first experiment underperformed. Reject English topic-only lexical hits lacking meaningful query coverage.

**Tech Stack:** Existing Python 3.10+, unittest, locked local ONNX encoder; no new dependencies.

**Spec:** [Whole-system review](../../audits/2026-11-17-memory-system-review.md), followed by the Owner's request to perform the remaining repairs.

## Global constraints

- Live memory/index remain read-only; all real-data experiments use the existing private frozen copy with `source_checks=False` explicitly marked.
- Preserve exact labels, thresholds and initial dirty work; do not tune hints or encode expected answers.
- Preserve pre/post snapshot checks, record identity/hash/Active/source checks, scopes and bounded body reads.
- Contradictory association expectations for identical inputs cannot both pass. Detect/report them; do not silently pick a label.
- No automatic model download, Git commit, deployment or external source reads.

## Review focus

1. Reusing pre-change embeddings after passage rules change: bind chunking recipe in embedding input digest.
2. Exact keys and approved aliases: preserve precise retrieval and existing corpus behavior.
3. Negative queries with shared topic but absent answer: add English multi-content-word coverage regression; no claim of general answer entailment.
4. Associations requested as zero or non-default: forward and validate the actual limit; never trim after an unauthorized body read.
5. Same-query conflicting labels across suites: detect before model inference; keep per-suite original failures and evidence.

## Tasks

- [x] `tests/test_retrieval_followup.py`: RED before implementation; 13 retained tests. The proposed semantic-first policy test was withdrawn with that rejected feature, not converted into a new success label.
- [x] `scripts/vector_memory.py`: cross-suite label validation and explicit association limits; preserve four-argument callbacks when no limit is specified. Legacy retrieval returns no graph results.
- [x] `scripts/evaluate_retrieval.py`: repeated `--suite`, preflight, separate gates, frozen bytes and console summaries without nested case text.
- [x] `scripts/retrieval_v3.py`: sentence-aware token-overflow passages and recipe-bound reuse. Semantic-score-first fallback rejected; RRF retained.
- [x] Narrow meaningful English-term coverage for non-exact lexical trust. No general answer-sufficiency claim.
- [x] All 162 historical positives, 10 historical negatives and 43 synthetic cases compared; original labels and thresholds unchanged.
- [x] 278 tests pass on Python 3.10 and 3.14; Ruff, wheel/sdist and external installed MCP smoke pass. Report records 5 remaining real retrieval failures, 2 synthetic paraphrase failures and 1 conflicting-label case; overall release gate remains failed.

## Investigation already completed

- One historical association case demands a record that another suite forbids for exactly the same query/context/limits. This contradicts the earlier assumption that one graph bug could be fixed while preserving every positive label.
- Suite `association_limit` was present in frozen data but ignored by evaluation callbacks.
- One fixed sentence-passage experiment (627 versus 192 fragments, unchanged model/thresholds/ranking) improved the difficult historical group from Top1 4/10 and Hit@3 7/10 to 6/10 and 8/10; other suite headline metrics and all 10 noise abstentions unchanged. This is diagnostic reused-history evidence, not a new blind holdout.
- RRF can outweigh the highest semantic score, but higher cosine was not consistently more correct: semantic-score-first reached only Top1 5/10 and Hit@3 7/10 versus sentence-aware RRF 6/10 and 8/10. Preserve RRF rather than treating cosine order as ground truth.
