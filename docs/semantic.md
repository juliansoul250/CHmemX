# Optional local semantic retrieval

[Design and measurements](v0.3.md) · [中文评估](zh-CN/v0.3.md)

The default package remains lexical-only. Dense retrieval is optional and disabled unless an
operator explicitly provides a local `--model-dir`. No runtime API calls or model downloads occur.

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

The standard MCP service creates a simple project-scoped, key-parent topic directory.
Switching `--model-dir` takes effect without waiting for a new memory commit. Clients sharing
the simple service index should use the same model setting to avoid repeated rebuilds.
Advanced deployments can build
their reviewed taxonomy with the standalone builder and query that candidate using
`python -m chmemx.scripts.retrieval_v3 recall --index ... --query ... --cwd ...`.
Do not silently replace an existing custom taxonomy with the simple service directory.

Use `build --previous /absolute/private/previous-v3.json` to reuse unchanged embeddings.
Reuse requires matching store, body and model-lock digest. Global IDF and metadata are still
rebuilt because one new record changes corpus statistics. There is no filesystem watcher or
background training. Display-only `contexts.node_views` never modifies ranking features or
canonical records; changes to ranking hints require another evaluation.

The index contains hashed sparse terms, vectors, IDs and source paths, not memory bodies.
It is **not anonymized**: metadata and vectors can disclose information. Keep it private.
Every recall checks a clean tracked store, exact HEAD, bound catalog/node/Active-index files,
record byte hashes, identity and current accepted+active membership. Graph expansion is bounded
to one hop in the primary record's project. With a registered context, foreign projects need
an explicit project/key reference. Without a known context, high-confidence lexical foreign
matches can also be returned, always labeled; this fallback is not project access control.

v0.4 also checks the bound source file. Stale/unverifiable project facts are separated into
`needs_review`; historical lessons are labeled. Frozen ranking evaluations must explicitly
distinguish that snapshot-only comparison from current-source eligibility. See [v0.4](v0.4.md).

## Failure and rollback

Dirty or changed canonical files fail closed. Missing/changed model files fail closed; the reader
does not silently replace them or fetch a new model. Keep the previous working reader/index until
the candidate passes your gates. A source commit makes an old index stale; rebuild before use.
Rolling back the tool/index does not revert memory facts. Memory corrections use the Owner-confirmed
workflow; no filesystem cleanup or automatic Git history rewrite is part of retrieval optimization.
