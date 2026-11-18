"""Caller-understood plans, not an NLP interpreter or an authorization grant."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re

TEXT = {"type": "string", "minLength": 1, "maxLength": 8192}
SPANS = {"type": "array", "maxItems": 12, "uniqueItems": True,
         "items": {"type": "string", "minLength": 1, "maxLength": 512}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["version", "query", "decision", "goal", "entities", "constraints", "facets"],
    "properties": {
        "version": {"const": 1}, "query": TEXT,
        "decision": {"enum": ["search", "clarify", "abstain"]}, "goal": TEXT,
        "entities": SPANS, "constraints": SPANS,
        "facets": {"type": "array", "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "query"],
            "properties": {"id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,31}$"},
                           "query": TEXT}}},
    },
}


def _text(value, maximum=8192):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("QUERY_PLAN_INVALID: bounded nonempty text required")
    return value


def validate(query, plan, *, limit=5, association_limit=3, body_limit=None):
    if not isinstance(query, str) or len(query) > 8192:
        raise ValueError("QUERY_INVALID")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("LIMIT_INVALID")
    if type(association_limit) is not int or not 0 <= association_limit <= 8:
        raise ValueError("ASSOCIATION_LIMIT_INVALID")
    if body_limit is not None and (type(body_limit) is not int or not 256 <= body_limit <= 16384):
        raise ValueError("BODY_LIMIT_INVALID")
    if plan is None:
        return None
    if not isinstance(plan, dict) or set(plan) != set(SCHEMA["required"]):
        raise ValueError("QUERY_PLAN_INVALID: fields")
    if type(plan["version"]) is not int or plan["version"] != 1:
        raise ValueError("QUERY_PLAN_INVALID: version")
    if _text(plan["query"]) != query:
        raise ValueError("QUERY_PLAN_INVALID: original query mismatch")
    if plan["decision"] not in ("search", "clarify", "abstain"):
        raise ValueError("QUERY_PLAN_INVALID: decision")
    _text(plan["goal"])
    spans = []
    for field in ("entities", "constraints"):
        values = plan[field]
        if not isinstance(values, list) or len(values) > 12:
            raise ValueError("QUERY_PLAN_INVALID: spans")
        for value in values:
            if _text(value, 512) not in query:
                raise ValueError("QUERY_PLAN_INVALID: invented span")
        if len(set(values)) != len(values):
            raise ValueError("QUERY_PLAN_INVALID: duplicate span")
        spans.extend(values)
    facets = plan["facets"]
    if not isinstance(facets, list) or len(facets) > 3:
        raise ValueError("QUERY_PLAN_INVALID: facet budget")
    seen, texts = {"original"}, set()
    for facet in facets:
        if not isinstance(facet, dict) or set(facet) != {"id", "query"}:
            raise ValueError("QUERY_PLAN_INVALID: facet fields")
        identifier = _text(facet["id"], 32)
        text = _text(facet["query"])
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", identifier) or identifier in seen or text in texts:
            raise ValueError("QUERY_PLAN_INVALID: duplicate or invalid facet")
        if any(span not in text for span in spans):
            raise ValueError("QUERY_PLAN_INVALID: facet drops preserved span")
        seen.add(identifier)
        texts.add(text)
    if plan["decision"] != "search" and facets:
        raise ValueError("QUERY_PLAN_INVALID: non-search plan has facets")
    return copy.deepcopy(plan)


def decision_result(plan):
    if plan is not None and plan["decision"] == "search":
        return None
    status = "NEEDS_UNDERSTANDING" if plan is None else {
        "clarify": "NEEDS_CLARIFICATION", "abstain": "ABSTAIN"}[plan["decision"]]
    return {"status": status, "entries": [], "associations": [], "needs_review": [],
            "understanding": {"status": "missing" if plan is None else "caller_interpreted",
                              "interpretation_verified": False, "answer_support": "not_assessed"},
            "notice": "Caller must understand the request first. No search or model inference performed."}


def load(path):
    """Bounded explicit local plan input, not a model/provider configuration."""
    with Path(path).open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("QUERY_PLAN_INVALID: file exceeds 65536 bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("QUERY_PLAN_INVALID: duplicate JSON field")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)
