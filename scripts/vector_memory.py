#!/usr/bin/env python3
"""Build, evaluate, optimize, and query the full offline Memory Graph pointer.

The index contains sparse vectors and record paths, never record bodies. Recall
verifies the Memory Graph Git HEAD, follows content cells and graph relations,
then reads only accepted Active records from the canonical store. Optimization
publishes only after generated Active coverage and a redacted golden suite pass.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Iterable
import unicodedata

try:
    from .assemble_inventory import assemble, load_export
except ImportError:
    from assemble_inventory import assemble, load_export


LEGACY_INDEX_TYPE = "memorygraph-vector-pointer-v1"
INDEX_TYPE = "memorygraph-vector-pointer-v2"
SUPPORTED_INDEX_TYPES = {LEGACY_INDEX_TYPE, INDEX_TYPE}
EVALUATION_TYPE = "memorygraph-recall-evaluation-v1"
DEFAULT_DIMENSIONS = 2048
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.:/+\-]*")

SCORING_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "balanced-v2",
        "record_weight": 0.62,
        "node_weight": 0.28,
        "cell_weight": 0.10,
        "cell_to_node_boost": 0.30,
        "relation_decay": 0.50,
        "project_boost": 0.05,
        "exact_key_boost": 0.30,
        "exact_phrase_boost": 0.05,
        "exact_phrase_cap": 0.20,
        "absolute_threshold": 0.08,
        "relative_threshold": 0.34,
    },
    {
        "name": "precision-v2",
        "record_weight": 0.72,
        "node_weight": 0.22,
        "cell_weight": 0.06,
        "cell_to_node_boost": 0.22,
        "relation_decay": 0.42,
        "project_boost": 0.04,
        "exact_key_boost": 0.34,
        "exact_phrase_boost": 0.06,
        "exact_phrase_cap": 0.24,
        "absolute_threshold": 0.10,
        "relative_threshold": 0.42,
    },
    {
        "name": "graph-v2",
        "record_weight": 0.50,
        "node_weight": 0.34,
        "cell_weight": 0.16,
        "cell_to_node_boost": 0.38,
        "relation_decay": 0.58,
        "project_boost": 0.06,
        "exact_key_boost": 0.26,
        "exact_phrase_boost": 0.04,
        "exact_phrase_cap": 0.16,
        "absolute_threshold": 0.07,
        "relative_threshold": 0.30,
    },
)

LEGACY_SCORING_PROFILE: dict[str, Any] = {
    "name": "legacy-v1-compatible",
    "record_weight": 0.0,
    "node_weight": 1.0,
    "cell_weight": 0.0,
    "cell_to_node_boost": 0.45,
    "relation_decay": 0.55,
    "project_boost": 0.0,
    "exact_key_boost": 0.0,
    "exact_phrase_boost": 0.0,
    "exact_phrase_cap": 0.0,
    "absolute_threshold": 0.10,
    "relative_threshold": 0.25,
}


class VectorError(RuntimeError):
    pass


def git_head(store: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(store), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    if completed.returncode:
        raise VectorError("Memory Graph Git HEAD is unavailable")
    return completed.stdout.decode().strip()


def normalize_text(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def text_tokens(value: object) -> list[str]:
    text = normalize_text(value)
    tokens = WORD_RE.findall(text)
    for sequence in CJK_RE.findall(text):
        if len(sequence) <= 8:
            tokens.append(sequence)
        for size in (2, 3):
            tokens.extend(sequence[index : index + size] for index in range(len(sequence) - size + 1))
    words = text.split()
    tokens.extend(f"{words[index]}::{words[index + 1]}" for index in range(len(words) - 1))
    return tokens


def token_bucket(token: str, dimensions: int) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % dimensions


def sparse_vector(
    weighted_texts: Iterable[tuple[object, float]],
    dimensions: int,
    feature_weights: dict[int, float] | None = None,
) -> list[list[float]]:
    values: dict[int, float] = {}
    for text, weight in weighted_texts:
        for token in text_tokens(text):
            index = token_bucket(token, dimensions)
            values[index] = values.get(index, 0.0) + weight * (
                feature_weights.get(index, 1.0) if feature_weights else 1.0
            )
    norm = math.sqrt(sum(value * value for value in values.values()))
    if not norm:
        return []
    return [[float(index), round(value / norm, 8)] for index, value in sorted(values.items())]


def corpus_feature_weights(
    documents: Iterable[Iterable[tuple[object, float]]], dimensions: int
) -> list[list[float]]:
    document_frequency: dict[int, int] = {}
    document_count = 0
    for weighted_texts in documents:
        buckets = {
            token_bucket(token, dimensions)
            for text, _ in weighted_texts
            for token in text_tokens(text)
        }
        if not buckets:
            continue
        document_count += 1
        for bucket in buckets:
            document_frequency[bucket] = document_frequency.get(bucket, 0) + 1
    if not document_count:
        return []
    return [
        [
            float(bucket),
            round(math.log((1.0 + document_count) / (1.0 + frequency)) + 1.0, 8),
        ]
        for bucket, frequency in sorted(document_frequency.items())
    ]


def feature_weight_map(index: dict[str, Any]) -> dict[int, float]:
    return {
        int(bucket): float(weight)
        for bucket, weight in index.get("vectorizer", {}).get("feature_weights", [])
    }


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def object_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def cosine(left: list[list[float]], right: list[list[float]]) -> float:
    left_map = {int(index): float(value) for index, value in left}
    right_map = {int(index): float(value) for index, value in right}
    if len(left_map) > len(right_map):
        left_map, right_map = right_map, left_map
    return sum(value * right_map.get(index, 0.0) for index, value in left_map.items())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VectorError(f"JSON root is not an object: {path}")
    return value


def atomic_json(path: Path, value: object, replace: bool) -> None:
    target = path.expanduser().resolve()
    if target.exists() and not replace:
        raise VectorError(f"refusing to overwrite {target}; pass --replace")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".vector-memory-", dir=target.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def scope_uid(project_id: str | None, node_id: str) -> str:
    return f"project:{project_id}:{node_id}" if project_id else f"global:{node_id}"


def collect_active(store: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    catalog = load_json(store / "catalog.json")
    projects = catalog.get("projects")
    if not isinstance(projects, dict):
        raise VectorError("Memory Graph project catalog is malformed")
    records: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    locations: list[tuple[Path, str | None]] = [(store / "global", None)]
    locations.extend(
        (store / "projects" / str(project_id), str(project_id))
        for project_id in projects
    )
    for scope_root, expected_project in locations:
        if not scope_root.is_dir():
            continue
        active_index = load_json(scope_root / "active-index.json")
        entries = active_index.get("entries")
        if not isinstance(entries, dict):
            raise VectorError(f"active index is malformed: {scope_root}")
        for canonical_identity, record_id_value in sorted(entries.items()):
            record_id = str(record_id_value)
            path = scope_root / "active" / f"{record_id}.json"
            if not path.is_file():
                raise VectorError(f"active index points to a missing record: {record_id}")
            record = load_json(path)
            if record.get("authority") != "accepted" or record.get("status") != "active":
                raise VectorError(f"active index points to a non-active record: {record_id}")
            project_id = str(record["project_id"]) if record.get("project_id") else None
            if project_id != expected_project:
                raise VectorError(f"record project mismatch: {path}")
            expected_identity = f"{record.get('class')}:{record.get('key')}"
            if canonical_identity != expected_identity or record.get("id") != record_id:
                raise VectorError(f"active index identity mismatch: {record_id}")
            node_uids: list[str] = []
            for node in record.get("nodes") or []:
                node_id = str(node["id"])
                uid = scope_uid(project_id, node_id)
                node_uids.append(uid)
                related_uids = [scope_uid(project_id, str(value)) for value in node.get("related_node_ids") or []]
                definition = {
                    "uid": uid,
                    "node_id": node_id,
                    "scope": record["scope"],
                    "project_id": project_id,
                    "title": node["title"],
                    "keywords": node.get("keywords") or [],
                    "aliases": node.get("aliases") or [],
                    "related_uids": related_uids,
                    "record_ids": [record["id"]],
                    "record_keys": [record["key"]],
                }
                current = nodes.get(uid)
                if current is None:
                    nodes[uid] = definition
                else:
                    for field in ("node_id", "scope", "project_id", "title", "keywords", "aliases", "related_uids"):
                        if current[field] != definition[field]:
                            raise VectorError(f"routing node definition conflict: {uid}")
                    current["record_ids"].append(record["id"])
                    current["record_keys"].append(record["key"])
            records.append(
                {
                    "id": record["id"],
                    "key": record["key"],
                    "class": record["class"],
                    "scope": record["scope"],
                    "project_id": project_id,
                    "node_uids": node_uids,
                    "record_path": path.resolve().relative_to(store.resolve()).as_posix(),
                    "submitted_by_agent": record.get("submitted_by_agent"),
                    "committed_by_agent": record.get("committed_by_agent"),
                    "_body": record.get("body") or "",
                    "_source_locator": (record.get("source") or {}).get("locator") or "",
                }
            )
    return catalog, records, [nodes[key] for key in sorted(nodes)]


def cell_matches_record(cell: dict[str, Any], record: dict[str, Any]) -> bool:
    if cell.get("project_id") and cell.get("project_id") != record.get("project_id"):
        return False
    key = str(record["key"])
    if key in {str(value) for value in cell.get("member_keys") or []}:
        return True
    return any(key.startswith(str(prefix)) for prefix in cell.get("member_key_prefixes") or [])


def node_vector_texts(node: dict[str, Any]) -> list[tuple[object, float]]:
    return [
        (node["title"], 4.0),
        (" ".join(node["keywords"]), 6.0),
        (" ".join(node["aliases"]), 5.0),
        (" ".join(node["record_keys"]), 3.0),
    ]


def record_vector_texts(
    record: dict[str, Any], node_by_uid: dict[str, dict[str, Any]]
) -> list[tuple[object, float]]:
    linked = [node_by_uid[uid] for uid in record["node_uids"] if uid in node_by_uid]
    return [
        (record["key"], 8.0),
        (record["class"], 1.5),
        (record.get("_body") or "", 1.0),
        (record.get("_source_locator") or "", 0.5),
        (" ".join(str(node["title"]) for node in linked), 5.0),
        (
            " ".join(str(word) for node in linked for word in node.get("keywords") or []),
            7.0,
        ),
        (
            " ".join(str(word) for node in linked for word in node.get("aliases") or []),
            6.0,
        ),
        (" ".join(record.get("hints") or []), 9.0),
    ]


def cell_vector_texts(
    cell: dict[str, Any], member_record_ids: list[str], record_by_id: dict[str, dict[str, Any]]
) -> list[tuple[object, float]]:
    return [
        (cell["title"], 5.0),
        (" ".join(cell.get("keywords") or []), 7.0),
        (" ".join(cell.get("aliases") or []), 6.0),
        (" ".join(record_by_id[value]["key"] for value in member_record_ids), 3.0),
    ]


def build_index(
    store: Path,
    taxonomy_path: Path,
    dimensions: int = DEFAULT_DIMENSIONS,
    scoring_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = store.expanduser().resolve()
    taxonomy = load_json(taxonomy_path.expanduser().resolve())
    catalog, records, nodes = collect_active(store)
    cells = taxonomy.get("cells")
    if not isinstance(cells, list) or not cells:
        raise VectorError("content directory has no cells")
    hints = taxonomy.get("record_hints") or {}
    if not isinstance(hints, dict):
        raise VectorError("record_hints must be an object")
    if set(hints) - {r["key"] for r in records}:
        raise VectorError("record_hints references unknown Active keys")
    for record in records:
        values = hints.get(record["key"], [])
        if not isinstance(values, list) or len(values) > 64 or any(
            not isinstance(v, str) or not v.strip() or len(v) > 256 for v in values
        ):
            raise VectorError("record_hints must contain bounded nonempty strings")
        record["hints"] = list(dict.fromkeys(normalize_text(v) for v in values))
    record_by_id = {str(record["id"]): record for record in records}
    node_by_uid = {str(node["uid"]): node for node in nodes}

    assigned_records: set[str] = set()
    indexed_cells: list[dict[str, Any]] = []
    for cell in cells:
        member_record_ids = [
            str(record["id"]) for record in records if cell_matches_record(cell, record)
        ]
        assigned_records.update(member_record_ids)
        member_node_uids = sorted(
            {
                uid
                for record_id in member_record_ids
                for uid in record_by_id[record_id]["node_uids"]
                if uid in node_by_uid
            }
        )
        indexed_cells.append(
            {
                "id": cell["id"],
                "title": cell["title"],
                "keywords": cell.get("keywords") or [],
                "aliases": cell.get("aliases") or [],
                "project_id": cell.get("project_id"),
                "related_cell_ids": cell.get("related_cell_ids") or [],
                "member_record_ids": sorted(member_record_ids),
                "member_node_uids": member_node_uids,
            }
        )

    documents: list[list[tuple[object, float]]] = []
    documents.extend(node_vector_texts(node) for node in nodes)
    documents.extend(record_vector_texts(record, node_by_uid) for record in records)
    documents.extend(
        cell_vector_texts(cell, cell["member_record_ids"], record_by_id)
        for cell in indexed_cells
    )
    feature_weights = corpus_feature_weights(documents, dimensions)
    feature_weights_by_bucket = {
        int(bucket): float(weight) for bucket, weight in feature_weights
    }

    for node in nodes:
        node["vector"] = sparse_vector(
            node_vector_texts(node), dimensions, feature_weights_by_bucket
        )
    for record in records:
        record["vector"] = sparse_vector(
            record_vector_texts(record, node_by_uid), dimensions, feature_weights_by_bucket
        )
        record.pop("_body", None)
        record.pop("_source_locator", None)
    for cell in indexed_cells:
        cell["vector"] = sparse_vector(
            cell_vector_texts(cell, cell["member_record_ids"], record_by_id),
            dimensions,
            feature_weights_by_bucket,
        )

    unassigned = sorted(set(record_by_id) - assigned_records)
    result: dict[str, Any] = {
        "schema_version": 2,
        "type": INDEX_TYPE,
        "memory_graph_store": str(store),
        "memory_graph_head": git_head(store),
        "taxonomy_path": str(taxonomy_path.expanduser().resolve()),
        "vectorizer": {
            "algorithm": "sha256-hashed-idf-word-and-cjk-2-3gram",
            "dimensions": dimensions,
            "normalization": "NFKC-casefold-l2",
            "semantic_embedding": False,
            "offline": True,
            "corpus_adaptive": True,
            "feature_weights": feature_weights,
        },
        "scoring_profile": copy.deepcopy(scoring_profile or SCORING_PROFILES[0]),
        "projects": catalog["projects"],
        "cells": indexed_cells,
        "nodes": nodes,
        "records": sorted(records, key=lambda value: (str(value["project_id"]), str(value["key"]))),
        "unassigned_record_ids": unassigned,
        "candidate_entries_included": False,
    }
    generated = evaluate_index(result, generated_evaluation_suite(result))
    result["quality"] = {
        "generated_coverage": evaluation_summary(generated),
        "generated_suite_digest": generated["suite_digest"],
    }
    return result


def current_project(index: dict[str, Any], cwd: Path) -> str | None:
    resolved = cwd.expanduser().resolve()
    matches: list[tuple[int, str]] = []
    for project_id, value in index.get("projects", {}).items():
        root = Path(str(value["root"])).expanduser().resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        matches.append((len(root.parts), str(project_id)))
    return max(matches)[1] if matches else None


def ensure_fresh(index: dict[str, Any]) -> Path:
    if index.get("type") not in SUPPORTED_INDEX_TYPES:
        raise VectorError("unsupported vector index")
    store = Path(str(index["memory_graph_store"])).expanduser().resolve()
    if git_head(store) != index.get("memory_graph_head"):
        raise VectorError("INDEX_STALE: rebuild after the latest Memory Graph commit")
    return store


def ranked(index: dict[str, Any], query: str, limit: int, cwd: Path) -> dict[str, Any]:
    store = ensure_fresh(index)
    dimensions = int(index["vectorizer"]["dimensions"])
    weights = feature_weight_map(index)
    query_vector = sparse_vector([(query, 1.0)], dimensions, weights)
    if not query_vector:
        return {
            "status": "OK",
            "query": query,
            "entries": [],
            "candidate_entries_included": False,
        }

    profile = copy.deepcopy(
        index.get("scoring_profile")
        or (
            LEGACY_SCORING_PROFILE
            if index.get("type") == LEGACY_INDEX_TYPE
            else SCORING_PROFILES[0]
        )
    )

    cell_scores = {
        str(cell["id"]): cosine(query_vector, cell["vector"])
        for cell in index["cells"]
    }
    top_cells = [
        cell_id
        for cell_id, score in sorted(cell_scores.items(), key=lambda item: (-item[1], item[0]))[:3]
        if score > 0
    ]
    cell_by_id = {str(cell["id"]): cell for cell in index["cells"]}
    node_scores = {
        str(node["uid"]): cosine(query_vector, node["vector"])
        for node in index["nodes"]
    }
    for cell_id in top_cells:
        boost = cell_scores[cell_id] * float(profile["cell_to_node_boost"])
        for uid in cell_by_id[cell_id]["member_node_uids"]:
            node_scores[uid] = node_scores.get(uid, 0.0) + boost

    node_by_uid = {str(node["uid"]): node for node in index["nodes"]}
    seeds = [
        uid for uid, score in sorted(node_scores.items(), key=lambda item: (-item[1], item[0]))[:8]
        if score > 0
    ]
    for uid in seeds:
        seed_score = node_scores[uid]
        for related_uid in node_by_uid[uid]["related_uids"]:
            if related_uid in node_scores:
                node_scores[related_uid] = max(
                    node_scores[related_uid],
                    seed_score * float(profile["relation_decay"]),
                )

    record_meta = {str(record["id"]): record for record in index["records"]}
    active_project = current_project(index, cwd)
    query_text = normalize_text(query)
    record_scores: dict[str, float] = {}
    score_parts: dict[str, dict[str, float]] = {}
    record_matches: dict[str, dict[str, list[str]]] = {
        record_id: {"cells": [], "nodes": []} for record_id in record_meta
    }
    for cell_id in top_cells:
        for record_id in cell_by_id[cell_id]["member_record_ids"]:
            record_matches[record_id]["cells"].append(cell_id)
    for uid, score in node_scores.items():
        if score <= 0:
            continue
        for record_id in node_by_uid[uid]["record_ids"]:
            record_matches[record_id]["nodes"].append(uid)

    for record_id, metadata in record_meta.items():
        direct_score = cosine(query_vector, metadata.get("vector") or [])
        node_score = max(
            (node_scores.get(uid, 0.0) for uid in metadata.get("node_uids") or []),
            default=0.0,
        )
        cell_score = max(
            (
                cell_scores.get(cell_id, 0.0)
                for cell_id, cell in cell_by_id.items()
                if record_id in cell["member_record_ids"]
            ),
            default=0.0,
        )
        key_text = normalize_text(metadata["key"])
        key_words = normalize_text(re.sub(r"[._:/+\-]+", " ", str(metadata["key"])))
        exact_key = bool(
            query_text
            and (
                (key_text and key_text in query_text)
                or (key_words and key_words in query_text)
            )
        )
        phrase_hits = 0
        for uid in metadata.get("node_uids") or []:
            node = node_by_uid.get(uid)
            if not node:
                continue
            phrases = [node["title"], *(node.get("keywords") or []), *(node.get("aliases") or [])]
            phrase_hits += sum(
                1
                for phrase in phrases
                if len(normalize_text(phrase)) >= 2 and normalize_text(phrase) in query_text
            )
        exact_bonus = (
            float(profile["exact_key_boost"]) if exact_key else 0.0
        ) + min(
            float(profile["exact_phrase_cap"]),
            phrase_hits * float(profile["exact_phrase_boost"]),
        )
        hint_hits = sum(1 for phrase in metadata.get("hints") or []
                        if normalize_text(phrase) in query_text)
        exact_bonus = min(float(profile["exact_phrase_cap"]),
                          exact_bonus + hint_hits * float(profile["exact_phrase_boost"])) if not exact_key else exact_bonus
        project_bonus = (
            float(profile["project_boost"])
            if active_project and metadata.get("project_id") == active_project
            else 0.0
        )
        final_score = (
            direct_score * float(profile["record_weight"])
            + node_score * float(profile["node_weight"])
            + cell_score * float(profile["cell_weight"])
            + exact_bonus
            + project_bonus
        )
        record_scores[record_id] = final_score
        score_parts[record_id] = {
            "direct": direct_score,
            "node": node_score,
            "cell": cell_score,
            "exact": exact_bonus,
            "project": project_bonus,
        }

    ordered_records = sorted(record_scores.items(), key=lambda item: (-item[1], item[0]))
    best_record_score = ordered_records[0][1] if ordered_records else 0.0
    minimum_record_score = max(
        float(profile["absolute_threshold"]),
        best_record_score * float(profile["relative_threshold"]),
    )
    selected = [
        record_id
        for record_id, score in ordered_records
        if score >= minimum_record_score
    ][:limit]
    entries: list[dict[str, Any]] = []
    for record_id in selected:
        metadata = record_meta[record_id]
        record = load_json(store / metadata["record_path"])
        if record.get("id") != record_id or record.get("authority") != "accepted" or record.get("status") != "active":
            raise VectorError(f"active record changed after vector indexing: {record_id}")
        project_id = metadata.get("project_id")
        entries.append(
            {
                **record,
                "vector_pointer": {
                    "score": round(record_scores[record_id], 8),
                    "score_components": {
                        key: round(value, 8) for key, value in score_parts[record_id].items()
                    },
                    "matched_cells": sorted(set(record_matches[record_id]["cells"])),
                    "matched_nodes": sorted(set(record_matches[record_id]["nodes"])),
                    "current_project_id": active_project,
                    "cross_project_reference": bool(project_id and project_id != active_project),
                },
            }
        )
    return {
        "status": "OK",
        "query": query,
        "memory_graph_head": index["memory_graph_head"],
        "vectorizer": index["vectorizer"],
        "scoring_profile": profile,
        "matched_cells": [
            {"id": cell_id, "score": round(cell_scores[cell_id], 8)} for cell_id in top_cells
        ],
        "minimum_record_score": round(minimum_record_score, 8),
        "entries": entries,
        "candidate_entries_included": False,
    }


def load_evaluation_suite(path: Path) -> dict[str, Any]:
    suite = load_json(path.expanduser().resolve())
    if suite.get("schema_version") != 1 or suite.get("type") != EVALUATION_TYPE:
        raise VectorError("unsupported recall evaluation suite")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise VectorError("recall evaluation suite requires at least one case")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise VectorError("recall evaluation case must be an object")
        case_id = str(case.get("id") or "")
        query = str(case.get("query") or "").strip()
        expected = case.get("expected_keys")
        if not case_id or case_id in seen or not query:
            raise VectorError("recall evaluation case id/query is invalid")
        if not isinstance(expected, list) or not expected or not all(expected):
            raise VectorError(f"recall evaluation case {case_id} needs expected_keys")
        top_k = int(case.get("top_k", 5))
        required_rank = int(case.get("required_rank", min(3, top_k)))
        if top_k < 1 or top_k > 64 or required_rank < 1 or required_rank > top_k:
            raise VectorError(f"recall evaluation case {case_id} has invalid rank limits")
        seen.add(case_id)
    return suite


def evaluation_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "passed": report["passed"],
        "case_count": report["case_count"],
        "suite_digest": report["suite_digest"],
        "metrics": report["metrics"],
        "thresholds": report["thresholds"],
    }


def evaluation_cwd(index: dict[str, Any], project_id: str | None) -> Path:
    if project_id is None:
        return Path("/__chmemx_neutral_context__")
    project = index.get("projects", {}).get(project_id)
    if not isinstance(project, dict) or not project.get("root"):
        raise VectorError(f"evaluation references unknown project {project_id}")
    return Path(str(project["root"]))


def evaluate_index(index: dict[str, Any], suite: dict[str, Any], *, ranker=None) -> dict[str, Any]:
    ensure_fresh(index)
    if suite.get("schema_version") != 1 or suite.get("type") != EVALUATION_TYPE:
        raise VectorError("unsupported recall evaluation suite")
    raw_cases = suite.get("cases") or []
    if not raw_cases and suite.get("suite_id") != "generated-active-coverage":
        raise VectorError("recall evaluation suite requires at least one case")
    thresholds = {
        "min_top1": 0.0,
        "min_hit_at_3": 1.0,
        "min_mrr": 0.0,
        "min_precision_at_k": 0.0,
        "max_forbidden_hits": 0,
        "max_scope_violations": 0,
        **(suite.get("thresholds") or {}),
    }
    case_reports: list[dict[str, Any]] = []
    top1_hits = 0
    top3_hits = 0
    reciprocal_rank_total = 0.0
    precision_total = 0.0
    forbidden_hits = 0
    scope_violations = 0
    result_count_total = 0
    for case in raw_cases:
        case_id = str(case["id"])
        top_k = int(case.get("top_k", 5))
        required_rank = int(case.get("required_rank", min(3, top_k)))
        expected = {str(value) for value in case["expected_keys"]}
        relevant = {str(value) for value in case.get("relevant_keys") or expected}
        forbidden = {str(value) for value in case.get("forbidden_keys") or []}
        project_id = str(case["project_id"]) if case.get("project_id") else None
        result = (ranker or ranked)(
            index,
            str(case["query"]),
            max(top_k, required_rank, 3),
            evaluation_cwd(index, project_id),
        )
        keys = [str(entry["key"]) for entry in result["entries"]]
        result_count_total += len(keys[:top_k])
        ranks = [position for position, key in enumerate(keys, 1) if key in expected]
        first_rank = min(ranks) if ranks else None
        top1 = bool(first_rank and first_rank <= 1)
        top3 = bool(first_rank and first_rank <= 3)
        within_required = bool(first_rank and first_rank <= required_rank)
        top1_hits += int(top1)
        top3_hits += int(top3)
        reciprocal_rank_total += 1.0 / first_rank if first_rank else 0.0
        considered = keys[:top_k]
        precision = len(relevant & set(considered)) / max(1, len(considered))
        precision_total += precision
        case_forbidden = sorted(forbidden & set(considered))
        forbidden_hits += len(case_forbidden)
        case_scope: list[str] = []
        if case.get("forbid_cross_project"):
            for entry in result["entries"][:top_k]:
                entry_project = entry.get("project_id")
                if entry_project and entry_project != project_id:
                    case_scope.append(str(entry["key"]))
            scope_violations += len(case_scope)
        case_reports.append(
            {
                "id": case_id,
                "query": case["query"],
                "project_id": project_id,
                "expected_keys": sorted(expected),
                "returned_keys": considered,
                "first_expected_rank": first_rank,
                "required_rank": required_rank,
                "precision_at_k": round(precision, 8),
                "forbidden_hits": case_forbidden,
                "scope_violations": case_scope,
                "passed": within_required and not case_forbidden and not case_scope,
            }
        )
    count = len(case_reports)
    if count == 0:
        metrics = {
            "top1": 1.0,
            "hit_at_3": 1.0,
            "mrr": 1.0,
            "mean_precision_at_k": 1.0,
            "forbidden_hits": 0,
            "scope_violations": 0,
            "average_results": 0.0,
        }
        return {
            "schema_version": 1,
            "type": "memorygraph-recall-evaluation-report-v1",
            "status": "QUALITY_PASS",
            "passed": True,
            "suite_id": suite.get("suite_id"),
            "suite_digest": object_digest(suite),
            "memory_graph_head": index["memory_graph_head"],
            "index_type": index["type"],
            "scoring_profile": copy.deepcopy(
                index.get("scoring_profile") or LEGACY_SCORING_PROFILE
            ),
            "case_count": 0,
            "metrics": metrics,
            "thresholds": thresholds,
            "cases": [],
        }
    metrics = {
        "top1": round(top1_hits / count, 8),
        "hit_at_3": round(top3_hits / count, 8),
        "mrr": round(reciprocal_rank_total / count, 8),
        "mean_precision_at_k": round(precision_total / count, 8),
        "forbidden_hits": forbidden_hits,
        "scope_violations": scope_violations,
        "average_results": round(result_count_total / count, 8),
    }
    passed = (
        metrics["top1"] >= float(thresholds["min_top1"])
        and metrics["hit_at_3"] >= float(thresholds["min_hit_at_3"])
        and metrics["mrr"] >= float(thresholds["min_mrr"])
        and metrics["mean_precision_at_k"] >= float(thresholds["min_precision_at_k"])
        and forbidden_hits <= int(thresholds["max_forbidden_hits"])
        and scope_violations <= int(thresholds["max_scope_violations"])
        and all(case["passed"] for case in case_reports)
    )
    return {
        "schema_version": 1,
        "type": "memorygraph-recall-evaluation-report-v1",
        "status": "QUALITY_PASS" if passed else "QUALITY_FAIL",
        "passed": passed,
        "suite_id": suite.get("suite_id"),
        "suite_digest": object_digest(suite),
        "memory_graph_head": index["memory_graph_head"],
        "index_type": index["type"],
        "scoring_profile": copy.deepcopy(index.get("scoring_profile") or LEGACY_SCORING_PROFILE),
        "case_count": count,
        "metrics": metrics,
        "thresholds": thresholds,
        "cases": case_reports,
    }


def generated_evaluation_suite(index: dict[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for record in index.get("records") or []:
        cases.append(
            {
                "id": f"record-{record['id']}",
                "query": record["key"],
                "project_id": record.get("project_id"),
                "expected_keys": [record["key"]],
                "relevant_keys": [record["key"]],
                "top_k": 3,
                "required_rank": 3,
            }
        )
    for position, node in enumerate(index.get("nodes") or [], 1):
        query = " ".join(
            [str(node["title"]), *(str(value) for value in (node.get("keywords") or [])[:3])]
        )
        cases.append(
            {
                "id": f"node-{position}-{node['node_id']}",
                "query": query,
                "project_id": node.get("project_id"),
                "expected_keys": list(node.get("record_keys") or []),
                "relevant_keys": list(node.get("record_keys") or []),
                "top_k": 5,
                "required_rank": 3,
            }
        )
    return {
        "schema_version": 1,
        "type": EVALUATION_TYPE,
        "suite_id": "generated-active-coverage",
        "thresholds": {
            "min_top1": 0.65,
            "min_hit_at_3": 1.0,
            "min_mrr": 0.75,
            "min_precision_at_k": 0.15,
            "max_forbidden_hits": 0,
            "max_scope_violations": 0,
        },
        "cases": cases,
    }


def quality_order(report: dict[str, Any]) -> tuple[float, ...]:
    metrics = report["metrics"]
    return (
        float(bool(report["passed"])),
        -float(metrics["forbidden_hits"]),
        -float(metrics["scope_violations"]),
        float(metrics["top1"]),
        float(metrics["hit_at_3"]),
        float(metrics["mrr"]),
        float(metrics["mean_precision_at_k"]),
        -float(metrics["average_results"]),
    )


def optimize_scoring(
    index: dict[str, Any], suite: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    trials: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for profile in SCORING_PROFILES:
        candidate = copy.deepcopy(index)
        candidate["scoring_profile"] = copy.deepcopy(profile)
        report = evaluate_index(candidate, suite)
        trials.append((profile, report))
    profile, report = max(trials, key=lambda value: quality_order(value[1]))
    selected = copy.deepcopy(index)
    selected["scoring_profile"] = copy.deepcopy(profile)
    generated = evaluate_index(selected, generated_evaluation_suite(selected))
    selected["quality"] = {
        "generated_coverage": evaluation_summary(generated),
        "generated_suite_digest": generated["suite_digest"],
        "golden_evaluation": evaluation_summary(report),
        "selected_profile": profile["name"],
        "profile_trials": [
            {
                "profile": trial_profile["name"],
                **evaluation_summary(trial_report),
            }
            for trial_profile, trial_report in trials
        ],
    }
    return selected, report, selected["quality"]["profile_trials"]


def route_upload(index: dict[str, Any], export_path: Path) -> dict[str, Any]:
    ensure_fresh(index)
    payload = load_export(export_path)
    inventory, source_report = assemble(payload)
    if inventory is None:
        return {
            "status": "NO_IMPORTABLE_CANDIDATES",
            "source_report": source_report,
            "routes": [],
        }
    dimensions = int(index["vectorizer"]["dimensions"])
    weights = feature_weight_map(index)
    record_identities = {
        (record["scope"], record.get("project_id"), record["class"], str(record["key"]).casefold()): record
        for record in index["records"]
    }
    roots = {
        str(Path(str(value["root"])).expanduser().resolve()): str(project_id)
        for project_id, value in index.get("projects", {}).items()
    }
    routes: list[dict[str, Any]] = []
    for candidate in inventory["candidates"]:
        source = candidate["source"]
        project_id = None
        if candidate["scope"] == "project":
            project_id = roots.get(str(Path(str(source["project_root"])).expanduser().resolve()))
        vector = sparse_vector(
            [
                (candidate["key"], 4.0),
                (candidate["body"], 1.0),
                (" ".join(node["title"] for node in candidate["nodes"]), 4.0),
                (" ".join(word for node in candidate["nodes"] for word in node["keywords"]), 7.0),
                (" ".join(word for node in candidate["nodes"] for word in node["aliases"]), 5.0),
            ],
            dimensions,
            weights,
        )
        cell_scores = sorted(
            ((str(cell["id"]), cosine(vector, cell["vector"])) for cell in index["cells"]),
            key=lambda item: (-item[1], item[0]),
        )
        node_scores = sorted(
            ((str(node["uid"]), cosine(vector, node["vector"])) for node in index["nodes"]),
            key=lambda item: (-item[1], item[0]),
        )
        active = record_identities.get(
            (candidate["scope"], project_id, candidate["class"], str(candidate["key"]).casefold())
        )
        routes.append(
            {
                "key": candidate["key"],
                "origin_agent": source_report["agent_id"],
                "project_id": project_id,
                "top_cells": [
                    {"id": cell_id, "score": round(score, 8)}
                    for cell_id, score in cell_scores[:3] if score > 0
                ],
                "top_nodes": [
                    {"uid": uid, "score": round(score, 8)}
                    for uid, score in node_scores[:5] if score > 0
                ],
                "active_identity_match": active,
                "recommendation": (
                    "CONFLICT_REVIEW_REQUIRED" if active else "CURATOR_SELECT_CELL_AND_RELATIONS"
                ),
                "automatic_write_allowed": False,
            }
        )
    return {
        "status": "ROUTED_FOR_CURATION",
        "memory_graph_head": index["memory_graph_head"],
        "source_report": source_report,
        "routes": routes,
        "canonical_store_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    item = commands.add_parser("build")
    item.add_argument(
        "--store",
        default=os.environ.get(
            "MEMORY_GRAPH_HOME", str(Path.home() / ".memory-graph" / "store")
        ),
    )
    item.add_argument(
        "--taxonomy",
        default=str(Path(__file__).resolve().parent.parent / "examples" / "content-directory.example.json"),
    )
    item.add_argument("--output", required=True)
    item.add_argument("--dimensions", type=int, default=DEFAULT_DIMENSIONS)
    item.add_argument("--replace", action="store_true")
    item = commands.add_parser("recall")
    item.add_argument("--index", required=True)
    item.add_argument("--query", required=True)
    item.add_argument("--cwd", default=os.getcwd())
    item.add_argument("--limit", type=int, default=8)
    item = commands.add_parser("route-upload")
    item.add_argument("--index", required=True)
    item.add_argument("--export", required=True)
    item = commands.add_parser("evaluate")
    item.add_argument("--index", required=True)
    item.add_argument("--suite", required=True)
    item = commands.add_parser("optimize")
    item.add_argument(
        "--store",
        default=os.environ.get(
            "MEMORY_GRAPH_HOME", str(Path.home() / ".memory-graph" / "store")
        ),
    )
    item.add_argument(
        "--taxonomy",
        default=str(Path(__file__).resolve().parent.parent / "examples" / "content-directory.example.json"),
    )
    item.add_argument("--suite", required=True)
    item.add_argument("--output", required=True)
    item.add_argument("--report")
    item.add_argument("--dimensions", type=int, default=DEFAULT_DIMENSIONS)
    item.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        exit_code = 0
        if args.command == "build":
            if args.dimensions < 256 or args.dimensions > 65536:
                raise VectorError("dimensions must be between 256 and 65536")
            result = build_index(Path(args.store), Path(args.taxonomy), args.dimensions)
            atomic_json(Path(args.output), result, args.replace)
            output: dict[str, Any] = {
                "status": "INDEX_BUILT",
                "output": str(Path(args.output).expanduser().resolve()),
                "memory_graph_head": result["memory_graph_head"],
                "cells": len(result["cells"]),
                "nodes": len(result["nodes"]),
                "records": len(result["records"]),
                "unassigned_records": result["unassigned_record_ids"],
                "vectorizer": result["vectorizer"],
                "scoring_profile": result["scoring_profile"],
                "quality": result["quality"],
            }
        elif args.command == "recall":
            output = ranked(load_json(Path(args.index)), args.query, args.limit, Path(args.cwd))
        elif args.command == "route-upload":
            output = route_upload(load_json(Path(args.index)), Path(args.export))
        elif args.command == "evaluate":
            output = evaluate_index(
                load_json(Path(args.index)), load_evaluation_suite(Path(args.suite))
            )
            if not output["passed"]:
                exit_code = 3
        else:
            if args.dimensions < 256 or args.dimensions > 65536:
                raise VectorError("dimensions must be between 256 and 65536")
            output_path = Path(args.output).expanduser().resolve()
            report_path = Path(args.report).expanduser().resolve() if args.report else None
            for target in (output_path, report_path):
                if target is not None and target.exists() and not args.replace:
                    raise VectorError(f"refusing to overwrite {target}; pass --replace")
            candidate = build_index(Path(args.store), Path(args.taxonomy), args.dimensions)
            selected, report, trials = optimize_scoring(
                candidate, load_evaluation_suite(Path(args.suite))
            )
            generated = selected["quality"]["generated_coverage"]
            report_artifact = {
                **report,
                "generated_coverage": generated,
                "profile_trials": trials,
                "selected_profile": selected["scoring_profile"]["name"],
                "index_published": bool(report["passed"] and generated["passed"]),
            }
            if not report["passed"] or not generated["passed"]:
                if report_path is not None:
                    atomic_json(report_path, report_artifact, args.replace)
                raise VectorError("QUALITY_GATE_FAILED: index was not published")
            atomic_json(output_path, selected, args.replace)
            if report_path is not None:
                atomic_json(report_path, report_artifact, args.replace)
            output = {
                "status": "INDEX_OPTIMIZED",
                "output": str(output_path),
                "report": str(report_path) if report_path else None,
                "memory_graph_head": selected["memory_graph_head"],
                "cells": len(selected["cells"]),
                "nodes": len(selected["nodes"]),
                "records": len(selected["records"]),
                "unassigned_records": selected["unassigned_record_ids"],
                "selected_profile": selected["scoring_profile"]["name"],
                "golden_evaluation": evaluation_summary(report),
                "generated_coverage": generated,
                "profile_trials": trials,
            }
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code
    except (VectorError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "VECTOR_MEMORY_FAILED", "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
