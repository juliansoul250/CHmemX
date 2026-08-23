#!/usr/bin/env python3
"""Build and query an offline vector pointer over the full Memory Graph.

The index contains sparse vectors and record paths, never record bodies. Recall
verifies the Memory Graph Git HEAD, follows content cells and graph relations,
then reads only accepted Active records from the canonical store.
"""

from __future__ import annotations

import argparse
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

from assemble_inventory import assemble, load_export


INDEX_TYPE = "memorygraph-vector-pointer-v1"
DEFAULT_DIMENSIONS = 2048
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.:/+\-]*")


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


def sparse_vector(weighted_texts: Iterable[tuple[object, float]], dimensions: int) -> list[list[float]]:
    values: dict[int, float] = {}
    for text, weight in weighted_texts:
        for token in text_tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            values[index] = values.get(index, 0.0) + weight
    norm = math.sqrt(sum(value * value for value in values.values()))
    if not norm:
        return []
    return [[float(index), round(value / norm, 8)] for index, value in sorted(values.items())]


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
                    "record_path": str(path.resolve().relative_to(store.resolve())),
                    "submitted_by_agent": record.get("submitted_by_agent"),
                    "committed_by_agent": record.get("committed_by_agent"),
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


def build_index(store: Path, taxonomy_path: Path, dimensions: int = DEFAULT_DIMENSIONS) -> dict[str, Any]:
    store = store.expanduser().resolve()
    taxonomy = load_json(taxonomy_path.expanduser().resolve())
    catalog, records, nodes = collect_active(store)
    cells = taxonomy.get("cells")
    if not isinstance(cells, list) or not cells:
        raise VectorError("content directory has no cells")
    record_by_id = {str(record["id"]): record for record in records}
    node_by_uid = {str(node["uid"]): node for node in nodes}

    for node in nodes:
        node["vector"] = sparse_vector(
            [
                (node["title"], 4.0),
                (" ".join(node["keywords"]), 6.0),
                (" ".join(node["aliases"]), 5.0),
                (" ".join(node["record_keys"]), 3.0),
            ],
            dimensions,
        )

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
                "vector": sparse_vector(
                    [
                        (cell["title"], 5.0),
                        (" ".join(cell.get("keywords") or []), 7.0),
                        (" ".join(cell.get("aliases") or []), 6.0),
                        (" ".join(record_by_id[value]["key"] for value in member_record_ids), 3.0),
                    ],
                    dimensions,
                ),
            }
        )

    unassigned = sorted(set(record_by_id) - assigned_records)
    return {
        "schema_version": 1,
        "type": INDEX_TYPE,
        "memory_graph_store": str(store),
        "memory_graph_head": git_head(store),
        "taxonomy_path": str(taxonomy_path.expanduser().resolve()),
        "vectorizer": {
            "algorithm": "sha256-hashed-word-and-cjk-2-3gram",
            "dimensions": dimensions,
            "normalization": "NFKC-casefold-l2",
            "semantic_embedding": False,
            "offline": True,
        },
        "projects": catalog["projects"],
        "cells": indexed_cells,
        "nodes": nodes,
        "records": sorted(records, key=lambda value: (str(value["project_id"]), str(value["key"]))),
        "unassigned_record_ids": unassigned,
        "candidate_entries_included": False,
    }


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
    if index.get("type") != INDEX_TYPE:
        raise VectorError("unsupported vector index")
    store = Path(str(index["memory_graph_store"])).expanduser().resolve()
    if git_head(store) != index.get("memory_graph_head"):
        raise VectorError("INDEX_STALE: rebuild after the latest Memory Graph commit")
    return store


def ranked(index: dict[str, Any], query: str, limit: int, cwd: Path) -> dict[str, Any]:
    store = ensure_fresh(index)
    dimensions = int(index["vectorizer"]["dimensions"])
    query_vector = sparse_vector([(query, 1.0)], dimensions)
    if not query_vector:
        return {
            "status": "OK",
            "query": query,
            "entries": [],
            "candidate_entries_included": False,
        }

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
        boost = cell_scores[cell_id] * 0.45
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
                node_scores[related_uid] = max(node_scores[related_uid], seed_score * 0.55)

    record_meta = {str(record["id"]): record for record in index["records"]}
    record_scores: dict[str, float] = {record_id: 0.0 for record_id in record_meta}
    record_matches: dict[str, dict[str, list[str]]] = {
        record_id: {"cells": [], "nodes": []} for record_id in record_meta
    }
    for cell_id in top_cells:
        score = cell_scores[cell_id] * 0.35
        for record_id in cell_by_id[cell_id]["member_record_ids"]:
            record_scores[record_id] = max(record_scores.get(record_id, 0.0), score)
            record_matches[record_id]["cells"].append(cell_id)
    for uid, score in node_scores.items():
        if score <= 0:
            continue
        for record_id in node_by_uid[uid]["record_ids"]:
            record_scores[record_id] = max(record_scores.get(record_id, 0.0), score)
            record_matches[record_id]["nodes"].append(uid)

    ordered_records = sorted(record_scores.items(), key=lambda item: (-item[1], item[0]))
    best_record_score = ordered_records[0][1] if ordered_records else 0.0
    minimum_record_score = max(0.10, best_record_score * 0.25)
    selected = [
        record_id
        for record_id, score in ordered_records
        if score >= minimum_record_score
    ][:limit]
    active_project = current_project(index, cwd)
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
        "matched_cells": [
            {"id": cell_id, "score": round(cell_scores[cell_id], 8)} for cell_id in top_cells
        ],
        "minimum_record_score": round(minimum_record_score, 8),
        "entries": entries,
        "candidate_entries_included": False,
    }


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
    args = parser.parse_args()
    try:
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
            }
        elif args.command == "recall":
            output = ranked(load_json(Path(args.index)), args.query, args.limit, Path(args.cwd))
        else:
            output = route_upload(load_json(Path(args.index)), Path(args.export))
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (VectorError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "VECTOR_MEMORY_FAILED", "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
