# Understanding-first retrieval

Owner direction: understand every natural-language retrieval request before choosing searches; avoid fitting rules to known failures. Owner requested implementation and another backtest. Existing native execution and no-deployment restrictions remain.

## Decisions

The calling Agent supplies its interpretation. CHmemX has an embedding encoder, not an instruction model; it must not pretend tokenization, a stopword list, or validating JSON constitutes understanding. No new provider, credential access, model download, or hidden cloud request.

Normal MCP and public CLI require a bounded `query_plan`. Missing plans return `NEEDS_UNDERSTANDING`; clarify/abstain plans return without reader/model loading. Low-level Python and historical evaluators retain a clearly labeled literal compatibility path. This is an intentional public-tool behavior change, not a deployed migration.

Plan v1 binds the exact original query, decision, interpreted goal, explicit entity/constraint spans and zero-to-three contextualized facets. Entity and constraint strings must occur in the original query and remain verbatim in every facet. These checks prevent certain omissions/inventions; they do not establish interpretation truth. Unknown fields, expected answers, record IDs, model/path selection and query-scope overrides are rejected.

For search: preserve original query; calculate scope once from it; rank each facet against the same index with no scope expansion; merge/deduplicate pointers with bounded interleaving; only then read bounded approved bodies and expand existing bounded associations. Report per-facet candidate provenance, missing candidates and truncation. `answer_support` remains `not_assessed`: hits are not proof of conjunction, entailment or graph joins. A facet cannot authorize another project. Dirty/changed snapshots and stale facts retain existing behavior.

The typed graph pilot and skill discovery already consume explicit caller-resolved intent. Keep those separate rather than invent natural-language parsing or promoting experimental relations to facts. Legacy standalone lexical tools are documented compatibility/diagnostic tools, not the normal understood workflow.

## Alternatives rejected

- Handwritten compound-word/price/topic heuristics as an understanding engine: brittle and contrary to Owner intent.
- Automatic local/cloud LLM installation: not currently configured or authorized; unnecessary when the calling Agent already interprets requests.
- Optional plan with silent user-facing literal fallback: falsely implies the new contract is enforced.
- Running full recall once per facet: multiplies body reads, permits scope drift and mixes evidence work before merging.

## Evaluation

Freeze new synthetic multi-facet/compound/negation/ambiguity/scope tests before implementation. Structured test plans are fixtures, NOT proof of an independent language interpreter. Keep historical 162+10 and synthetic43 suites unchanged as literal regression baselines; planned execution is a separately labeled experiment. Never inject expected keys/answers into understanding inputs. Measure scope leaks, retained constraints, facet candidate coverage (not answer completeness), body reads, latency and regressions. No claim of independent blind semantic quality without an independently annotated interpreter evaluation.

No Active memory, live index, canonical rules, or graph relations are changed by this implementation.
