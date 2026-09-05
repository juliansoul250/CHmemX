"""Evaluate a frozen v3 index and suite without publishing or tuning either.

Reports remain local. Generated coverage is regression evidence, not unseen
semantic evidence. Keep a separate human-checked held-out suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from chmemx.scripts import retrieval_v3 as v3
from chmemx.scripts import vector_memory as lexical


def evaluate(index_path: Path, suite_path: Path):
    raw = index_path.read_bytes()
    suite_raw = suite_path.read_bytes()
    index = json.loads(raw)
    suite = lexical.load_evaluation_suite(suite_path)
    if not suite.get("cases"):
        raise ValueError("EMPTY_SUITE_NOT_EVIDENCE")
    begin = time.perf_counter()
    reader = v3.Retriever(index)
    cold = time.perf_counter() - begin
    times = []

    def ranked(_index, query, limit, cwd):
        start = time.perf_counter()
        result = reader.recall(query, cwd, limit)
        times.append(time.perf_counter() - start)
        return result

    # Reuse the existing evaluator; do not rewrite labels to fit candidate output.
    report = lexical.evaluate_index({**index, "type": lexical.INDEX_TYPE}, suite, ranker=ranked)
    report.update(
        index_type=v3.TYPE,
        index_sha256=hashlib.sha256(raw).hexdigest(),
        frozen_suite_sha256=hashlib.sha256(suite_raw).hexdigest(),
        latency_seconds={
            "cold": cold,
            "p50": statistics.median(times),
            "p95": sorted(times)[int(0.95 * (len(times) - 1))],
        },
        publication="NOT_PUBLISHED",
        evaluation_role="regression; held-out evidence must be reported separately",
    )
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--suite", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = evaluate(args.index, args.suite)
    v3.save(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
    return 0 if report["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
