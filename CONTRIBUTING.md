# Contributing

Changes should preserve these invariants:

- source agents upload but do not promote;
- only accepted Active records are recalled;
- project authority outranks memory;
- conflicts require explicit owner decisions;
- writes are atomic Git commits bound to exact batch review;
- the vector index contains no record bodies and fails closed when stale;
- tests use synthetic data only.

Run the commands in the README before opening a pull request.
