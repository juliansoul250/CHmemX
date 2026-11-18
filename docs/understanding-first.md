# Understanding before retrieval / 先理解，再检索

**Unreleased behavior change; not deployed to existing stores or adapters.**

CHmemX's embedding encoder is not an instruction model. The calling Agent must interpret the request before invoking normal MCP/CLI retrieval. CHmemX validates and executes that plan; it does not claim to have verified the interpretation or the answer.

## Contract

1. Resolve the goal, referents, compound entities, time/scope, negation and exceptions from the conversation. Do not infer authority from retrieved memory.
2. Keep simple requests whole. Split genuinely distinct information needs into at most three contextualized facets. Never substitute a bag of isolated words for a compound concept.
3. Supply `query_plan` to MCP `recall`, or to `start` when using its optional `query`. CLI uses `recall QUERY --query-plan /path/plan.json`.
4. Missing plans return `NEEDS_UNDERSTANDING`; `clarify` and `abstain` return `NEEDS_CLARIFICATION` and `ABSTAIN`. These paths perform no retrieval or encoder loading. A clarification decision is not proof that the memory store lacks an answer.
5. Assess returned evidence against the requested conditions before answering. A relevant candidate is not necessarily an answer, and a fragment match does not prove a conjunction across records.

Example plan (the outer tool's `query` must equal this `query` exactly):

```json
{
  "version": 1,
  "query": "Who inspects the fume hood and who approves restart?",
  "decision": "search",
  "goal": "Identify the inspector and the restart approver for the fume hood.",
  "entities": ["fume hood"],
  "constraints": [],
  "facets": [
    {"id": "inspection", "query": "Who inspects the fume hood?"},
    {"id": "restart", "query": "Who approves restart of the fume hood?"}
  ]
}
```

All seven top-level fields are required; unknown fields are rejected. No expected answers, record IDs, provider configuration or scope override fields are accepted. Entity/constraint arrays contain at most 12 unique, verbatim original-query spans each; every facet must retain all those spans verbatim. This intentionally conservative rule can repeat a global qualification. It is a mechanical omission check, **not semantic understanding**. The caller can still omit an important constraint from the plan, misread a negation, or submit a poor interpretation. CHmemX cannot prove otherwise.

Query/goal/facet text is bounded to 8192 characters; spans to 512; facet IDs are unique lowercase identifiers, with `original` reserved. Non-search plans have no facets. CLI JSON files are bounded to 64 KiB and duplicate fields are rejected. Existing entry, association and body-preview limits still apply.

## Execution and evidence boundaries

- Search the original question as well as the supplied facets against one verified index snapshot.
- Determine eligible projects from the original question/current context once. Facets cannot widen that set; planned unknown-context searches do not use the historical cross-project lexical fallback. This is a routing boundary, not an OS access-control boundary.
- Merge/deduplicate candidate pointers by bounded interleaving before loading bodies. Facets share the request's existing primary/association budgets; they do not each get a complete recall budget.
- Preserve before/after store checks, Active/hash binding, source freshness, stale-fact separation, body-preview provenance and bounded one-hop associations.
- If a planned query exceeds the encoder token window, skip dense encoding for that query, retain full-query lexical scoring and expose `dense_window_exceeded`. Do not silently drop late conditions.
- `understanding.facets` reports candidate counts and returned candidate IDs. `candidate_union_truncated` reports pointer-budget loss. Neither field certifies that every condition was answered.
- `interpretation_verified` is `false`; `answer_support` is `not_assessed`. Per-query scores are ranking values, not confidence. Whole canonical bodies remain authoritative; vectors are derived routing data.

Typed graph queries and skill selection already require explicit structured caller intent. They remain separate APIs. This change does not implement automatic entity resolution, typed graph joins, an entailment verifier, or a new model service. It does not change canonical memory rules, approval policy or accepted graph relations.

## Migration and compatibility

Normal MCP `recall` and optional `start(query=...)`, public `chmemx recall`, and standalone v3 recall now require a plan before searching. `start` without a query remains a context/status call. Client adapters must expose and forward `query_plan`; upgrading the server alone is insufficient. Existing Pi wrappers with the older schema are not silently upgraded by a source-tree edit. Do not deploy before clients migrate.

Low-level `Service.recall`, `Retriever.recall`, historical evaluation and legacy lexical tools retain an explicit **literal compatibility** path. Its responses say `understanding.status=literal_compatibility`; it is not the understood production workflow. No user-facing compatibility switch bypasses the MCP/CLI gate. Old bare-query examples in historical release notes describe released behavior, not this working-tree contract.

## Regression versus generalization

Historical frozen suites remain unchanged and test literal compatibility. They do not establish planned-workflow quality. New frozen synthetic cases exercise multi-intent retrieval, compound terms, Chinese/English requests, negation and no-answer behavior. Test-authored plans establish execution behavior only. An independent Agent may interpret **questions only**, without records or answer labels, to test that boundary separately; this still is not an independently human-annotated blind evaluation.

Report missing conditions, false positives, old-case regressions, body reads and latency, not only Top-1. Never feed expected answers into the interpreter, rewrite labels to make scores pass, or add query-specific keyword exceptions after inspecting misses.

## 中文摘要

先由调用 Agent 理解目标、对象、关系及限定，再提交计划；不是先向量化再猜意图。简单问题不拆，多个真实意图才拆；复合实体、否定、时间和例外不能丢。服务只验证计划约束、合并候选并读取获批证据，不宣称理解正确或答案完整。旧字面回测与新计划回测分别报告；没有新盲测，不能宣称泛化提升。
