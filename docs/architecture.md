# Architecture

[简体中文](zh-CN/architecture.md) | [Interactive diagram](architecture.html)

The Git store is the canonical memory project. Global preference and registered projects are
subdirectories with independent Active indexes and node maps. Approval receipts bind every write
to a sealed batch digest and parent Git HEAD.

Uploads live outside the canonical store. The curation layer validates their evidence and creates
a single curator-owned inventory. The derived vector index contains metadata, sparse vectors, and
record paths but never copies record bodies. It is bound to the exact Memory Graph Git HEAD and
fails closed when stale.

Retrieval uses two levels:

1. score content cells from the natural-language query;
2. score and expand member routing nodes, then load linked accepted Active records.

The default vectorizer uses deterministic SHA-256 hashing over normalized word tokens and Chinese
2/3-character fragments. It is lexical, offline, and dependency-free; it is not a neural embedding.
