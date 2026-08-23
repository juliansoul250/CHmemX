# Security and privacy

[简体中文](zh-CN/security.md)

This project provides workflow integrity, provenance checks, atomic writes, and Git rollback. It
does not authenticate agent identities and cannot stop a malicious same-user process from passing
another Agent ID.

Never place secrets, credentials, cookies, tokens, private keys, full chat transcripts, or hidden
runtime state in uploads or memory. Keep source-agent private directories isolated. The curator
reads shared uploads and canonical memory, not private source storage.

Pending, quarantine, rejected, and unresolved conflict records are excluded from recall. The
vector index is derived, contains no bodies, and refuses reads when its Git HEAD is stale.

Backups are a separate policy. Do not claim disk-loss protection unless the owner explicitly
configures and verifies external backup.
