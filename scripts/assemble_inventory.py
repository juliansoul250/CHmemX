#!/usr/bin/env python3
"""Validate a source-agent export and seal a pending Memory Graph inventory.

This script never edits the canonical Memory Graph store. Unsafe items are
reported without copying their bodies into the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any


EXPORT_TYPE = "memorygraph-agent-export-v1"
INVENTORY_TYPE = "memorygraph-pending-import-v1"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,95}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_CLASSES = {"decision", "preference", "lesson", "state", "evidence"}
MAX_EXPORT_BYTES = 24 * 1024 * 1024
MAX_BODY_BYTES = 512 * 1024

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
        r"\s*[:=]\s*[^\s]{8,}"
    ),
)
INSTRUCTION_PATTERNS = (
    re.compile(r"(?i)ignore (?:all |any )?(?:previous|prior|system) instructions"),
    re.compile(r"(?i)(?:disable|bypass) (?:security|policy|guardrails?)"),
    re.compile(r"(?i)send (?:the )?(?:secret|token|credential)"),
    re.compile(r"(?i)<\s*/?\s*system\b[^>]*>"),
)


class ExportError(RuntimeError):
    pass


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def safe_id(value: object, field: str) -> str:
    text = str(value or "")
    if not ID_RE.fullmatch(text):
        raise ExportError(f"invalid {field}")
    return text


def safe_text(value: object, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > maximum
        or any(ord(char) < 32 and char not in "\n\t" for char in text)
    ):
        raise ExportError(f"invalid {field}")
    return text


def unsafe_reasons(value: object) -> list[str]:
    serialized = canonical_json(value).decode("utf-8", errors="replace")
    reasons: list[str] = []
    if any(pattern.search(serialized) for pattern in SECRET_PATTERNS):
        reasons.append("possible-secret")
    if any(pattern.search(serialized) for pattern in INSTRUCTION_PATTERNS):
        reasons.append("embedded-instruction")
    return reasons


def load_export(path: Path) -> dict[str, Any]:
    requested = path.expanduser()
    if requested.is_symlink() or not requested.is_file():
        raise ExportError("export must be a real file, not a symlink")
    if requested.stat().st_size > MAX_EXPORT_BYTES:
        raise ExportError("export exceeds the 24 MiB limit")
    try:
        payload = json.loads(requested.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("export is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ExportError("export root must be an object")
    return payload


def run_git(root: Path, arguments: list[str]) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise ExportError("Git source verification failed")
    return completed.stdout


def validate_source(raw: object, scope: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ExportError("source must be an object")
    kind = raw.get("kind")
    if kind == "user-instruction":
        if scope != "global":
            raise ExportError("project memory requires a Git source")
        digest = str(raw.get("message_digest") or "")
        if not HASH_RE.fullmatch(digest):
            raise ExportError("user instruction digest is malformed")
        return {
            "kind": kind,
            "thread_id": safe_text(raw.get("thread_id"), "thread_id", 128),
            "message_digest": digest,
            "locator": safe_text(raw.get("locator"), "locator", 512),
        }

    if kind != "git" or scope != "project":
        raise ExportError("source must be user-instruction for global or git for project")

    project_root = Path(str(raw.get("project_root") or "")).expanduser().resolve()
    if project_root.is_symlink() or not project_root.is_dir():
        raise ExportError("project_root is unavailable")
    top = Path(run_git(project_root, ["rev-parse", "--show-toplevel"]).decode().strip()).resolve()
    if top != project_root:
        raise ExportError("project_root is not the Git toplevel")

    relative_text = safe_text(raw.get("path"), "Git path", 1024)
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ExportError("Git path escapes the project")
    commit = str(raw.get("commit") or "")
    digest = str(raw.get("hash") or "")
    if not COMMIT_RE.fullmatch(commit) or not HASH_RE.fullmatch(digest):
        raise ExportError("Git commit or hash is malformed")
    blob = run_git(project_root, ["show", f"{commit}:{relative.as_posix()}"])
    if sha256_bytes(blob) != digest:
        raise ExportError("Git blob hash does not match")
    require_head = raw.get("require_current_head")
    if not isinstance(require_head, bool):
        raise ExportError("require_current_head must be boolean")
    if require_head:
        head = run_git(project_root, ["rev-parse", "HEAD"]).decode().strip()
        if head != commit:
            raise ExportError("project HEAD moved after the source commit")
    return {
        "kind": "git",
        "project_root": str(project_root),
        "path": relative.as_posix(),
        "commit": commit,
        "hash": digest,
        "locator": safe_text(raw.get("locator"), "locator", 1024),
        "require_current_head": require_head,
    }


def validate_nodes(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list) or not raw or len(raw) > 32:
        raise ExportError("items require 1-32 routing nodes")
    nodes: list[dict[str, object]] = []
    for value in raw:
        if not isinstance(value, dict):
            raise ExportError("routing node must be an object")
        keywords = value.get("keywords")
        aliases = value.get("aliases", [])
        related = value.get("related_node_ids", [])
        if (
            not isinstance(keywords, list)
            or not keywords
            or not isinstance(aliases, list)
            or not isinstance(related, list)
        ):
            raise ExportError("invalid node keyword, alias, or relation list")
        nodes.append(
            {
                "id": safe_id(value.get("id"), "node id"),
                "title": safe_text(value.get("title"), "node title", 160),
                "keywords": [safe_text(item, "keyword", 128) for item in keywords[:64]],
                "aliases": [safe_text(item, "alias", 128) for item in aliases[:64]],
                "related_node_ids": [safe_id(item, "related node id") for item in related[:64]],
            }
        )
    return nodes


def validate_item(raw: object, agent_id: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ExportError("item must be an object")
    scope = str(raw.get("scope") or "")
    if scope not in {"global", "project"}:
        raise ExportError("scope must be global or project")
    memory_class = str(raw.get("class") or "")
    if memory_class not in ALLOWED_CLASSES:
        raise ExportError("unsupported memory class")
    if scope == "global" and memory_class != "preference":
        raise ExportError("global scope accepts preferences only")
    body = safe_text(raw.get("body"), "body", MAX_BODY_BYTES)
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise ExportError("body exceeds 512 KiB")
    operation = str(raw.get("operation") or "promote")
    if operation not in {"promote", "supersede"}:
        raise ExportError("operation must be promote or supersede")
    expected_current_id = raw.get("expected_current_id")
    if operation == "supersede":
        expected_current_id = safe_id(expected_current_id, "expected_current_id")
    elif expected_current_id is not None:
        raise ExportError("promote item must not set expected_current_id")
    return {
        "scope": scope,
        "class": memory_class,
        "key": safe_text(raw.get("key"), "canonical key", 256),
        "body": body,
        "source": validate_source(raw.get("source"), scope),
        "nodes": validate_nodes(raw.get("nodes")),
        "operation": operation,
        "expected_current_id": expected_current_id,
        "valid_from": safe_text(raw.get("valid_from"), "valid_from", 128),
        "stale_when": safe_text(raw.get("stale_when"), "stale_when", 1024),
        "submitted_by_agent": agent_id,
    }


def assemble(payload: dict[str, Any]) -> tuple[dict[str, object] | None, dict[str, object]]:
    if payload.get("schema_version") != 1 or payload.get("type") != EXPORT_TYPE:
        raise ExportError("unsupported export type or schema version")
    export_id = safe_id(payload.get("export_id"), "export_id")
    agent_id = safe_id(payload.get("agent_id"), "agent_id")
    tool = safe_text(payload.get("tool"), "tool", 128)
    created_at = safe_text(payload.get("created_at"), "created_at", 64)
    items = payload.get("items")
    if not isinstance(items, list) or not items or len(items) > 512:
        raise ExportError("items must contain 1-512 entries")
    source_rejected = payload.get("rejected", [])
    if not isinstance(source_rejected, list):
        raise ExportError("rejected must be a list")

    candidates: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    identities: set[tuple[object, object, object]] = set()
    project_roots: set[str] = set()

    for index, raw in enumerate(items):
        fallback_id = f"invalid-item-{index + 1}"
        item_id = fallback_id
        if isinstance(raw, dict) and ID_RE.fullmatch(str(raw.get("export_item_id") or "")):
            item_id = str(raw["export_item_id"])
        reasons = unsafe_reasons(raw)
        if reasons:
            rejected.append({"export_item_id": item_id, "reason": ",".join(reasons)})
            continue
        try:
            candidate = validate_item(raw, agent_id)
            identity = (candidate["scope"], candidate["class"], str(candidate["key"]).casefold())
            if identity in identities:
                raise ExportError("duplicate canonical identity in one export")
            identities.add(identity)
            source = candidate["source"]
            if isinstance(source, dict) and source.get("kind") == "git":
                project_roots.add(str(source["project_root"]))
            candidates.append(candidate)
        except ExportError as exc:
            rejected.append({"export_item_id": item_id, "reason": str(exc)})

    for raw in source_rejected:
        if not isinstance(raw, dict):
            continue
        rejected.append(
            {
                "export_item_id": str(raw.get("export_item_id") or "source-rejected"),
                "reason": safe_text(raw.get("reason"), "rejected reason", 256),
            }
        )

    if len(project_roots) > 1:
        raise ExportError("one export cannot contain several project roots")

    report: dict[str, object] = {
        "schema_version": 1,
        "type": "memorygraph-agent-export-report-v1",
        "export_id": export_id,
        "agent_id": agent_id,
        "tool": tool,
        "created_at": created_at,
        "accepted_count": len(candidates),
        "rejected_count": len(rejected),
        "rejected": rejected,
        "bodies_included": False,
    }
    if not candidates:
        report["status"] = "NO_IMPORTABLE_CANDIDATES"
        return None, report

    inventory: dict[str, object] = {
        "schema_version": 1,
        "type": INVENTORY_TYPE,
        "batch_id": export_id,
        "created_at": created_at,
        "candidates": candidates,
    }
    inventory["batch_digest"] = sha256_bytes(canonical_json(inventory))
    report["status"] = "SEALED_WITH_REJECTIONS" if rejected else "SEALED"
    report["inventory_digest"] = inventory["batch_digest"]
    report["project_root"] = next(iter(project_roots), None)
    return inventory, report


def atomic_json(path: Path, value: object) -> None:
    target = path.expanduser().resolve()
    if target.exists() or target.is_symlink():
        raise ExportError(f"refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".migration-", dir=target.parent)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report_path = Path(args.report)
    try:
        payload = load_export(Path(args.export))
        inventory, report = assemble(payload)
        if inventory is not None:
            atomic_json(Path(args.output), inventory)
        atomic_json(report_path, report)
    except ExportError as exc:
        print(json.dumps({"status": "EXPORT_INVALID", "message": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if inventory is None:
        return 1
    return 2 if int(report["rejected_count"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
