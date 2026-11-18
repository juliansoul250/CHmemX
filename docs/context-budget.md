# Bounded recall without another memory store

Development checkout only; these additions have not been released or deployed.

CHmemX stores reviewed long-term memory. Temporary logs, web pages and command output
are not accepted facts merely because they are useful during a task. Keep those artifacts
in the owning task's workspace, with explicit scope and retention; do not ingest them into
Active Memory, install global hooks, scan other sessions, or add an execution tool to CHmemX.

## Read less before returning less

The v3 retriever scores lexical pointers from the existing index before opening record
bodies. Only bounded primary/association candidates are read. Ranked metadata is not an
accepted record: each selected record still passes digest, Active membership, identity,
source-validity and paired clean-HEAD checks. Scoring weights and graph rules are unchanged.
The legacy full-record query interface remains available and validates its selected records.

## Optional body previews

The existing `recall` MCP tool accepts `body_limit` from 256 to 16384 characters:

```json
{"query":"editor preferences","limit":3,"body_limit":1024,"association_limit":0}
```

CLI equivalent:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/project \
  recall 'editor preferences' --limit 3 --body-limit 1024 --association-limit 0
```

Omit `body_limit` for the original full-body response. The opt-in preview changes delivery,
not ranking or source admission. Every returned record includes `body_preview` with its
full character count, returned character count, truncation flag and SHA-256 of the full
UTF-8 body. Scope, source, source badge, freshness and ranking fields remain available.
This is a body-character budget, **not** a hard response-byte or model-token limit.

`association_limit` exposes the existing retriever's graph budget (0–8, default 3).
Use 0 when the task needs primary hits only; omit it for the original one-hop associations.
This is explicit caller intent, not a new relevance threshold or a fix for a failed graph suite.

A truncated preview is a navigation aid, not complete evidence. Conditions, exceptions or
negations may be at the end. Repeat recall without `body_limit`, optionally using the exact
canonical key, then compare record ID and the full-body SHA-256 before relying on it.
Changed identity/content requires a new read. Approval/review output is never previewed;
the Owner must see the complete sealed batch and diff through the existing review workflow.

## What is deliberately not included

- No automatic raw-output capture, history scanning, additional SQLite service or global routing rules.
- No neural-model change, new embedding download, graph authority or relaxed source check.
- No automatic deployment, Active write, index publication, cleanup or source-repair task dispatch.

The useful pattern from context-mode is evidence on demand, not copying its implementation
or treating its output-size statistics as a bill. CHmemX's optional previews and pointer-first
ranking implement that pattern at existing interfaces without importing upstream code.

## Evaluation

Compare identical index and suite digests. Record body I/O, complete response bytes,
elapsed time, matching keys/scores, abstention, forbidden hits and graph associations.
Measure actual provider input/output/cache tokens separately if that measurement is available.
Generated fixtures prove mechanical regression, not unseen semantic accuracy or savings on a
subscription. Preserve failing labels; stale sources need source-owner review, not weaker gates.

The evaluation report includes `diagnostics` alongside the unchanged metrics and pass/fail
decision. Primary outcomes partition cases into a hit within the required rank, all expected
keys source-blocked, an unresolved scan-budget limit, a partial source block, or an unexplained
miss. These describe observed results, not proven root causes. A primary hit can still fail
because of a forbidden association or a scope violation.

`positive_association_coverage: ABSENT` means the suite has no expected-association cases;
it does not prove graph quality. Add independent positive and exclusion fixtures, but do not
rewrite frozen answers or count a safe source-blocking abstention as a retrieval hit.
