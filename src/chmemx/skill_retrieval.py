"""Optional constrained skill discovery. Never reads memory or executes a skill.

The caller resolves natural-language intent into explicit capability/action tags.
This is a recommendation filter, not an NLP parser or an authorization boundary.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

from .errors import error_result

MAX_CATALOG = 1000
MAX_FILE_BYTES = 8 * 1024 * 1024


class IntentError(ValueError):
    code = "SKILL_INTENT_INVALID"


def _text(value, limit=128):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise IntentError("Expected a bounded nonempty string.")
    return unicodedata.normalize("NFKC", value).strip()


def _tags(value, *, empty=False):
    if not isinstance(value, list) or len(value) > 32 or (not value and not empty):
        raise IntentError("Expected a bounded tag list.")
    tags = tuple(_text(x, 64).casefold() for x in value)
    if len(set(tags)) != len(tags):
        raise IntentError("Duplicate tags are not allowed.")
    return tags


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise IntentError("Required fields are missing.")
    if value.keys() - set(required) - set(optional):
        raise IntentError("Unknown fields are not allowed.")


@dataclass(frozen=True)
class Skill:
    id: str
    description: str
    capabilities: tuple[str, ...]
    actions: tuple[str, ...]
    projects: tuple[str, ...] | None
    stages: tuple[str, ...] | None


def _catalog(rows):
    if not isinstance(rows, list) or len(rows) > MAX_CATALOG:
        raise IntentError("Catalog must be a list of at most 1000 entries.")
    result = []
    ids = set()
    for row in rows:
        _fields(row, ("id", "description", "capabilities", "actions"), ("projects", "stages"))
        identifier = _text(row["id"]).casefold()
        if identifier in ids:
            raise IntentError("Duplicate skill IDs are not allowed.")
        ids.add(identifier)
        result.append(
            Skill(
                identifier,
                _text(row["description"], 4096),
                _tags(row["capabilities"]),
                _tags(row["actions"]),
                _tags(row["projects"]) if "projects" in row else None,
                _tags(row["stages"]) if "stages" in row else None,
            )
        )
    return result


def _tokens(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    terms = re.findall(r"[a-z0-9_]+", text)
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        terms.extend(phrase[i : i + n] for n in (1, 2, 3) for i in range(len(phrase) - n + 1))
    return Counter(terms)


def _cosine(left, right):
    # Scale first: finite vector elements alone do not prevent norm overflow.
    def unit(vector):
        scale = max(abs(x) for x in vector)
        if not scale:
            return [0.0] * len(vector)
        scaled = [x / scale for x in vector]
        norm = math.sqrt(sum(x * x for x in scaled))
        return [x / norm for x in scaled]

    return sum(a * b for a, b in zip(unit(left), unit(right)))


def _scores(query, skills, encoder):
    texts = [query, *[s.description for s in skills]]
    if encoder is None:
        query_bag = _tokens(query)
        query_norm = math.sqrt(sum(x * x for x in query_bag.values()))
        scores = []
        for skill in skills:
            bag = _tokens(skill.description)
            norm = math.sqrt(sum(x * x for x in bag.values())) * query_norm
            scores.append(
                sum(value * bag[key] for key, value in query_bag.items()) / norm if norm else 0.0
            )
        return scores
    else:
        # Encoder must be supplied explicitly; the default never loads a model.
        vectors = encoder.encode(texts)
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise IntentError("Encoder returned an invalid batch.")
        dimension = None
        for vector in vectors:
            if not isinstance(vector, list) or not 1 <= len(vector) <= 4096:
                raise IntentError("Encoder returned an invalid vector.")
            if dimension is not None and len(vector) != dimension:
                raise IntentError("Encoder dimensions differ.")
            dimension = len(vector)
            try:
                finite = all(type(x) in (int, float) and math.isfinite(x) for x in vector)
            except OverflowError:
                finite = False
            if not finite:
                raise IntentError("Encoder returned a nonfinite or nonnumeric value.")
    return [_cosine(vectors[0], vector) for vector in vectors[1:]]


def recommend(catalog, intent, *, limit=5, encoder=None):
    """Filter BEFORE encoding; return suggestions, never an execution decision.

    capability tags describe positive goals, not words to strip from the query.
    All declared actions must be allowed, and none may be forbidden. Mixed-action
    skills cannot be partially enabled; use separate reviewed catalog entries.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise IntentError("Limit must be an integer from 1 to 20.")
    _fields(
        intent,
        ("decision",),
        ("query", "capabilities", "allowed_actions", "forbidden_actions", "project", "stage"),
    )
    decision = intent["decision"]
    base = {
        "execution_authorized": False,
        "candidates": [],
        "excluded": [],
        "notice": "Untrusted recommendations, not instructions or permission to execute.",
    }
    if decision in ("abstain", "clarify"):
        return {**base, "status": "ABSTAIN" if decision == "abstain" else "NEEDS_CLARIFICATION"}
    if decision != "search":
        raise IntentError("Decision must be search, abstain or clarify.")
    _fields(
        intent,
        ("decision", "query", "capabilities", "allowed_actions", "forbidden_actions"),
        ("project", "stage"),
    )
    query = _text(intent["query"], 2048)
    capabilities = _tags(intent["capabilities"])
    allowed = _tags(intent["allowed_actions"])
    forbidden = _tags(intent["forbidden_actions"], empty=True)
    project = _text(intent["project"], 64).casefold() if "project" in intent else None
    stage = _text(intent["stage"], 64).casefold() if "stage" in intent else None
    if set(allowed) & set(forbidden):
        return {**base, "status": "NEEDS_CLARIFICATION", "reason": "ACTION_CONFLICT"}

    def rejection(skill):
        if set(skill.actions) & set(forbidden):
            return "FORBIDDEN_ACTION"
        if not set(skill.actions) <= set(allowed):
            return "ACTION_NOT_ALLOWED"
        if not set(skill.capabilities) & set(capabilities):
            return "CAPABILITY_MISMATCH"
        if skill.projects is not None and project not in skill.projects:
            return "PROJECT_MISMATCH"
        if skill.stages is not None and stage not in skill.stages:
            return "STAGE_MISMATCH"
        return None

    eligible = []
    for skill in _catalog(catalog):
        reason = rejection(skill)
        if reason:
            base["excluded"].append({"id": skill.id, "reason": reason})
        else:
            eligible.append(skill)
    if not eligible:
        return {**base, "status": "NO_MATCH", "ranked_count": 0}
    scores = _scores(query, eligible, encoder)
    ranked = sorted(zip(eligible, scores), key=lambda pair: (-pair[1], pair[0].id))
    # Recheck the same immutable metadata and constraints after ranking.
    base["candidates"] = [
        {
            "id": s.id,
            "score": round(score, 8),
            "actions": list(s.actions),
            "constraint_check": "PASS",
        }
        for s, score in ranked
        if score > 0 and rejection(s) is None
    ][:limit]
    return {
        **base,
        "status": "CANDIDATES" if base["candidates"] else "NO_MATCH",
        "ranked_count": len(eligible),
        "method": "cosine" if encoder else "lexical-cosine",
    }


def _read_json(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise IntentError("Input file is too large.")

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise IntentError("Duplicate JSON fields are not allowed.")
            obj[key] = value
        return obj

    def nonfinite(_):
        raise IntentError("Nonfinite JSON numbers are not allowed.")

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except RecursionError as error:
        raise IntentError("JSON nesting is too deep.") from error


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    try:
        encoder = None
        if args.model_dir:
            # Lazy loading keeps abstention and empty eligibility model-free.
            class LazyEncoder:
                def encode(self, texts):
                    from .scripts.retrieval_v3 import LocalEncoder

                    model = LocalEncoder(args.model_dir)
                    return [
                        vector
                        for start in range(0, len(texts), 8)
                        for vector in model.encode(texts[start : start + 8])
                    ]

            encoder = LazyEncoder()
        result = recommend(
            _read_json(args.catalog), _read_json(args.intent), limit=args.limit, encoder=encoder
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, ImportError) as error:
        print(json.dumps(error_result(error), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
