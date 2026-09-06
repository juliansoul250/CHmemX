# Queue maintenance

[简体中文](zh-CN/maintenance.md) · [v0.5](v0.5.md)

Maintenance is an operator task, not an MCP tool. It never deletes canonical Active records,
approval history, source projects or external inboxes. No timer or startup hook runs these commands.

## Choose an operation

| Action | Eligible data | Default age |
|---|---|---|
| `archive` | Finished uploads, replaced review revisions, finished raw events | 30 days |
| `purge` | Compressed queue archives; minimal request receipts remain | 180 days |
| `reconcile` | Missing/unindexed hot uploads and their metadata | No age filter |
| `expire-receipts` | Payload-free terminal request receipts, not live uploads | At least 180 days |
| `nonces` | Replay entries past expiry plus a five-minute margin | Signature expiry |

Archive and purge ages can be overridden explicitly, including zero days. Receipt expiry cannot
go below 180 days. A plan selects at most 100 items per family by default; `--limit` accepts 1-500.
Use a fresh plan for the remainder. Unknown-age, unresolved and unmanaged data is preserved.

Approved or explicitly closed uploads leave the working queue when archived. Their compressed
history and request receipts remain queryable. This frees admission slots without copying the
entire memory Git repository.

## Preview and apply

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator maintenance-plan --action archive \
  --older-than-days 30 --limit 100 --output /absolute/archive-plan.json
```

Inspect `targets`, `target_files`, `blockers` and `preserve`. The plan reports each file's before/after
hash and size, estimated net space released, and temporary journal space needed. A negative space
estimate means metadata is added rather than space released. Inventory totals cover uploads,
candidates, batches, archives and receipts. The plan is valid for 24 hours.

After explicitly approving that exact plan:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator maintenance-apply \
  --plan /absolute/archive-plan.json --digest 'sha256:EXACT_REVIEWED_DIGEST'
```

Placeholders are not approval. Changed files, HEAD or plan require another preview. Reapplying a
completed plan returns its receipt instead of running twice. `purge` permanently removes listed
compressed queue payloads unless an independent backup exists. `expire-receipts` also forgets listed
request-ID bindings; those IDs may then be processed as new requests. Formal Git memory history stays.

## Close an unwanted upload

Pending and conflicting content is not discarded to make room. Review it or explicitly close it
with a reason. Obtain its `job_digest` using:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator status --upload-id UPLOAD_ID
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator close-upload UPLOAD_ID --decision cancel \
  --reason 'No longer needed.' --digest 'sha256:EXACT_UPLOAD_DIGEST'
```

`reject` is also available. Closing invalidates the current review but does not change Active memory.
Active corrections use the normal Owner-confirmed workflow. A changed upload cannot be closed with an old digest.

## Reconcile missing files

Use `maintenance-plan --action reconcile` when files and metadata disagree. It can index an existing
managed upload or recover a missing upload's committed result from Git. Without payload or commit
proof, it records `UPLOAD_DATA_MISSING`, retains the request fingerprint and releases the orphan
reservation. It never invents content or silently treats the retry as new. Restore missing input
from a verified backup, or explicitly close it if the operator chooses to discard that unresolved job.

Do not manually edit `state.json` or remove upload/receipt files to clean the queue. Legacy requests
without enough context cannot be safely rebound; use a new request ID or recover their input.

## Recover an interrupted operation

`status` reports `queue_health` and the transaction ID. While recovery is needed, uploads, reviews
and approvals stop; canonical recall remains available. Backups wait until the queue is consistent.

```bash
chmemx --store /absolute/private-memory --agent-id main-memory-curator \
  maintenance-recover --transaction-id TRANSACTION_ID --action rollback
```

`complete` resumes a fully prepared operation when bytes and HEAD still match. Incomplete preparation
must be rolled back if required staged bytes were never written. Unexpected target or staging changes
stop recovery without overwrite. Do not delete the recovery directory.
If data changes finished and only journal cleanup was interrupted, use `complete`; that state cannot
be rolled back by the interrupted-operation command. Purged payloads still require an independent backup.

Journals address process interruption, not disk destruction. Enable and test separate backups only
if your retention policy calls for them. This upgrade enables no automatic cleanup or backup.

For nonce maintenance, use `maintenance-plan --action nonces`, then
`maintenance-nonces --digest EXACT_NONCE_PLAN_DIGEST`. Archive-plan digests are not interchangeable.
