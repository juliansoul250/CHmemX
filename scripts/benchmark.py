"""Measure mechanical curation and write-to-recall latency in a disposable store."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from chmemx.scripts.retrieval_v3 import save

from chmemx.service import Service


def benchmark():
    with tempfile.TemporaryDirectory(prefix="chmemx-benchmark-") as value:
        root = Path(value)
        project = root / "fictional-project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        service = Service(root / "store", project, "fictional-source")
        service.initialize("project-fictional", "team")
        begin = time.perf_counter()
        upload = service.upload(
            "preference.editor.theme",
            "The preferred editor theme is blue.",
            {"quote": "The preferred editor theme is blue."},
        )
        uploaded = time.perf_counter()
        review = service.review(upload["upload_id"])
        reviewed = time.perf_counter()
        # Synthetic fixture only: this is not evidence of a real Owner's approval.
        service.runtime.approve(
            review["batch_id"],
            review["batch_digest"],
            review["required_confirmation"],
            committing_agent="main-memory-curator",
        )
        committed = time.perf_counter()
        result = service.recall("preference.editor.theme")
        available = time.perf_counter()
        assert len(result["entries"]) == 1
        samples = []
        for _ in range(20):
            begin_recall = time.perf_counter()
            service.recall("preference.editor.theme")
            samples.append(time.perf_counter() - begin_recall)
        return {
            "type": "chmemx-mechanical-benchmark-v1",
            "fixture": "one synthetic preference; lexical backend",
            "human_review_seconds": None,
            "human_review_note": "Not measured; never infer from automation time.",
            "upload_seconds": uploaded - begin,
            "curation_to_sealed_review_seconds": reviewed - uploaded,
            "approval_commit_seconds": committed - reviewed,
            "commit_to_recall_seconds": available - committed,
            "total_machine_pipeline_seconds": available - begin,
            "warm_recall_seconds": {
                "samples": 20,
                "p50": statistics.median(samples),
                "p95": sorted(samples)[18],
            },
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = benchmark()
    save(args.output, result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
