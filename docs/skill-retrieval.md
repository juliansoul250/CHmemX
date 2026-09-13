# Optional constrained Skill retrieval

This module recommends entries from an explicitly supplied, reviewed capability catalog.
It does not modify memory `recall`, expose another MCP tool, install skills, read agent
histories, or replace a host's native skill-selection mechanism.

## Intent before similarity

The calling agent first interprets the request, context, positive goals, prohibited actions
and project/stage boundaries. Pass that interpretation as structured input; the module
does **not** claim to parse unrestricted natural language or resolve negation itself.
Do not create tags by deleting words such as “not”, “不要”, or “UI”.

- “Review UI, do not generate it”: request `ui-review`, allow `read`, forbid `generate-image`.
- “Edit only a button, do not regenerate the screen”: allow `edit-local`, forbid `generate-full`.
- “Stock report, no UI”: request the stock-reading capability, not UI capabilities.
- Double negatives, quotations and conflicting instructions need contextual interpretation.
  Return `decision: clarify` if the caller cannot resolve them; `decision: abstain` when
  no specialized skill is needed. A bare “continue” needs the current task context.

The library then filters capability, **all declared actions**, project and stage before
encoding descriptions. After ranking it rechecks the same immutable metadata. No eligible
candidate, zero/negative similarity, unresolved intent and contradictory action sets do
not become an automatic fallback to another skill.

## Run without a memory store

From a source checkout with CHmemX installed:

```bash
python -m chmemx.skill_retrieval \
  --catalog examples/skill-catalog.json --intent examples/skill-intent.json
```

The synthetic example returns `ui-review`. Image generation is excluded by action and the
other project's review by project scope. The default uses local lexical cosine similarity
and requires no optional dependencies. Add `--model-dir /absolute/verified-model` to use
the existing hash-verified local ONNX encoder with the `semantic` extra installed. No model
is downloaded. Rejected candidates never reach that encoder; an empty eligible set does
not load the model. The current ONNX encoder truncates each input at its model token limit;
this can affect ranking, not the separately checked metadata constraints.

Python callers can use `chmemx.skill_retrieval.recommend(catalog, intent, limit=5, encoder=None)`.
An explicit custom encoder must implement `encode(list[str]) -> list[list[number]]` and
return equal, nonempty, finite vectors. Only use an encoder whose data handling is approved.

## Input contract

- Catalog: at most 1000 entries; each has a unique `id`, `description`, nonempty
  `capabilities` and nonempty `actions`. Optional `projects` and `stages` are nonempty
  allowlists. Omitted restrictions mean unrestricted **within this catalog**, not authority
  to read or act in every project. Missing caller context cannot match a restricted entry.
- Search intent: `decision: search`, a nonempty positive `query`, `capabilities`,
  `allowed_actions` and `forbidden_actions` (the last may be empty). Optional `project` and
  `stage` supply context. At least one requested capability must match. These are shared
  exact tags, normalized with Unicode NFKC and casefold, not inferred synonyms.
- Every candidate action must be explicitly allowed, and none may be forbidden. A combined
  read-and-write skill is rejected for read-only work. Split genuinely separable modes into
  separately reviewed catalog entries; never silently strip a skill's unwanted actions.
- Unknown fields, duplicate tags/IDs/JSON fields, nonfinite vectors and oversized input
  are rejected. Tags are limited to 32 per list and 64 characters each; descriptions to
  4096 characters; query to 2048 characters; files to 8 MiB. Results are limited to 1–20.

## Output and limits

Statuses: `CANDIDATES`, `NO_MATCH`, `ABSTAIN`, `NEEDS_CLARIFICATION`, or the common CLI
`ERROR` envelope. Results include IDs, scores, declared actions, per-candidate constraint
checks, and exclusion reasons. They omit instruction bodies and always report
`execution_authorized: false`.

Scores are relative recommendations, not calibrated confidence or permission. A wrong
intent interpretation or misleading catalog metadata can still produce a wrong suggestion.
The caller owns interpretation, catalog review and actual action-time authorization.
Neither tags nor a passing constraint check are a sandbox or prompt-injection defense.

The regression tests use synthetic catalogs and structured interpretations, including
negative controls that check which descriptions reach an encoder. They do not establish
free-form intent-parser accuracy or native Codex routing accuracy. Test those separately
before connecting an agent workflow. Do not apply action exclusions to memory retrieval:
searching for a “do not generate UI” preference should still recall that preference.
