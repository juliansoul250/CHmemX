# Changelog

## 0.5.4

- Select scope before admitting unrequested project matches: with no known context, qualified global lexical matches retain their scope.
- Preserve explicit project/key/hint routes and labeled project fallback when no scoped lexical match exists; an empty explicitly requested project does not fall through to another project.
- Keep source validity, approval policy, ranking thresholds and index format unchanged. Cover the behavior with synthetic stores and real stdio MCP calls.

## 0.5.3

- Check exact fact identities and aliases against all validated definitions, independently of display pagination.
- Permit short corrections to valid legacy long records without relaxing new-upload limits or Owner review, preserving superseded history.
- Verify project-scoped decision/evidence corrections and large conflict responses through the standard MCP SDK, including reconnects.

## 0.5.2 (2026-09-07)

- Roll back failed project/source registration without retaining staged changes or deleting unrelated files.
- Reject foreign-project review before changing the upload or its original sealed batch.
- Recognize Git-declared text line endings without executing content filters or hiding real source changes.
- Recover missing-upload lifecycle proof from Git ancestry, retaining exact upload binding and absent-body status.
- Clarify Team/Personal write policies and legacy lexical versus current MCP/v3 retrieval in both READMEs.
- Preserve existing policy, index formats, ranking, maintenance locks and client permissions.

See [upgrade notes](docs/v0.5.2.md) or [中文说明](docs/zh-CN/v0.5.2.md).

## 0.5.1 (2026-09-06)

- Finish completed maintenance cleanup after a legitimate Git HEAD change, retaining byte checks.
- Preserve bound lifecycle receipts when reading immutable archives, and validate them before purge.
- Recover reverted approval proof from current Git ancestry even when upload writeback failed.
- Take one full inventory per plan/application and share historical lookup across Pending batches.
- Report missing recovery transactions and interrupted event accounting explicitly.
- Share precise canonical rollback between approval and source revocation, preserving unrelated edits.
- Keep TEAM confirmation, retention policy, source-agent MCP tools and retrieval settings unchanged.

See [upgrade notes](docs/v0.5.1.md) or [中文说明](docs/zh-CN/v0.5.1.md).

## 0.3.1 — 2026-09-06

- Fix a non-editable-install failure exposed by Linux CI: the source checkout could shadow
  the installed package and hide its runtime subpackage.
- Move the public API to a conventional `src/chmemx` layout; keep legacy script paths.
- Retain normal wheel installation in CI so this integration path remains covered.
- Apply an explicit semantic/lexical backend switch even when memory HEAD has not changed.
- Do not move or replace the already-published v0.3.0 tag.

## 0.3.0 — 2026-09-06

- Add installable package and three-tool stdio MCP interface, independently tested with the official MCP client.
- Keep team approval as default; add explicit limited personal policy, duplicate no-op, conflict diff and policy receipts.
- Add committed-byte/Active-membership v3 reader, topic-node associations and optional pinned local ONNX semantic fallback.
- Reuse unchanged embedding chunks by body/model digest; preserve separate frozen regression and holdout evaluations.
- Add optional Ed25519 source keys, replay rejection, source review escalation and digest-bound current-source deactivation.
- Add bilingual quick setup, design tradeoffs, measured limitations, revised public Skill and Archify diagrams.
- Add portable Git lookup and a Windows lock backend; complete Windows acceptance remains pending.

No automatic migration, remote memory upload, model download or existing-store policy change.
See [English details](docs/v0.3.md) or [中文说明](docs/zh-CN/v0.3.md).
