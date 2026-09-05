# Backlog

[简体中文](docs/zh-CN/backlog.md)

Owner-set order, 2026-09-06:

1. MCP server
2. Tiered confirmation
3. Signatures, behavioral signals, and recall isolation
4. Hybrid retrieval
5. Incremental indexing

This orders the remaining work. Keep the foundations shipped in v0.3.1; do not redo them or
present a partial implementation as a completed milestone. Close the earliest unfinished
acceptance work before expanding a later area. Baseline measurements accompany every stage.

## 1. MCP server

- [x] Ship `start`, `recall`, `upload`, installable CLI, normal-wheel packaging, and an official-SDK integration test.
- [ ] Verify real Codex, ZCode and Claude Code clients separately; record exact versions, setup, responses and remaining blockers.
- [ ] Exercise startup failures, reconnects and bounded responses with the same minimal public interface.

Done when: a clean install can complete context loading, sourced upload and scoped recall in each
claimed client, with truthful Pending/Active states and no dependency on another tool's private files.

## 2. Tiered confirmation

- [x] Keep team approval as default; add explicit limited personal mode, duplicate no-op, conflict review and policy receipts.
- [ ] Improve policy preview, pending/retry receipt consistency and concurrent/idempotent workflows.
- [ ] Measure approval interactions and machine processing separately from human waiting time.

Done when: policy-authorized additions are straightforward, duplicates do not write again, and
conflicts/sensitive changes/sampled items still reach review. Existing team stores never silently
inherit personal mode. A policy change requires explicit Owner authorization.

## 3. Signatures, behavioral signals, and recall isolation

- [x] Add optional pinned Ed25519 sources, replay rejection, conflict-count review escalation, untrusted-data envelopes and targeted source deactivation.
- [ ] Complete source-key rotation/revocation lifecycle and attributable audit records.
- [ ] Add bounded per-source/key behavioral windows and review reasons; investigate anomalous conflict or revocation patterns.
- [ ] Validate consumer behavior across clients: recalled data cannot authorize tool use, change permissions or become higher-priority instructions.

Done when: forged/replayed/revoked inputs fail as specified, suspicious activity raises review,
quarantine never reaches recall, and source deactivation preserves independent later corrections.
Signatures prove provenance, not benevolence; an envelope is not a model sandbox; reputation must
not grant privileges. This milestone remains partial until the consumer tests are real.

## 4. Hybrid retrieval

- [x] Add optional pinned local ONNX semantics, lexical-first fallback fusion and one-hop topic associations.
- [ ] Improve confident-but-wrong lexical matches using independently frozen evaluations, not query-specific hints.
- [ ] Extend paraphrase, abstention, scope and recall-distribution comparisons without reusing tuned cases as blind evidence.

Done when: a candidate passes fixed regression and scope/negative tests, improves independent
holdout results, and stays within measured latency/memory budgets. Preserve the previous index
until publication gates pass. An aggregate gain is not a claim that every query is correct.

## 5. Incremental indexing

- [x] Reuse unchanged embeddings by body/model digest; keep corpus IDF recalculation explicit.
- [ ] Add changed-record/node/cell updates with generation consistency, atomic publication and recovery tests.
- [ ] Demonstrate an end-to-end write-to-recall improvement against a full rebuild, not only fewer embedding calls.
- [ ] Consider an opt-in filesystem watcher only after incremental correctness and a real operational need are established.

Done when: incremental and full rebuilds are equivalent for the tested changes; interrupted and
concurrent updates cannot expose a mixed generation. Embedding reuse alone is not full incremental indexing.

## Scope

This backlog does not enable features, change a live approval policy, publish a memory index,
or authorize uploads of private data. Existing release behavior and limits remain in
[v0.3 evidence](docs/v0.3.md), [MCP usage](docs/mcp.md), and [retrieval evaluation](docs/semantic.md).
