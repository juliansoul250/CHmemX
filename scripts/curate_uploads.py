#!/usr/bin/env python3
"""Combine source-Agent uploads into one curator-owned pending import inventory.

This script is read-only with respect to the canonical Memory Graph. It validates
source exports, removes exact duplicates, blocks conflicting canonical identities,
preserves origin metadata in a redacted report, and attributes the resulting
inventory to one curator Agent for later import, review, Owner confirmation, and
Git commit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import unicodedata

try:
    from .assemble_inventory import (
        ExportError, INVENTORY_TYPE, assemble, atomic_json, canonical_json,
        load_export, safe_id, sha256_bytes,
    )
except ImportError:
    from assemble_inventory import (
        ExportError, INVENTORY_TYPE, assemble, atomic_json, canonical_json,
        load_export, safe_id, sha256_bytes,
    )


class CurationError(RuntimeError):
    pass


def iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def identity(candidate: dict[str, Any]) -> tuple[str, str | None, str, str]:
    source = candidate.get("source")
    project_root = None
    if isinstance(source, dict) and source.get("kind") == "git":
        project_root = str(source.get("project_root"))
    return (
        str(candidate.get("scope")),
        project_root,
        str(candidate.get("class")),
        str(candidate.get("key") or "").casefold(),
    )


def candidate_core(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key != "submitted_by_agent"
    }


def candidate_keywords(candidate: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for node in candidate.get("nodes") or []:
        for field in ("keywords", "aliases"):
            values.update(str(value).casefold() for value in node.get(field) or [])
    return values


def normalize_runtime_value(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value).strip()
    if isinstance(value, list):
        return [normalize_runtime_value(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_runtime_value(item) for key, item in value.items()}
    return value


def comparable_memory(value: dict[str, Any]) -> dict[str, Any]:
    output = {
        field: normalize_runtime_value(value.get(field))
        for field in (
            "scope", "class", "key", "body", "source", "nodes", "valid_from", "stale_when"
        )
    }
    output["key"] = " ".join(str(output["key"] or "").casefold().split())
    return output


def load_active_records(store: Path) -> tuple[dict[tuple[str, str | None, str, str], dict[str, Any]], dict[str, str]]:
    catalog_path = store / "catalog.json"
    if not catalog_path.is_file():
        raise CurationError("Memory Graph catalog is unavailable")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    projects = catalog.get("projects")
    if not isinstance(projects, dict):
        raise CurationError("Memory Graph project catalog is malformed")
    root_to_project = {
        str(Path(str(value["root"])).expanduser().resolve()): str(project_id)
        for project_id, value in projects.items()
        if isinstance(value, dict) and value.get("root")
    }

    records: dict[tuple[str, str | None, str, str], dict[str, Any]] = {}
    locations: list[tuple[Path, str | None]] = [(store / "global", None)]
    locations.extend(
        (store / "projects" / str(project_id), str(project_id))
        for project_id in projects
    )
    for scope_root, expected_project in locations:
        if not scope_root.is_dir():
            continue
        active_index_path = scope_root / "active-index.json"
        if not active_index_path.is_file():
            raise CurationError(f"Active index is unavailable: {scope_root}")
        active_index = json.loads(active_index_path.read_text(encoding="utf-8"))
        entries = active_index.get("entries")
        if not isinstance(entries, dict):
            raise CurationError(f"Active index is malformed: {scope_root}")
        for canonical_identity, record_id_value in sorted(entries.items()):
            record_id = str(record_id_value)
            path = scope_root / "active" / f"{record_id}.json"
            if not path.is_file():
                raise CurationError(f"Active index points to missing record: {record_id}")
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("authority") != "accepted" or record.get("status") != "active":
                raise CurationError(f"Active index points to non-active record: {record_id}")
            project_id = str(record["project_id"]) if record.get("project_id") else None
            if project_id != expected_project:
                raise CurationError(f"Active record project mismatch: {path}")
            expected_identity = f"{record.get('class')}:{record.get('key')}"
            if canonical_identity != expected_identity or record.get("id") != record_id:
                raise CurationError(f"Active index identity mismatch: {record_id}")
            record_identity = (
                str(record.get("scope")),
                project_id,
                str(record.get("class")),
                str(record.get("key") or "").casefold(),
            )
            if record_identity in records:
                raise CurationError("Active catalog contains duplicate canonical identity")
            records[record_identity] = record
    return records, root_to_project


def active_identity(
    candidate: dict[str, Any], root_to_project: dict[str, str]
) -> tuple[str, str | None, str, str]:
    project_id: str | None = None
    if candidate.get("scope") == "project":
        source = candidate.get("source")
        if not isinstance(source, dict) or source.get("kind") != "git":
            raise CurationError("Project candidate is missing Git source")
        project_root = str(Path(str(source["project_root"])).expanduser().resolve())
        project_id = root_to_project.get(project_root)
        if project_id is None:
            raise CurationError(f"Project is not registered in Memory Graph: {project_root}")
    return (
        str(candidate.get("scope")),
        project_id,
        str(candidate.get("class")),
        str(candidate.get("key") or "").casefold(),
    )


def curate(
    export_paths: list[Path], curator_agent_id: str, curation_id: str,
    active_store: Path | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    curator_agent_id = safe_id(curator_agent_id, "curator agent id")
    curation_id = safe_id(curation_id, "curation id")
    if not export_paths:
        raise CurationError("at least one source export is required")

    grouped: dict[tuple[str, str | None, str, str], list[dict[str, Any]]] = {}
    rejected: list[dict[str, str]] = []
    source_exports: list[dict[str, Any]] = []
    scopes: set[str] = set()
    project_roots: set[str] = set()
    active_records: dict[tuple[str, str | None, str, str], dict[str, Any]] = {}
    root_to_project: dict[str, str] = {}
    if active_store is not None:
        active_records, root_to_project = load_active_records(active_store.expanduser().resolve())

    for export_path in export_paths:
        payload = load_export(export_path)
        inventory, source_report = assemble(payload)
        source_agent = str(source_report["agent_id"])
        export_id = str(source_report["export_id"])
        source_exports.append(
            {
                "agent_id": source_agent,
                "export_id": export_id,
                "path": str(export_path.expanduser().resolve()),
                "accepted_count": int(source_report["accepted_count"]),
                "rejected_count": int(source_report["rejected_count"]),
            }
        )
        for item in source_report.get("rejected") or []:
            rejected.append(
                {
                    "origin_agent": source_agent,
                    "export_id": export_id,
                    "export_item_id": str(item.get("export_item_id") or "source-rejected"),
                    "reason": str(item.get("reason") or "source-rejected"),
                }
            )
        if inventory is None:
            continue
        for candidate in inventory["candidates"]:
            candidate = dict(candidate)
            scopes.add(str(candidate["scope"]))
            source = candidate.get("source")
            if isinstance(source, dict) and source.get("kind") == "git":
                project_roots.add(str(source["project_root"]))
            grouped.setdefault(identity(candidate), []).append(
                {
                    "candidate": candidate,
                    "origin_agent": source_agent,
                    "export_id": export_id,
                }
            )

    if len(scopes) > 1:
        raise CurationError("one curation run cannot mix global and project scope")
    if len(project_roots) > 1:
        raise CurationError("one curation run cannot mix project roots")

    curated: list[dict[str, Any]] = []
    origins: list[dict[str, Any]] = []
    conflict_identities: set[tuple[str, str | None, str, str]] = set()
    for item_identity, variants in sorted(grouped.items()):
        cores = {canonical_json(candidate_core(value["candidate"])) for value in variants}
        if len(cores) != 1:
            conflict_identities.add(item_identity)
            for value in variants:
                rejected.append(
                    {
                        "origin_agent": str(value["origin_agent"]),
                        "export_id": str(value["export_id"]),
                        "export_item_id": str(value["candidate"].get("key") or "conflict"),
                        "reason": "canonical-conflict: curator review required",
                    }
                )
            continue
        candidate = dict(variants[0]["candidate"])
        candidate["submitted_by_agent"] = curator_agent_id
        curated.append(candidate)
        origins.append(
            {
                "key": candidate["key"],
                "origin_agents": sorted({str(value["origin_agent"]) for value in variants}),
                "source_exports": sorted({str(value["export_id"]) for value in variants}),
                "exact_duplicates_collapsed": len(variants) - 1,
            }
        )

    node_definitions: dict[str, bytes] = {}
    node_conflict_keys: set[str] = set()
    for candidate in curated:
        for node in candidate.get("nodes") or []:
            node_id = str(node["id"])
            definition = canonical_json(node)
            existing = node_definitions.get(node_id)
            if existing is not None and existing != definition:
                node_conflict_keys.add(str(candidate["key"]))
            else:
                node_definitions[node_id] = definition
    if node_conflict_keys:
        kept: list[dict[str, Any]] = []
        for candidate in curated:
            if str(candidate["key"]) in node_conflict_keys:
                rejected.append(
                    {
                        "origin_agent": "multiple",
                        "export_id": curation_id,
                        "export_item_id": str(candidate["key"]),
                        "reason": "node-conflict: curator review required",
                    }
                )
            else:
                kept.append(candidate)
        curated = kept
        origins = [value for value in origins if str(value["key"]) not in node_conflict_keys]

    active_conflicts: list[dict[str, Any]] = []
    duplicate_active_keys: list[str] = []
    if active_store is not None:
        origin_by_key = {str(value["key"]): value for value in origins}
        kept_after_active_review: list[dict[str, Any]] = []
        for candidate in curated:
            candidate_identity = active_identity(candidate, root_to_project)
            existing = active_records.get(candidate_identity)
            if existing is None:
                kept_after_active_review.append(candidate)
                continue
            candidate_view = comparable_memory(candidate)
            existing_view = comparable_memory(existing)
            if candidate_view == existing_view:
                duplicate_active_keys.append(str(candidate["key"]))
                rejected.append(
                    {
                        "origin_agent": ",".join(origin_by_key[str(candidate["key"])]["origin_agents"]),
                        "export_id": curation_id,
                        "export_item_id": str(candidate["key"]),
                        "reason": "duplicate-active: no write required",
                    }
                )
                continue
            differing_fields = [
                field for field in candidate_view if candidate_view[field] != existing_view[field]
            ]
            body_diff = list(
                difflib.unified_diff(
                    str(existing_view.get("body") or "").splitlines(),
                    str(candidate_view.get("body") or "").splitlines(),
                    fromfile="current-active",
                    tofile="incoming-pending",
                    lineterm="",
                )
            )
            conflict_id = "conflict-" + hashlib.sha256(
                canonical_json(
                    {
                        "identity": candidate_identity,
                        "active_record_id": existing.get("id"),
                        "incoming": candidate_view,
                    }
                )
            ).hexdigest()[:24]
            active_conflicts.append(
                {
                    "conflict_id": conflict_id,
                    "canonical_identity": {
                        "scope": candidate_identity[0],
                        "project_id": candidate_identity[1],
                        "class": candidate_identity[2],
                        "key": candidate_identity[3],
                    },
                    "differing_fields": differing_fields,
                    "current_active": {
                        "record_id": existing.get("id"),
                        "body": existing_view.get("body"),
                        "source": existing_view.get("source"),
                        "nodes": existing_view.get("nodes"),
                        "valid_from": existing_view.get("valid_from"),
                        "stale_when": existing_view.get("stale_when"),
                    },
                    "incoming_pending": {
                        "body": candidate_view.get("body"),
                        "source": candidate_view.get("source"),
                        "nodes": candidate_view.get("nodes"),
                        "valid_from": candidate_view.get("valid_from"),
                        "stale_when": candidate_view.get("stale_when"),
                        "origins": origin_by_key.get(str(candidate["key"]), {}),
                    },
                    "body_unified_diff": body_diff,
                    "curator_recommendation": "MANUAL_ANALYSIS_REQUIRED",
                    "owner_options": [
                        "KEEP_CURRENT_ACTIVE",
                        "SUPERSEDE_WITH_INCOMING",
                        "REWRITE_MERGED_CANDIDATE",
                        "KEEP_SEPARATE_WITH_NEW_KEY_AND_RELATION"
                    ],
                    "write_blocked_until_owner_decision": True,
                }
            )
            rejected.append(
                {
                    "origin_agent": ",".join(origin_by_key[str(candidate["key"])]["origin_agents"]),
                    "export_id": curation_id,
                    "export_item_id": str(candidate["key"]),
                    "reason": "conflict-with-active: Owner decision required",
                }
            )
        curated = kept_after_active_review
        origins = [value for value in origins if str(value["key"]) not in {
            *duplicate_active_keys,
            *(str(conflict["canonical_identity"]["key"]) for conflict in active_conflicts),
        }]

    semantic_overlap_candidates: list[dict[str, Any]] = []
    for index, left in enumerate(curated):
        left_words = candidate_keywords(left)
        for right in curated[index + 1 :]:
            right_words = candidate_keywords(right)
            shared = sorted(left_words & right_words)
            union = left_words | right_words
            if len(shared) >= 3 and union and len(shared) / len(union) >= 0.4:
                semantic_overlap_candidates.append(
                    {
                        "left_key": left["key"],
                        "right_key": right["key"],
                        "shared_keywords": shared,
                        "action": "manual-review-do-not-auto-merge",
                    }
                )

    report: dict[str, Any] = {
        "schema_version": 1,
        "type": "memorygraph-curation-report-v1",
        "curation_id": curation_id,
        "curator_agent_id": curator_agent_id,
        "created_at": iso(),
        "source_exports": source_exports,
        "accepted_count": len(curated),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "origins": origins,
        "conflicting_identity_count": len(conflict_identities),
        "semantic_overlap_candidates": semantic_overlap_candidates,
        "duplicate_active_keys": sorted(duplicate_active_keys),
        "active_conflict_count": len(active_conflicts),
        "active_conflicts": active_conflicts,
        "bodies_included": bool(active_conflicts),
        "canonical_store_modified": False,
        "owner_decision_format": "确认记忆冲突 <conflict-id> <decision> <review-digest>",
    }
    report["review_digest"] = sha256_bytes(canonical_json(report))
    if not curated:
        report["status"] = "NO_IMPORTABLE_CANDIDATES"
        return None, report

    inventory: dict[str, Any] = {
        "schema_version": 1,
        "type": INVENTORY_TYPE,
        "batch_id": curation_id,
        "created_at": report["created_at"],
        "candidates": sorted(curated, key=lambda value: str(value["key"])),
    }
    inventory["batch_digest"] = sha256_bytes(canonical_json(inventory))
    report["inventory_digest"] = inventory["batch_digest"]
    report["status"] = "CURATED_WITH_REJECTIONS" if rejected else "CURATED"
    return inventory, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="append", required=True)
    parser.add_argument("--curator-agent-id", default="main-memory-curator")
    parser.add_argument("--curation-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument(
        "--store",
        default=os.environ.get(
            "MEMORY_GRAPH_HOME", str(Path.home() / ".memory-graph" / "store")
        ),
    )
    args = parser.parse_args()
    try:
        inventory, report = curate(
            [Path(value) for value in args.export],
            args.curator_agent_id,
            args.curation_id,
            Path(args.store),
        )
        if inventory is not None:
            atomic_json(Path(args.output), inventory)
        atomic_json(Path(args.report), report)
    except (CurationError, ExportError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "CURATION_INVALID", "message": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if inventory is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
