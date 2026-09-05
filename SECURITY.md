# Security policy

[简体中文](SECURITY.zh-CN.md)

Do not report real credentials in a public issue. If a vulnerability requires sensitive details,
contact the repository owner privately through their GitHub profile.

Never use real secrets, private memory, full transcripts, or production paths in reproductions.
Use temporary repositories and synthetic records.

The project is designed to prevent accidental workflow mistakes. Unsigned Agent IDs remain soft
identities. Optional Ed25519 checks bind payloads to operator-registered keys, not to honest content.
CHmemX is not a security boundary against malicious processes running as the same OS user.
See the [v0.3 threat-model limits](docs/v0.3.md#security-assessment).
