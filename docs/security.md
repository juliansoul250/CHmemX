# Security and privacy

[简体中文](zh-CN/security.md)

This project provides workflow integrity, provenance checks, atomic writes, and Git rollback. It
does not make unsigned Agent IDs authenticated identities. Optional registered Ed25519 keys bind
signed bytes to a source key, not to truthful content. Same-user malicious processes remain outside
the protection boundary. See [current limits and personal-mode tradeoffs](v0.3.md#security-assessment).

Never place secrets, credentials, cookies, tokens, private keys, full chat transcripts, or hidden
runtime state in uploads or memory. Keep source-agent private directories isolated. The curator
reads shared uploads and canonical memory, not private source storage.

Pending, quarantine, rejected, and unresolved conflict records are excluded from recall. The
vector index is derived, contains no bodies, and refuses reads when its Git HEAD is stale.
Record vectors may still reveal whether a guessed term is present through dictionary probing, so
keep the index local unless its memory corpus is safe to disclose. Golden-query suites must be
redacted and must never become logs of private user prompts.

Backups are a separate policy. Do not claim disk-loss protection unless the owner explicitly
configures and verifies external backup.
