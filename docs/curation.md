# Centralized curation

[简体中文](zh-CN/curation.md)

Source tools have upload-only write permissions. The curator owns all normal mutations.

For each curation run:

1. validate source schema and provenance;
2. scan secrets and embedded instructions;
3. compare canonical identities within uploads and current Active;
4. collapse byte-equivalent candidates while preserving every origin in the report;
5. block disagreements and produce a current-vs-incoming diff;
6. use vector routing to suggest a content cell and related nodes;
7. let the curator write a recommendation;
8. obtain owner conflict resolution when needed;
9. create and review the final Pending batch;
10. obtain exact batch confirmation and commit atomically.

Never select the newest timestamp as truth. Project authority and explicit owner decisions outrank
memory summaries. Conflict resolution and final batch confirmation are separate approvals.
