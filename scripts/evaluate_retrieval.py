"""Evaluate a frozen v3 index and suite without publishing or tuning either.

Reports remain local. Generated coverage is regression evidence, not unseen
semantic evidence. Keep a separate human-checked held-out suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path

from chmemx.scripts import retrieval_v3 as v3
from chmemx.scripts import vector_memory as lexical


def evaluate(index_path: Path, suite_path: Path, *, source_checks: bool = True):
    return _evaluate(index_path.read_bytes(), suite_path.read_bytes(), source_checks=source_checks)


def evaluate_suites(index_path: Path, suite_paths: list[Path], *, source_checks: bool = True):
    if not suite_paths:
        raise ValueError("EMPTY_SUITE_NOT_EVIDENCE")
    # Freeze bytes once: preflight, reported hash and evaluated labels must agree.
    suites = [path.read_bytes() for path in suite_paths]
    lexical.validate_evaluation_suites([json.loads(raw) for raw in suites])
    raw = index_path.read_bytes()
    reports = [_evaluate(raw, data, source_checks=source_checks) for data in suites]
    if len(reports) == 1:
        return reports[0]
    return {"status": "EVALUATED", "passed": all(r["passed"] for r in reports),
            "reports": reports, "case_count": sum(r["case_count"] for r in reports),
            "source_checks": source_checks, "publication": "NOT_PUBLISHED",
            "evaluation_role": "per-suite gates preserved; not independent held-out evidence"}


def _evaluate(raw: bytes, suite_raw: bytes, *, source_checks: bool):
    index = json.loads(raw)
    suite = json.loads(suite_raw)
    lexical.validate_evaluation_suites([suite])
    if not suite.get("cases"):
        raise ValueError("EMPTY_SUITE_NOT_EVIDENCE")
    if any(case.get("top_k", 5) > 20 or case.get("required_rank", 3) > 20
           for case in suite["cases"]):
        raise ValueError("V3_EVALUATION_LIMIT_EXCEEDED: top_k and required_rank must be <= 20")
    begin = time.perf_counter()
    reader = v3.Retriever(index, source_checks=source_checks)
    cold = time.perf_counter() - begin
    times = []

    def ranked(_index, query, limit, cwd, *, association_limit=3):
        start = time.perf_counter()
        result = reader.recall(query, cwd, limit, association_limit)
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
            "cold_definition": "reader construction; excludes first query",
            "p50": statistics.median(times),
            "p95": sorted(times)[math.ceil(0.95 * len(times)) - 1],
            "samples": len(times),
            "percentile_method": "nearest-rank",
        },
        publication="NOT_PUBLISHED",
        source_checks=source_checks,
        evaluation_scope="current-source eligibility" if source_checks else "ranking-only; not current-use evidence",
        evaluation_role="regression; held-out evidence must be reported separately",
    )
    return report


def console_summary(report):
    return {key: [console_summary(item) for item in value] if key == "reports" else value
            for key, value in report.items() if key != "cases"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--suite", type=Path, required=True, action="append",
                   help="Repeat for cross-suite label checks; each suite retains its own gates")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--ranking-only", action="store_true",
                   help="Explicit frozen snapshot comparison; does not validate current project sources")
    args = p.parse_args()
    report = evaluate_suites(args.index, args.suite, source_checks=not args.ranking_only)
    v3.save(args.output, report)
    print(json.dumps(console_summary(report), indent=2))
    return 0 if report["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
