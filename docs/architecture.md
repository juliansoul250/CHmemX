# Architecture

[简体中文](zh-CN/architecture.md) | [Historical interactive diagram](architecture.html)

## Canonical memory and governance

The Git store is the canonical memory project. Global preferences and registered projects have
separate Active indexes and node maps. Records retain their body, key, scope, source, lifecycle
and approval provenance. Approval receipts bind writes to a sealed batch digest and parent Git
HEAD. Pending uploads are not retrieval evidence. Conflicts are reviewed, not resolved by vector
similarity or whichever record is newest.

The derived v3 index holds routing metadata, sparse lexical features, body/key term counts,
optional locked local semantic chunk vectors and canonical record paths. It does not copy raw
record bodies, but is still private data, not an anonymized export. Index and selected body reads
are bound to store HEAD, hashes, Active membership and source freshness. Before/after verification
fails closed if the store changes. Stale facts are separated; historical lessons remain labeled.

## Understanding and retrieval

**Unreleased working-tree contract:** the calling Agent interprets a natural-language request
before normal MCP/CLI retrieval. CHmemX validates a bounded plan; it has no automatic instruction
model and does not certify that interpretation. Missing plans do not search. See
[understanding-first contract, limits and migration](understanding-first.md).

1. Preserve the original query and its project scope. Optional contextualized facets must retain
   the caller's explicit entity/constraint spans and cannot broaden project selection.
2. Rank metadata pointers using sparse record/node/cell features and BM25. An optional verified
   multilingual encoder scores sentence/token-overflow chunks. Strong lexical evidence retains
   priority; reciprocal-rank fusion handles semantic fallback. This is not a general answer verifier.
3. Merge facet candidate pointers before loading bodies. Share request budgets rather than running
   independent complete recalls. Original queries remain represented.
4. Read selected approved bodies, check freshness, and return bounded same-project one-hop topic
   associations. Caller then checks whether the evidence actually answers every requested condition.

The dependency-free sparse vectorizer hashes normalized words and Chinese 2/3-character fragments;
it is lexical, not neural understanding. Semantic inference is optional and local, with locked model
files and no implicit download. Legacy lexical tools and low-level literal Python recall remain
compatibility/diagnostic paths, not the understood public workflow.

## What the graph means

Records are linked through reviewed topic nodes and bounded related-node edges: a network of
memory, not a flat vector list. These navigation links do not assert arbitrary causal, temporal or
logical relationships. The [typed knowledge-graph pilot](experimental-knowledge-graph.md) separately
supports scoped, evidence-bound entities and relations; it is not automatically used by ordinary
recall and has not promoted candidate relations into canonical facts.

## Evaluation and publication

Generated record/node coverage is structural regression, not blind semantic evaluation. Historical
frozen suites retain their original gates and labels; contradictory labels are reported before
combined evaluation. Measure misses, abstention, forbidden hits, scope, associations, relevant-set
coverage and latency as well as Top-1/Hit@3. Planned workflows and interpreter quality need separate
evidence from literal regression. Never use expected answers to construct an interpretation.

The legacy bounded optimizer evaluates lexical profiles only. It does not tune or publish the v3
understanding pipeline. Code changes, isolated backtests, live index publication, and canonical
memory approval are distinct actions. See [semantic retrieval and evaluation](semantic.md).
