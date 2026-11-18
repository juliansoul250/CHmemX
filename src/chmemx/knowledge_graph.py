"""Opt-in evidence-bound candidate graph; never an approval or execution authority."""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone
import json
import re
import unicodedata

ENTITY_TYPES = {"Project", "Tool", "Role", "Rule", "Evidence", "Concept"}

RELATIONS = {
    "uses": ({"Project"}, {"Tool"}),
    "depends_on": ({"Tool"}, {"Tool"}),
    "responsible_for": ({"Role"}, {"Project"}),
    "constrained_by": ({"Project", "Tool"}, {"Rule"}),
    "supported_by": ({"Rule"}, {"Evidence"}),
    "related_to": (None, None),
}


def _fields(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or (value.keys() - set(required) - set(optional))
    ):
        raise ValueError("GRAPH_FIELDS_INVALID")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("GRAPH_TEXT_INVALID")
    return value


def _strings(value, maximum=16):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("GRAPH_LIST_INVALID")
    for text in value:
        _text(text)
    if len(set(value)) != len(value):
        raise ValueError("GRAPH_DUPLICATE")
    return value


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("GRAPH_DATE_INVALID")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("GRAPH_DATE_INVALID") from error


def _name(value):
    return unicodedata.normalize("NFKC", _text(value)).casefold().strip()


class TypedGraph:
    def __init__(self, specification: dict, records: list[dict]):
        _fields(specification, {"schema_version", "entities", "relations"})
        if type(specification["schema_version"]) is not int or specification["schema_version"] != 1:
            raise ValueError("GRAPH_VERSION_UNSUPPORTED")
        for field, maximum in (("entities", 256), ("relations", 512)):
            if not isinstance(specification[field], list) or len(specification[field]) > maximum:
                raise ValueError("GRAPH_SIZE_INVALID")
        self.specification = copy.deepcopy(specification)
        self.records = {
            r["id"]: {"project_id": r.get("project_id"), "record_sha256": r.get("record_sha256")}
            for r in records
        }
        self.entities = {}
        for entity in self.specification["entities"]:
            _fields(
                entity,
                {"id", "type", "name", "project_id", "record_ids"},
                {"aliases", "condition_record_ids"},
            )
            identifier = _text(entity["id"])
            if identifier in self.entities:
                raise ValueError("GRAPH_DUPLICATE")
            project = entity["project_id"]
            if project is not None:
                _text(project)
            if not identifier.startswith(f"{project or 'global'}:"):
                raise ValueError("GRAPH_NAMESPACE_INVALID")
            if _text(entity["type"]) not in ENTITY_TYPES:
                raise ValueError("GRAPH_ENTITY_TYPE_INVALID")
            _text(entity["name"])
            _strings(entity.get("aliases", []))
            references = _strings(entity["record_ids"])
            if not references:
                raise ValueError("GRAPH_RECORD_REQUIRED")
            for rid in references + _strings(entity.get("condition_record_ids", [])):
                self._record(rid, project)
            self.entities[identifier] = entity
        edge_ids, triples = set(), set()
        for edge in self.specification["relations"]:
            _fields(
                edge,
                {"id", "type", "from", "to", "evidence_record_id", "evidence_sha256", "valid_from"},
                {"valid_until", "condition_record_ids"},
            )
            identifier = _text(edge["id"])
            if _text(edge["type"]) not in RELATIONS:
                raise ValueError("GRAPH_RELATION_INVALID")
            if any(_text(edge[k]) not in self.entities for k in ("from", "to")):
                raise ValueError("GRAPH_ENTITY_UNKNOWN")
            source, target = (self.entities[edge[k]] for k in ("from", "to"))
            project = source["project_id"]
            if project != target["project_id"]:
                raise ValueError("GRAPH_SCOPE_MISMATCH")
            sources, targets = RELATIONS[edge["type"]]
            if sources is not None and (
                source["type"] not in sources or target["type"] not in targets
            ):
                raise ValueError("GRAPH_ENDPOINT_TYPE_INVALID")
            evidence = self._record(_text(edge["evidence_record_id"]), project)
            sha = edge["evidence_sha256"]
            if (
                not isinstance(sha, str)
                or not re.fullmatch(r"[0-9a-f]{64}", sha)
                or (sha != evidence["record_sha256"])
            ):
                raise ValueError("GRAPH_EVIDENCE_MISMATCH")
            start = _date(edge["valid_from"])
            end = _date(edge["valid_until"]) if edge.get("valid_until") is not None else None
            if end is not None and start >= end:
                raise ValueError("GRAPH_DATE_INVALID")
            for rid in _strings(edge.get("condition_record_ids", [])):
                self._record(rid, project)
            pair = (edge["from"], edge["to"])
            if edge["type"] == "related_to":
                pair = tuple(sorted(pair))
            triple = (edge["type"], *pair, start, end)
            if identifier in edge_ids or triple in triples:
                raise ValueError("GRAPH_DUPLICATE")
            edge_ids.add(identifier)
            triples.add(triple)

    def _record(self, identifier, project):
        if identifier not in self.records:
            raise ValueError("GRAPH_RECORD_UNKNOWN")
        record = self.records[identifier]
        if record.get("project_id") != project:
            raise ValueError("GRAPH_SCOPE_MISMATCH")
        return record

    def paths(
        self,
        entity: str,
        project_id: str | None,
        relation: str,
        *,
        direction="outgoing",
        max_hops=1,
        at=None,
        limit=3,
        max_edges=64,
    ):
        if project_id is not None:
            _text(project_id)
        if not isinstance(relation, str) or relation not in RELATIONS:
            raise ValueError("GRAPH_RELATION_INVALID")
        if (
            not isinstance(direction, str)
            or direction not in {"incoming", "outgoing"}
            or type(max_hops) is not int
            or not 1 <= max_hops <= 2
            or type(limit) is not int
            or not 1 <= limit <= 8
            or type(max_edges) is not int
            or not 1 <= max_edges <= 512
        ):
            raise ValueError("GRAPH_QUERY_INVALID")
        moment = _date(at) if at is not None else datetime.now(timezone.utc).date()
        name = _name(entity)
        allowed = [e for e in self.entities.values() if e["project_id"] in {None, project_id}]
        exact = [e for e in allowed if e["id"] == entity]
        anchors = exact or [
            e
            for e in allowed
            if name in {_name(e["name"]), *(_name(a) for a in e.get("aliases", []))}
        ]
        result = {
            "status": "OK",
            "paths": [],
            "scanned_edges": 0,
            "truncated": False,
            "execution_authorized": False,
            "authority": "candidate_projection",
        }
        if not anchors:
            return {**result, "status": "NO_ENTITY"}
        if len(anchors) != 1:
            return {
                **result,
                "status": "ENTITY_AMBIGUOUS",
                "candidate_ids": sorted(e["id"] for e in anchors),
            }
        anchor = anchors[0]
        result["anchor_id"] = anchor["id"]
        frontier = [([anchor["id"]], [])]
        while frontier:
            visited, path = frontier.pop(0)
            for edge in sorted(self.specification["relations"], key=lambda e: e["id"]):
                if edge["type"] != relation:
                    continue
                start, end = (edge[k] for k in ("from", "to"))
                if direction == "incoming":
                    start, end = end, start
                if relation == "related_to" and end == visited[-1]:
                    start, end = end, start
                if start != visited[-1]:
                    continue
                if result["scanned_edges"] >= max_edges:
                    return {**result, "truncated": True}
                result["scanned_edges"] += 1
                if (
                    end in visited
                    or self.entities[end]["project_id"] != anchor["project_id"]
                    or moment < _date(edge["valid_from"])
                    or (
                        edge.get("valid_until") is not None and moment >= _date(edge["valid_until"])
                    )
                ):
                    continue
                entity_ids, edges = visited + [end], path + [edge]
                required, conditions = set(), set()
                for uid in entity_ids:
                    required.update(self.entities[uid]["record_ids"])
                    conditions.update(self.entities[uid].get("condition_record_ids", []))
                for link in edges:
                    required.add(link["evidence_record_id"])
                    conditions.update(link.get("condition_record_ids", []))
                result["paths"].append(
                    {
                        "entity_ids": entity_ids,
                        "edges": copy.deepcopy(edges),
                        "required_record_ids": sorted(required | conditions),
                        "constraint_record_ids": sorted(conditions),
                        "authority": "candidate_projection",
                    }
                )
                if len(result["paths"]) >= limit:
                    return {**result, "truncated": True}
                if len(edges) < max_hops:
                    frontier.append((entity_ids, edges))
        return result


class ExperimentalGraphRetriever:
    """Compose explicit graph lookup with the existing verified record reader.

    No sidecar relation becomes accepted memory or execution permission. The
    caller resolves structured query intent; plain recall remains unchanged.
    """

    def __init__(self, index: dict, specification: dict | None = None):
        from chmemx.scripts.retrieval_v3 import Retriever

        self.reader = Retriever(index)
        self.graph = (
            TypedGraph(specification, index["records"]) if specification is not None else None
        )

    def recall(self, query: str, cwd, limit=5, *, graph_query: dict | None = None):
        if graph_query is None:
            return self.reader.recall(query, cwd, limit)
        if self.graph is None:
            raise ValueError("GRAPH_NOT_CONFIGURED")
        _fields(
            graph_query,
            {"entity", "relation"},
            {
                "direction",
                "max_hops",
                "at",
                "limit",
                "max_edges",
                "max_bundle_bytes",
                "max_evidence_reads",
            },
        )
        if not isinstance(query, str) or len(query) > 8192:
            raise ValueError("QUERY_INVALID")
        maximum = graph_query.get("max_bundle_bytes", 65536)
        read_budget = graph_query.get("max_evidence_reads", 16)
        if (
            type(maximum) is not int
            or not 32 <= maximum <= 4194304
            or type(read_budget) is not int
            or not 1 <= read_budget <= 64
        ):
            raise ValueError("GRAPH_QUERY_INVALID")
        self.reader.verify()
        plan = self.graph.paths(
            graph_query["entity"],
            self.reader.project(cwd),
            graph_query["relation"],
            **{
                k: v
                for k, v in graph_query.items()
                if k not in {"entity", "relation", "max_bundle_bytes", "max_evidence_reads"}
            },
        )
        cache, records, paths, blocked, freshness_cache = {}, {}, [], [], {}

        def fetch(identifier):
            if identifier in cache:
                return cache[identifier]
            if len(cache) >= read_budget:
                return None, "EVIDENCE_READ_BUDGET_EXCEEDED"
            # IDs are already scope-bound by TypedGraph. Read exact verified bytes;
            # do not run a complete semantic search for every bundle member.
            record = self.reader.read(identifier, freshness_cache)
            if record and record["source_freshness"]["current_use_allowed"]:
                cache[identifier] = record, None
            else:
                cache[identifier] = None, "SOURCE_NOT_CURRENT"
            return cache[identifier]

        for path in plan["paths"]:
            bundle, reason = {}, None
            for identifier in path["required_record_ids"]:
                record, reason = fetch(identifier)
                if reason:
                    break
                bundle[identifier] = record
            combined = {**records, **bundle}
            if reason is None and len(json.dumps(combined, ensure_ascii=False).encode()) > maximum:
                reason = "BUNDLE_BUDGET_EXCEEDED"
            if reason:
                blocked.append({"edge_ids": [e["id"] for e in path["edges"]], "reason": reason})
            else:
                paths.append(path)
                records = combined
        from chmemx.scripts import source_freshness

        changed = set()
        rechecks = len(records)
        for identifier, record in records.items():
            freshness = source_freshness.check(record)
            if not freshness["current_use_allowed"]:
                changed.add(identifier)
            else:
                record["source_freshness"] = freshness
        retained = []
        for path in paths:
            if changed.intersection(path["required_record_ids"]):
                blocked.append(
                    {
                        "edge_ids": [e["id"] for e in path["edges"]],
                        "reason": "SOURCE_CHANGED_DURING_QUERY",
                    }
                )
            else:
                retained.append(path)
        paths = retained
        used = {rid for path in paths for rid in path["required_record_ids"]}
        records = {rid: record for rid, record in records.items() if rid in used}
        self.reader.verify()
        from chmemx.scripts.retrieval_v3 import NOTICE

        return {
            "status": "OK",
            "entries": [],
            "associations": [],
            "needs_review": [],
            "content_type": "untrusted_historical_memory",
            "notice": NOTICE,
            "candidate_entries_included": False,
            "graph": {
                **plan,
                "paths": paths,
                "records": records,
                "blocked_paths": blocked,
                "evidence_reads": len(cache),
                "source_rechecks": rechecks,
                "source_checks": True,
                "candidate_relations_included": True,
                "constraint_completeness": "declared-only; unknown constraints are not absent",
            },
        }
