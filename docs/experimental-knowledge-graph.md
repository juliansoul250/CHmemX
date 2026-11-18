# Evidence-bound typed graph pilot

This is an opt-in, local experiment, not a new write policy, MCP tool, deployed reader,
or approved set of facts. Existing topic associations stay unchanged. No graph database,
LLM extraction, API, filesystem watcher, or model download is required.

## Small ontology

Entities are scoped `Project`, `Tool`, `Role`, `Rule`, `Evidence`, or `Concept`.
An entity has a stable scope-qualified ID, a name, optional aliases, and supporting
accepted-record IDs. Scope is `project_id`, or `null` for global; aliases do not merge
entities across projects. Ambiguous names require a qualified ID.

| Relation | Source | Target | Direction |
|---|---|---|---|
| `uses` | Project | Tool | directed |
| `depends_on` | Tool | Tool | directed |
| `responsible_for` | Role | Project | directed |
| `constrained_by` | Project or Tool | Rule | directed |
| `supported_by` | Rule | Evidence | directed |
| `related_to` | any supported type | any supported type | explicitly symmetric |

Relations bind an accepted record ID and its exact byte SHA from the frozen index.
Each also has an effective date and optional exclusive expiry date. Unknown fields,
relation types, endpoint types, duplicate IDs/edges, missing evidence, and cross-scope
edges are rejected. Related topic links are not converted into typed statements.

`uses` does not imply permission. `responsible_for` is not approval or writer assignment.
Paths are traceable candidate relationships, not new inferred facts: dependency traversal
does not manufacture a transitive edge, and weak links do not become causation.

## Authority and compatibility

The sidecar is a **candidate projection**. Binding evidence proves provenance, not that
the text entails a relation. All returned paths are labeled `candidate_projection` and
`execution_authorized=false`; the pilot cannot approve, write, supersede, or deploy memory.
Making relationships authoritative later requires source review and the normal Owner
confirmation route, not a `reviewed=true` flag or a model confidence score.

Only records from the existing frozen accepted index can support the candidate. The
existing Retriever still validates canonical bytes, membership, clean HEAD, and sources.
Scoped record IDs use its byte-verified `read` interface inside paired snapshot checks;
the pilot does not repeat semantic ranking for each evidence record. Source validity is
rechecked before return to catch changes during the bundle read. This is a bounded check,
not an atomic lock over externally owned source repositories.
Stale evidence cannot support a path, including evidence returned as a historical lesson.
If a constraint/exception record is unavailable, its associated path is blocked rather
than returning only the conclusion. Missing knowledge is not proof that no constraint exists.

The pilot is composed beside the current Retriever, not patched into MCP/service routing.
Calling `ExperimentalGraphRetriever.recall(query, cwd)` without `graph_query` delegates
unchanged to normal recall. Explicit structured graph intent selects the relationship
path; natural-language intent recognition is not claimed.

```python
reader = ExperimentalGraphRetriever(index, candidate_graph)
result = reader.recall(
    "Project Alpha tool requirements", cwd,
    graph_query={"entity": "Project Alpha", "relation": "uses"},
)
```

Incoming lookup must request `direction="incoming"`; it retains original edge direction.
Traversal defaults to one hop, allows at most two, has an edge-scan and result budget,
and checks the anchor evidence before expanding. Typed queries do not fall back to
unrelated nearest-neighbor results if the entity or relation is missing.

Records marked as conditions accompany the target as a complete evidence bundle, not
independently ranked fragments. These bundles remain historical data, never instructions.

## Evaluation and stop conditions

The public seams are graph validation and the composed `recall` result. Tests use disposable
Git stores and literal scenario expectations, not generated answers, real projects, or the
live memory store. Cover forward/reverse lookup, entity aliases and ambiguity, condition
closure, stale/source-less evidence, dates, pending records, cross-project names/edges,
cycles, unsupported relations, bounded traversal, tampering, and ordinary-recall parity.

This is synthetic regression and integration evidence, not a blind human-checked holdout
or proof of production semantic improvement. Production acceptance still needs a frozen
independent question set, zero forbidden/scope hits, citation/condition support, and measured
cold/warm latency and storage costs. Do not rewrite failed labels or disable source checks.

Rollback removes this optional module/sidecar only. No canonical data migration is part of
this experiment. Do not publish private fixtures or query text. The public README and version
remain about the current release until a separately authorized release is accepted.
