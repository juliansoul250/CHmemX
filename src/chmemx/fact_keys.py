"""Fact identity directory. Aliases suggest review, never silently merge facts."""

from __future__ import annotations
from .runtime import simple_memory as core


def directory(runtime, cwd, query="", limit=30):
    if (
        not isinstance(query, str)
        or len(query) > 8192
        or type(limit) != int
        or not 1 <= limit <= 100
    ):
        raise ValueError("FACT_QUERY_INVALID")
    core.ensure_clean(runtime.store)
    pid, _ = runtime.project_for_cwd(cwd)
    rows = {}
    for r in runtime.active_records(pid):
        ident = (r.get("project_id"), r["scope"], r["key"])
        row = rows.setdefault(
            ident,
            {
                "key": r["key"],
                "scope": r["scope"],
                "project_id": r.get("project_id"),
                "allowed_classes": [],
                "aliases": [],
                "description": r["body"][:160],
                "description_kind": "record excerpt; not an independently defined fact",
            },
        )
        row["allowed_classes"].append(r["class"])
    path = runtime.store / "fact-key-catalog.json"
    if path.exists():
        payload = core.load_json(path)
        facts = payload.get("facts")
        if not isinstance(facts, list) or len(facts) > 2000:
            raise ValueError("FACT_CATALOG_INVALID")
        seen = set()
        for row in facts:
            if not isinstance(row, dict) or not all(
                k in row for k in ("key", "scope", "allowed_classes", "aliases", "description")
            ):
                raise ValueError("FACT_CATALOG_INVALID")
            if not isinstance(row["aliases"], list) or not isinstance(row["allowed_classes"], list):
                raise ValueError("FACT_CATALOG_INVALID")
            core.canonical_key(row["key"])
            if (
                row["scope"] not in {"global", "project"}
                or not row["allowed_classes"]
                or any(c not in core.ALLOWED_CLASSES for c in row["allowed_classes"])
                or not isinstance(row["description"], str)
                or not 0 < len(row["description"]) <= 1024
                or len(row["aliases"]) > 32
            ):
                raise ValueError("FACT_CATALOG_INVALID")
            if row["scope"] == "global":
                if row.get("project_id") is not None or row["allowed_classes"] != ["preference"]:
                    raise ValueError("FACT_CATALOG_INVALID")
            else:
                core.safe_id(row.get("project_id"), "fact project id")
            for alias in [row["key"], *row["aliases"]]:
                if core.canonical_key(alias) != alias:
                    raise ValueError("FACT_CATALOG_NONCANONICAL_KEY")
                identity = (row.get("project_id"), row["scope"], alias)
                if identity in seen:
                    raise ValueError("FACT_ALIAS_AMBIGUOUS")
                seen.add(identity)
            if row.get("project_id") not in (None, pid):
                continue
            rows[(row.get("project_id"), row["scope"], row["key"])] = {
                **row,
                "description_kind": "declared fact definition",
            }
    q = query.casefold()
    selected = [
        r
        for r in rows.values()
        if not q or q in r["key"].casefold() or any(q in a.casefold() for a in r["aliases"])
    ]
    return sorted(selected, key=lambda r: (r.get("project_id") or "", r["key"]))[:limit]


def conflicts(runtime, cwd, key, scope, memory_class):
    return [
        r
        for r in directory(runtime, cwd, key, 100)
        if r["scope"] == scope
        and ((key == r["key"] and memory_class not in r["allowed_classes"]) or key in r["aliases"])
    ]
