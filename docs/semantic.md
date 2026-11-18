# Optional local semantic retrieval

[Design and measurements](v0.3.md) · [中文评估](zh-CN/v0.3.md)

The default package remains lexical-only. Dense retrieval is optional and disabled unless an
operator explicitly provides a local `--model-dir`. No runtime API calls or model downloads occur.

For the unreleased pointer-first read optimization and opt-in MCP/CLI body previews, see
[bounded recall](context-budget.md). They change I/O and delivery, not ranking or source gates.

A separate **unreleased, client-breaking** change introduces [understanding-first query plans](understanding-first.md)
for normal MCP/CLI recall. The caller interprets the request; the encoder does not. Missing plans
return `NEEDS_UNDERSTANDING`. Historical evaluations below retain the labeled literal compatibility
path and do not establish planned-workflow quality. No existing adapter or store has been migrated.

## Model contract

The tested model is [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2),
revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, Apache-2.0, 384 dimensions, 128-token windows,
attention-mask mean pooling. The tested ARM64 quantized ONNX file plus tokenizer totals about
122 MiB. Other hardware/model combinations need separate evaluation; this is not a universal model loader.

From a checked-out release, install the optional dependencies into your own environment:

```bash
python -m pip install '.[semantic]'
```

Download these exact files yourself into a **private directory outside the tool repository**:

- [ONNX model](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/onnx/model_qint8_arm64.onnx), named `model.onnx`.
- [Tokenizer](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/tokenizer.json), named `tokenizer.json`.
- Copy the checked-in [model lock](../examples/model-lock.minilm.json) there as `model-lock.json`.

The loader verifies the lock's required files, hashes and pooling/dimension contract before use.
The lock itself is operator-controlled, not a signed vendor attestation. Keep the model's license
and origin with any redistributed model files. CHmemX does not redistribute the model.

## Build and evaluate a candidate

```bash
python -m chmemx.scripts.retrieval_v3 build \
  --store /absolute/private-memory --taxonomy /absolute/private/content-directory.json \
  --model-dir /absolute/private/models/minilm --output /absolute/private/candidate-v3.json

python -m chmemx.scripts.evaluate_retrieval \
  --index /absolute/private/candidate-v3.json --suite /absolute/private/frozen-regression.json \
  --output /absolute/private/evaluation-v3.json
```

Use the existing [suite format](../examples/recall-evaluation.example.json). Empty suites are not
evidence. Reports bind candidate bytes, frozen suite bytes, Git HEAD, per-case results and latency.
They do not publish an index or adjust labels. The legacy `vector_memory.py optimize` still tunes
lexical profiles only; do not feed a v3 index to that legacy command.

An explicit no-answer case uses `"expect_empty": true, "expected_keys": []`.
It passes only when both primary entries and associations are empty; `needs_review`
is diagnostic, not an answer. Positive cases still require nonempty expectations.
`top1`, `hit_at_3`, MRR, precision and recall use only positive cases; they are `null`
for a negative-only suite. `abstention_rate` uses only no-answer cases and is `null`
when none were tested. A positive `min_abstention_rate` requires negative coverage.
`mean_recall_at_k` measures the fraction of labeled `relevant_keys` returned, unlike
Hit@3's any-expected-key hit. Relevant alternatives are not necessarily mandatory facts.
`min_recall_at_k` can gate that coverage; existing per-case gates are not relaxed.

The legacy suite format allows `top_k` up to 64; the v3 evaluator rejects values
above 20 before constructing a model, rather than silently clipping them.
Latency P95 uses nearest rank; `cold` means reader construction, not first-query latency.
For authorized, isolated snapshots only, `--ranking-only` skips external source
checks and marks the report as **not current-use evidence**. Default evaluation
keeps source checks enabled. Store/record/Active verification remains enabled in both modes.

Graph results are evaluated separately from primary hits. A case may specify
`expected_association_keys` and `forbidden_association_keys`; `forbidden_keys` and
`forbid_cross_project` also apply to associations. Missing required associations or
forbidden/scope hits fail the case even when the primary answer is correct. An
association hit rate is `null` when no association expectations were tested, not
100%; a positive `min_association_hit_rate` requires such cases.

`source_blocked_expected_keys` explains expectations found in `needs_review` with
`current_use_allowed=false`, without copying their bodies into the report. These
remain misses in strict metrics. Absence from this bounded list does not prove a
source is fresh: review the source evidence and `scan_budget_exhausted` separately.
Do not compare scores from different suite digests as a before/after improvement.

Before selecting the candidate, compare it with the previous index on:

- fixed regression and graph-association cases;
- a human-checked holdout frozen before tuning, including paraphrases;
- unrelated-query abstention, project boundaries and invalid/Pending exclusion;
- body/index tampering, stale HEAD and Active membership changes;
- cold/warm latency and embedding reuse after a normal accepted change.

Treat generated coverage as structural regression. Do not generate questions from the answers,
tune against them, then call their success independent proof. Do not lower a failed gate or turn a
miss into a new “correct” label. New confirmed facts may justify new future tests, not rewriting
the holdout already used for this release. Keep reports and query text private.

The bounded local trial gates were: 136/136 existing cases, all association/noise checks, zero
forbidden/scope hits, holdout Hit@3 at least 70% and 20 percentage points above baseline, and warm
P95 below 250 ms. Passing one small corpus does not establish those thresholds for another corpus.

## Run and rebuild

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id source-one --model-dir /absolute/private/models/minilm serve
```

The standard MCP service groups records by project and canonical-key parent. On rebuild,
a generated global cell matches only global records, even when a project uses the same key.
In custom taxonomies `"scope": "global"` makes this restriction explicit; with neither that
scope nor a project filter, a cell may span projects as before. Membership guides ranking,
not access control. Cell vectors tokenize member keys in canonical-key order, so reimporting
identical records with different opaque IDs does not change cell features or corpus IDF.
Existing index files keep their original memberships and vectors until rebuilt.
Switching `--model-dir` takes effect without waiting for a new memory commit. Clients sharing
the simple service index should use the same model setting to avoid repeated rebuilds.
Advanced deployments can build
their reviewed taxonomy with the standalone builder and query that candidate using
`python -m chmemx.scripts.retrieval_v3 recall --index ... --query ... --cwd ...`.
Do not silently replace an existing custom taxonomy with the simple service directory.

Use `build --previous /absolute/private/previous-v3.json` to reuse unchanged embeddings.
Reuse requires matching store, body, model-lock and chunking-recipe digest. Global IDF and metadata are still
rebuilt because one new record changes corpus statistics. There is no filesystem watcher or
background training. Display-only `contexts.node_views` never modifies ranking features or
canonical records; changes to ranking hints require another evaluation.

The index contains hashed sparse terms, vectors, IDs and source paths, not memory bodies.
It is **not anonymized**: metadata and vectors can disclose information. Keep it private.
Every recall checks a clean tracked store, exact HEAD, bound catalog/node/Active-index files,
record byte hashes, identity and current accepted+active membership. Graph expansion is bounded
to one hop in the primary record's project. With a registered context, foreign projects need
an explicit project/key reference. Without a known context, a qualified global lexical match
suppresses unrequested-project fallback. An explicit project route (name, key or curated hint)
also prevents fallback into other projects, including when the requested project has no match.
Otherwise, high-confidence lexical project references remain available and labeled. The existing
lexical thresholds are unchanged; this is scope selection, not project access control.

Unreleased hardening additionally requires real body-term or canonical navigation/key
support before trusting a hashed sparse score or expanding unknown-context scope.
The existing BM25 term digests distinguish body terms from key namespace components;
node aliases and exact/spaced keys remain supported. This rejects bucket-only matches,
not every topically related but unanswered question. No new index format is required.
Pure pointer scoring removes a redundant legacy HEAD subprocess only inside the
pre/post-verified v3 path. The dense matrix is reused per reader; source freshness is
not cached across requests. Service index-file caching remains unchanged.

The follow-up candidate additionally checks meaningful English query-term coverage before
trusting non-exact lexical matches. Sharing only an editor topic does not establish that a
memory about its theme answers a question about subscription pricing. This is a narrow lexical
filter, **not** general answer entailment or a multilingual intent classifier.

New builds split approved bodies at sentence/paragraph boundaries, then apply the existing
128-token overflow to each segment. Whole approved bodies, not isolated fragments, are returned;
conditions and exceptions must still be read together. `hybrid.chunking_recipe` identifies
`sentence-token-overflow-v1`; recipe changes invalidate embedding reuse. Existing indices remain
readable and are not silently rewritten. Test a separately rebuilt candidate before publishing.
The model, semantic thresholds, precise lexical priority and RRF fallback are unchanged. A
semantic-score-first experiment was rejected after comparison; it is not shipped.

Evaluation cases accept `association_limit` (integer 0–8, default 3), forwarded before expansion.
Custom evaluation rankers receive that keyword only when a case explicitly supplies it; old
four-argument callbacks remain compatible for cases without it. Legacy lexical retrieval still
has no graph expansion. Repeat `--suite` to check multiple frozen suites together:

```bash
python -m chmemx.scripts.evaluate_retrieval --index /private/index.json \\
  --suite /private/first.json --suite /private/second.json --output /private/report.json
```

Conflicting labels for identical query/context/budgets fail before model inference with
`EVALUATION_LABEL_CONFLICT`. Each compatible suite retains its own thresholds and pass/fail;
there is no merged average that can conceal a failed suite. Preflight, hashes and evaluation
use the same frozen suite bytes. Explicit contradictions are detected, but this does not
establish semantic correctness of labels or resolve their disagreements automatically.

v0.4 also checks the bound source file. Stale/unverifiable project facts are separated into
`needs_review`; historical lessons are labeled. Frozen ranking evaluations must explicitly
distinguish that snapshot-only comparison from current-source eligibility. See [v0.4](v0.4.md).

## Failure and rollback

Dirty or changed canonical files fail closed. Missing/changed model files fail closed; the reader
does not silently replace them or fetch a new model. Keep the previous working reader/index until
the candidate passes your gates. A source commit makes an old index stale; rebuild before use.
Rolling back the tool/index does not revert memory facts. Memory corrections use the Owner-confirmed
workflow; no filesystem cleanup or automatic Git history rewrite is part of retrieval optimization.
