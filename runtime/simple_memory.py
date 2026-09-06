#!/usr/bin/env python3
"""User-owned Git Memory Graph with candidate-first writes.

This is intentionally a workflow safety system, not an OS security boundary.
Agents may read active records and write pending candidates.  An active mutation
requires an exact owner confirmation phrase bound to an immutable batch digest,
then the originating Agent creates one Git commit.  Snapshots remain disabled
until the Owner explicitly enables them.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import datetime as dt

try:
    import fcntl
except ImportError:  # Windows uses a byte-range lock; chmod is not an ACL.
    fcntl = None
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid


SCHEMA_VERSION = 1
STORE_TYPE = "memorygraph-simple-store"
BATCH_TYPE = "memorygraph-simple-pending-batch"
IMPORT_TYPE = "memorygraph-pending-import-v1"
ALLOWED_CLASSES = {"decision", "preference", "lesson", "state", "evidence"}
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,95}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_BODY_BYTES = 512 * 1024
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[^\s]{8,}"
    ),
)
INSTRUCTION_PATTERNS = (
    re.compile(r"(?i)ignore (?:all |any )?(?:previous|prior|system) instructions"),
    re.compile(r"(?i)(?:disable|bypass) (?:security|policy|guardrails?)"),
    re.compile(r"(?i)send (?:the )?(?:secret|token|credential)"),
)


class MemoryError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def confirmation_phrases(batch_id: str, digest: str) -> dict[str, str]:
    return {
        "zh": f"确认记忆批次 {batch_id} {digest}",
        "en": f"Confirm memory batch {batch_id} {digest}",
    }


def candidate_id_for_upload(upload_id: str, revision: int) -> str:
    safe_id(upload_id, "upload id")
    if type(revision) != int or not 0 <= revision <= 100:
        raise MemoryError("REVISION_INVALID", "Invalid review revision.")
    return "candidate-" + hashlib.sha256(canonical_json([upload_id, revision])).hexdigest()[:32]


def batch_id_for_request(request_id: str, head: str) -> str:
    safe_id(request_id, "request id")
    return "batch-" + hashlib.sha256(canonical_json([request_id, head])).hexdigest()[:32]


def safe_id(value: object, field: str) -> str:
    text = str(value or "")
    if not ID_RE.fullmatch(text):
        raise MemoryError("SCHEMA_INVALID", f"Invalid {field}.")
    return text


def clean_text(value: object, field: str, maximum: int = 1024) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if (
        not text
        or len(text) > maximum
        or any(ord(char) < 32 and char not in "\n\t" for char in text)
    ):
        raise MemoryError("SCHEMA_INVALID", f"Invalid {field}.")
    return text


def canonical_key(value: object) -> str:
    return " ".join(clean_text(value, "key", 256).casefold().split())


def atomic_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".memorygraph-", dir=str(path.parent))
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value: object, mode: int = 0o600) -> None:
    atomic_bytes(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        mode,
    )


def load_json(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise MemoryError("STATE_UNAVAILABLE", f"JSON state unavailable: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryError("STATE_INVALID", f"JSON state invalid: {path}") from exc
    if not isinstance(value, dict):
        raise MemoryError("STATE_INVALID", f"JSON root must be an object: {path}")
    return value


def run_git(
    root: Path, arguments: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        [shutil.which("git") or "git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if check and completed.returncode:
        raise MemoryError("GIT_FAILED", "A bounded Git operation failed.", operation=arguments[0])
    return completed


def git_head(root: Path) -> str:
    return run_git(root, ["rev-parse", "HEAD"]).stdout.decode().strip()


def git_toplevel(root: Path) -> Path:
    completed = run_git(root, ["rev-parse", "--show-toplevel"])
    return Path(completed.stdout.decode().strip()).resolve()


def ensure_clean(root: Path) -> None:
    if run_git(root, ["status", "--porcelain", "--untracked-files=no"]).stdout.strip():
        raise MemoryError(
            "STORE_DIRTY", "Tracked canonical files changed outside an approved transaction."
        )


def ensure_queue_ready(store: Path, *, canonical_only: bool = False) -> None:
    """Gate mutations under the store lock; completed cleanup permits Git-only admin."""
    path = store / ".queue/chmemx/maintenance/active.json"
    if not path.exists() and not path.is_symlink():
        return
    journal = load_json(path)
    if canonical_only and (
        journal.get("type") == "chmemx-maintenance-journal-v1"
        and journal.get("store") == str(store)
        and journal.get("phase") == "COMPLETED"
        and journal.get("completed_side") in {"before", "after"}
        and sha256_bytes(canonical_json({k: v for k, v in journal.items() if k != "digest"}))
        == journal.get("digest")
    ):
        return
    raise MemoryError(
        "MAINTENANCE_RECOVERY_REQUIRED",
        "Finish or roll back the interrupted queue transaction.",
        transaction_id=journal.get("transaction_id"),
    )


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise MemoryError("PATH_ESCAPE", "Source path must be repository-relative.")
    return path


def secret_reasons(value: object) -> list[str]:
    text = unicodedata.normalize("NFKC", json.dumps(value, ensure_ascii=False, sort_keys=True))
    reasons: list[str] = []
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        reasons.append("possible-secret")
    if any(pattern.search(text) for pattern in INSTRUCTION_PATTERNS):
        reasons.append("embedded-instruction")
    return reasons


class StoreLock:
    def __init__(self, store: Path) -> None:
        self.path = store / ".memorygraph.lock"
        self.stream: object | None = None

    def __enter__(self) -> "StoreLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        try:
            if fcntl is not None:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
            else:
                import msvcrt

                # Windows byte locks may extend beyond EOF. Never read/write
                # the lock byte before owning it: another process may hold it.
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_LOCK, 1)
        except OSError as error:
            self.stream.close()
            raise MemoryError(
                "LOCK_UNAVAILABLE", "Could not acquire store lock.", retryable=True
            ) from error
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        assert self.stream is not None
        if fcntl is not None:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        else:
            import msvcrt

            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()


APP_GIT_CONFIG = {
    "user.name": "Memory Graph",
    "user.email": "memory-graph@invalid",
    "core.autocrlf": "false",
}


class SimpleMemory:
    def __init__(self, store: Path) -> None:
        self.store = store.expanduser().resolve()

    @property
    def queue(self) -> Path:
        return self.store / ".queue"

    def _catalog(self) -> dict[str, object]:
        catalog = load_json(self.store / "catalog.json")
        if catalog.get("type") != STORE_TYPE or catalog.get("schema_version") != SCHEMA_VERSION:
            raise MemoryError("STORE_INVALID", "Memory catalog type/schema mismatch.")
        return catalog

    def init(
        self,
        project_root: Path,
        project_id: str,
        title: str,
        backup_root: Path | None,
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        if not confirmed:
            raise MemoryError(
                "CONFIRMATION_REQUIRED", "Store initialization requires owner confirmation."
            )
        if self.store.exists():
            raise MemoryError("STORE_EXISTS", "Simple memory store already exists.")
        project_root = project_root.expanduser().resolve()
        if git_toplevel(project_root) != project_root:
            raise MemoryError("PROJECT_INVALID", "Project root must be the exact Git toplevel.")
        project_id = safe_id(project_id, "project id")
        title = clean_text(title, "project title", 160)
        self.store.mkdir(parents=True, mode=0o700)
        try:
            for path in (
                self.store / "global" / "active",
                self.store / "projects" / project_id / "active",
                self.store / "approvals",
                self.store / "tombstones",
                self.queue / "candidates",
                self.queue / "batches",
                self.queue / "archive",
            ):
                path.mkdir(parents=True, exist_ok=True)
            policy = {
                "schema_version": 1,
                "type": "memorygraph-simple-policy",
                "active_write_requires_exact_owner_confirmation": True,
                "same_uid_malicious_process_resistance": False,
                "secrets_allowed": False,
                "backup_encryption": False,
                "backup_root": str(backup_root.expanduser().resolve()) if backup_root else None,
                "snapshots_enabled": False,
                "snapshot_policy": "owner-explicit-only",
                "agent_commits_own_batches": True,
                "git_revert_only": True,
            }
            catalog = {
                "schema_version": 1,
                "type": STORE_TYPE,
                "created_at": iso(),
                "projects": {
                    project_id: {
                        "project_id": project_id,
                        "title": title,
                        "root": str(project_root),
                    }
                },
            }
            atomic_json(self.store / "policy.json", policy, 0o600)
            atomic_json(self.store / "catalog.json", catalog, 0o600)
            atomic_json(
                self.store / "global" / "active-index.json",
                {"schema_version": 1, "entries": {}},
                0o600,
            )
            atomic_json(
                self.store / "global" / "nodes.json", {"schema_version": 1, "nodes": {}}, 0o600
            )
            atomic_json(
                self.store / "projects" / project_id / "binding.json",
                {
                    "schema_version": 1,
                    "project_id": project_id,
                    "title": title,
                    "root": str(project_root),
                },
                0o600,
            )
            atomic_json(
                self.store / "projects" / project_id / "active-index.json",
                {"schema_version": 1, "entries": {}},
                0o600,
            )
            atomic_json(
                self.store / "projects" / project_id / "nodes.json",
                {"schema_version": 1, "nodes": {}},
                0o600,
            )
            atomic_bytes(
                self.store / ".gitignore",
                b".queue/\n.memorygraph.lock\n__pycache__/\n*.pyc\n",
                0o600,
            )
            atomic_bytes(
                self.store / "README.md",
                (
                    "# MemoryGraph Simple\n\n"
                    "User-owned Git canonical memory. Active writes require an exact owner-confirmed batch.\n"
                    "Pending candidates under `.queue/` are ignored by Git and never recalled.\n"
                ).encode(),
                0o600,
            )
            subprocess.run(
                [shutil.which("git") or "git", "init", "-q", str(self.store)], check=True
            )
            for key, value in APP_GIT_CONFIG.items():
                run_git(self.store, ["config", "--local", key, value])
            run_git(
                self.store,
                [
                    "add",
                    ".gitignore",
                    "README.md",
                    "policy.json",
                    "catalog.json",
                    "global",
                    "projects",
                ],
            )
            run_git(self.store, ["commit", "-q", "-m", "memory: initialize simple store"])
            return {
                "status": "INITIALIZED",
                "store": str(self.store),
                "project_id": project_id,
                "head": git_head(self.store),
            }
        except Exception:
            shutil.rmtree(self.store, ignore_errors=True)
            raise

    def _project_for_cwd(self, cwd: Path) -> tuple[str | None, dict[str, object] | None]:
        catalog = self._catalog()
        try:
            root = git_toplevel(cwd.expanduser().resolve())
        except MemoryError:
            return None, None
        matches = (
            [
                (str(project_id), value)
                for project_id, value in catalog.get("projects", {}).items()
                if isinstance(value, dict) and Path(str(value.get("root"))).resolve() == root
            ]
            if isinstance(catalog.get("projects"), dict)
            else []
        )
        if len(matches) > 1:
            raise MemoryError(
                "PROJECT_AMBIGUOUS", "Multiple project bindings match current Git root."
            )
        return matches[0] if matches else (None, None)

    def project_for_cwd(self, cwd: Path):
        """Public project binding query; does not create registrations."""
        return self._project_for_cwd(cwd)

    def active_records(self, project_id: str | None):
        """Public, current Active view for one project plus global preferences."""
        return self._all_active(project_id)

    def approval_result(self, batch_id: str, expected_digest: str, *, upload_id: str | None = None):
        """Resolve current or reverted proof from this HEAD's Git ancestry, never queue claims."""
        safe_id(batch_id, "batch id")
        ensure_clean(self.store)
        head = git_head(self.store)
        rel = f"approvals/{batch_id}.json"
        result = run_git(self.store, ["show", f"{head}:{rel}"], check=False)
        present = result.returncode == 0
        commit = (
            run_git(
                self.store,
                [
                    "log",
                    "-1",
                    "--format=%H",
                    *([] if present else ["--diff-filter=A"]),
                    head,
                    "--",
                    rel,
                ],
            )
            .stdout.decode()
            .strip()
        )
        if not present:
            if not commit:
                if git_head(self.store) != head:
                    raise MemoryError("HEAD_CHANGED", "Canonical HEAD moved during proof lookup.")
                return None
            result = run_git(self.store, ["show", f"{commit}:{rel}"])
        receipt = json.loads(result.stdout)
        if (
            not isinstance(receipt, dict)
            or receipt.get("type") != "memorygraph-simple-approval"
            or receipt.get("batch_id") != batch_id
            or not isinstance(receipt.get("records"), list)
            or not receipt["records"]
            or any(not isinstance(record, dict) for record in receipt["records"])
        ):
            raise MemoryError("APPROVAL_PROOF_INVALID", "Committed approval identity is invalid.")
        if receipt.get("batch_digest") != expected_digest:
            raise MemoryError("BATCH_CHANGED", "Committed approval digest differs.")
        if upload_id is not None:
            safe_id(upload_id, "upload id")
            if any(record.get("upload_id") != upload_id for record in receipt["records"]):
                raise MemoryError("APPROVAL_UPLOAD_MISMATCH", "Approval belongs to another upload.")
        active_ids = []
        indexes = {}
        for record in receipt["records"] if present else []:
            root = (
                "global"
                if record["scope"] == "global"
                else "projects/" + safe_id(record["project_id"], "project id")
            )
            if root not in indexes:
                raw = run_git(self.store, ["show", f"{head}:{root}/active-index.json"])
                indexes[root] = set(json.loads(raw.stdout)["entries"].values())
            if record["id"] in indexes[root]:
                active_ids.append(record["id"])
        if git_head(self.store) != head:
            raise MemoryError("HEAD_CHANGED", "Canonical HEAD moved during proof lookup.")
        return {
            "status": "COMMIT_NOT_CURRENT"
            if not present
            else "ACTIVE_COMMITTED"
            if len(active_ids) == len(receipt["records"])
            else "COMMITTED_NOT_ACTIVE",
            "batch_id": batch_id,
            "batch_digest": expected_digest,
            "commit": commit,
            "record_ids": [r["id"] for r in receipt["records"]],
            "active_record_ids": active_ids,
            "checked_head": head,
            "committed_by_agent": receipt["committed_by_agent"],
            "recovered_from_git": True,
            "recovered_from_history": not present,
        }

    def status(self, cwd: Path) -> dict[str, object]:
        catalog = self._catalog()
        project_id, project = self._project_for_cwd(cwd)
        return {
            "status": "READY",
            "mode": "simple-git-confirmed",
            "store": str(self.store),
            "head": git_head(self.store),
            "project_id": project_id,
            "project": project,
            "project_count": len(catalog.get("projects", {})),
            "pending_candidates": len(list((self.queue / "candidates").glob("candidate-*.json"))),
            "pending_batches": len(list((self.queue / "batches").glob("batch-*.json"))),
            "active_count": len(self._all_active(project_id)),
        }

    def register_project(
        self,
        project_root: Path,
        project_id: str,
        title: str,
        *,
        confirmed: bool,
    ) -> dict[str, object]:
        if not confirmed:
            raise MemoryError(
                "CONFIRMATION_REQUIRED", "Project registration requires owner confirmation."
            )
        project_root = project_root.expanduser().resolve()
        if git_toplevel(project_root) != project_root:
            raise MemoryError("PROJECT_INVALID", "Project root must be the exact Git toplevel.")
        project_id = safe_id(project_id, "project id")
        title = clean_text(title, "project title", 160)
        with StoreLock(self.store):
            ensure_queue_ready(self.store, canonical_only=True)
            ensure_clean(self.store)
            catalog = self._catalog()
            projects = catalog.get("projects")
            if not isinstance(projects, dict):
                raise MemoryError("STORE_INVALID", "Project catalog is malformed.")
            if project_id in projects or any(
                isinstance(value, dict) and Path(str(value.get("root"))).resolve() == project_root
                for value in projects.values()
            ):
                raise MemoryError("PROJECT_EXISTS", "Project id or root is already registered.")
            project_scope = self.store / "projects" / project_id
            project_scope.mkdir(parents=True)
            (project_scope / "active").mkdir()
            projects[project_id] = {
                "project_id": project_id,
                "title": title,
                "root": str(project_root),
            }
            atomic_json(project_scope / "binding.json", projects[project_id], 0o600)
            atomic_json(
                project_scope / "active-index.json", {"schema_version": 1, "entries": {}}, 0o600
            )
            atomic_json(project_scope / "nodes.json", {"schema_version": 1, "nodes": {}}, 0o600)
            atomic_json(self.store / "catalog.json", catalog, 0o600)
            run_git(self.store, ["add", "catalog.json", str(project_scope.relative_to(self.store))])
            run_git(self.store, ["commit", "-q", "-m", f"memory: register project {project_id}"])
            return {
                "status": "PROJECT_REGISTERED",
                "project_id": project_id,
                "root": str(project_root),
                "head": git_head(self.store),
            }

    def _scope_root(self, scope: str, project_id: str | None) -> Path:
        if scope == "global":
            return self.store / "global"
        if scope != "project" or project_id is None:
            raise MemoryError("SCOPE_INVALID", "Project scope requires a bound project id.")
        safe_id(project_id, "project id")
        root = self.store / "projects" / project_id
        if not root.is_dir() or root.is_symlink():
            raise MemoryError("PROJECT_UNAVAILABLE", "Project memory scope is unavailable.")
        return root

    def _validate_source(
        self, source: object, scope: str, project_id: str | None
    ) -> dict[str, object]:
        if not isinstance(source, dict):
            raise MemoryError("SOURCE_INVALID", "Candidate source must be an object.")
        kind = source.get("kind")
        if kind == "user-instruction":
            message_digest = str(source.get("message_digest") or "")
            if not HASH_RE.fullmatch(message_digest):
                raise MemoryError(
                    "SOURCE_INVALID", "User-instruction source requires a message digest."
                )
            return {
                "kind": kind,
                "thread_id": clean_text(source.get("thread_id"), "thread id", 128),
                "message_digest": message_digest,
                "locator": clean_text(source.get("locator"), "source locator", 512),
            }
        if kind == "memory-file":
            memory_root = (
                Path(
                    os.environ.get(
                        "MEMORY_GRAPH_CODEX_MEMORY_ROOT", str(Path.home() / ".codex" / "memories")
                    )
                )
                .expanduser()
                .resolve()
            )
            path = Path(str(source.get("path") or "")).expanduser().resolve()
            try:
                path.relative_to(memory_root)
            except ValueError as exc:
                raise MemoryError(
                    "SOURCE_SCOPE_VIOLATION", "Memory-file source escapes the Codex memories root."
                ) from exc
            digest = str(source.get("hash") or "")
            if (
                path.is_symlink()
                or not path.is_file()
                or not HASH_RE.fullmatch(digest)
                or sha256_file(path) != digest
            ):
                raise MemoryError("SOURCE_STALE", "Codex memory-file source hash failed.")
            return {
                "kind": "memory-file",
                "path": str(path),
                "hash": digest,
                "locator": clean_text(source.get("locator"), "source locator", 1024),
            }
        if kind != "git":
            raise MemoryError(
                "SOURCE_INVALID", "Source kind must be git, memory-file, or user-instruction."
            )
        if scope != "project" or project_id is None:
            raise MemoryError("SOURCE_INVALID", "Git project source belongs to project scope.")
        catalog = self._catalog()
        project = (
            catalog.get("projects", {}).get(project_id)
            if isinstance(catalog.get("projects"), dict)
            else None
        )
        if not isinstance(project, dict):
            raise MemoryError("PROJECT_UNAVAILABLE", "Project catalog entry is unavailable.")
        project_root = Path(str(project["root"])).resolve()
        if Path(str(source.get("project_root"))).resolve() != project_root:
            raise MemoryError(
                "SOURCE_SCOPE_VIOLATION", "Git source root differs from registered project."
            )
        commit = str(source.get("commit") or "")
        digest = str(source.get("hash") or "")
        relative = safe_relative(clean_text(source.get("path"), "source path", 1024))
        if not COMMIT_RE.fullmatch(commit) or not HASH_RE.fullmatch(digest):
            raise MemoryError("SOURCE_INVALID", "Git source commit/hash is malformed.")
        completed = run_git(project_root, ["show", f"{commit}:{relative.as_posix()}"], check=False)
        if completed.returncode or sha256_bytes(completed.stdout) != digest:
            raise MemoryError("SOURCE_STALE", "Git source blob hash failed.")
        require_current_head = bool(source.get("require_current_head", True))
        if require_current_head and git_head(project_root) != commit:
            raise MemoryError(
                "SOURCE_STALE", "Registered project HEAD moved after the candidate source commit."
            )
        return {
            "kind": "git",
            "project_root": str(project_root),
            "path": relative.as_posix(),
            "commit": commit,
            "hash": digest,
            "locator": clean_text(
                source.get("locator") or relative.as_posix(), "source locator", 1024
            ),
            "require_current_head": require_current_head,
        }

    def _validated_nodes(self, nodes: object) -> list[dict[str, object]]:
        if not isinstance(nodes, list) or not nodes or len(nodes) > 32:
            raise MemoryError("SCHEMA_INVALID", "Candidate requires 1-32 routing nodes.")
        output: list[dict[str, object]] = []
        for raw in nodes:
            if not isinstance(raw, dict):
                raise MemoryError("SCHEMA_INVALID", "Routing node must be an object.")
            keywords = raw.get("keywords")
            aliases = raw.get("aliases", [])
            related = raw.get("related_node_ids", [])
            if (
                not isinstance(keywords, list)
                or not keywords
                or not isinstance(aliases, list)
                or not isinstance(related, list)
            ):
                raise MemoryError(
                    "SCHEMA_INVALID", "Node keyword/alias/relation fields must be lists."
                )
            output.append(
                {
                    "id": safe_id(raw.get("id"), "node id"),
                    "title": clean_text(raw.get("title"), "node title", 160),
                    "keywords": [clean_text(value, "keyword", 128) for value in keywords[:64]],
                    "aliases": [clean_text(value, "alias", 128) for value in aliases[:64]],
                    "related_node_ids": [
                        safe_id(value, "related node id") for value in related[:64]
                    ],
                }
            )
        return output

    def _candidate_digest(self, candidate: dict[str, object]) -> str:
        core = {key: value for key, value in candidate.items() if key != "candidate_digest"}
        return sha256_bytes(canonical_json(core))

    def propose(self, raw: dict[str, object], cwd: Path) -> dict[str, object]:
        with StoreLock(self.store):
            ensure_queue_ready(self.store)
            scope = str(raw.get("scope") or "")
            project_id, _project = self._project_for_cwd(cwd)
            if scope == "global":
                target_project = None
            elif scope == "project":
                target_project = project_id
                if target_project is None:
                    raise MemoryError(
                        "PROJECT_UNAVAILABLE", "Current Git root has no registered project scope."
                    )
            else:
                raise MemoryError("SCOPE_INVALID", "Candidate scope must be global or project.")
            memory_class = str(raw.get("class") or "")
            if memory_class not in ALLOWED_CLASSES:
                raise MemoryError("CLASS_INVALID", "Unsupported memory class.")
            if scope == "global" and memory_class != "preference":
                raise MemoryError("CLASS_INVALID", "Global scope accepts Preference only.")
            body = clean_text(raw.get("body"), "body", MAX_BODY_BYTES)
            if len(body.encode("utf-8")) > MAX_BODY_BYTES:
                raise MemoryError("SCHEMA_INVALID", "Candidate body exceeds 512 KiB.")
            candidate: dict[str, object] = {
                "schema_version": 1,
                "type": "memorygraph-simple-candidate",
                "candidate_id": "candidate-" + uuid.uuid4().hex,
                "scope": scope,
                "project_id": target_project,
                "class": memory_class,
                "key": canonical_key(raw.get("key")),
                "body": body,
                "source": self._validate_source(raw.get("source"), scope, target_project),
                "nodes": self._validated_nodes(raw.get("nodes")),
                "operation": str(raw.get("operation") or "promote"),
                "expected_current_id": raw.get("expected_current_id"),
                "valid_from": clean_text(raw.get("valid_from") or iso(), "valid_from", 128),
                "stale_when": clean_text(raw.get("stale_when"), "stale_when", 1024)
                if raw.get("stale_when")
                else None,
                "status": "candidate",
                "reasons": [],
                "submitted_by_agent": safe_id(raw.get("submitted_by_agent"), "submitting agent"),
                "source_agent": safe_id(
                    raw.get("source_agent") or raw.get("submitted_by_agent"), "source agent"
                ),
                "created_at": iso(),
            }
            if raw.get("upload_id"):
                candidate["upload_id"] = safe_id(raw["upload_id"], "upload id")
                candidate["review_revision"] = raw.get("review_revision", 0)
                candidate["candidate_id"] = candidate_id_for_upload(
                    candidate["upload_id"], candidate["review_revision"]
                )
            if candidate["operation"] not in {"promote", "supersede"}:
                raise MemoryError(
                    "OPERATION_INVALID", "Candidate operation must be promote or supersede."
                )
            if candidate["operation"] == "supersede":
                safe_id(candidate["expected_current_id"], "expected current id")
            reasons = secret_reasons(candidate)
            if reasons:
                candidate["status"] = "quarantine"
                candidate["reasons"] = reasons
                candidate["body"] = "[REDACTED QUARANTINE]"
                candidate["nodes"] = []
            candidate["candidate_digest"] = self._candidate_digest(candidate)
            path = self.queue / "candidates" / f"{candidate['candidate_id']}.json"
            if path.exists() and candidate.get("upload_id"):
                previous = self._load_candidate(candidate["candidate_id"])
                stable = lambda x: {
                    k: v for k, v in x.items() if k not in ("created_at", "candidate_digest")
                }
                if stable(previous) != stable(candidate):
                    raise MemoryError(
                        "CANDIDATE_CHANGED", "Upload revision already has different content."
                    )
                return previous
            atomic_json(path, candidate, 0o600)
            return copy.deepcopy(candidate)

    def _load_candidate(self, candidate_id: str) -> dict[str, object]:
        candidate = load_json(
            self.queue / "candidates" / f"{safe_id(candidate_id, 'candidate id')}.json"
        )
        if self._candidate_digest(candidate) != candidate.get("candidate_digest"):
            raise MemoryError("CANDIDATE_CHANGED", "Candidate digest failed.")
        return candidate

    def _batch_digest(self, batch: dict[str, object]) -> str:
        core = {key: value for key, value in batch.items() if key != "batch_digest"}
        return sha256_bytes(canonical_json(core))

    def create_batch(
        self,
        candidate_ids: list[str],
        *,
        request_id: str | None = None,
        expected_head: str | None = None,
    ) -> dict[str, object]:
        with StoreLock(self.store):
            head = git_head(self.store)
            ensure_queue_ready(self.store)
            if expected_head is not None and head != expected_head:
                raise MemoryError("HEAD_CHANGED", "Memory HEAD moved while preparing review.")
            batch_id = (
                batch_id_for_request(request_id, head)
                if request_id
                else "batch-" + uuid.uuid4().hex
            )
            if request_id and (self.queue / "batches" / f"{batch_id}.json").exists():
                previous = self._load_batch(batch_id)
                if previous["candidate_ids"] != candidate_ids:
                    raise MemoryError("BATCH_CHANGED", "Review request changed candidates.")
                return previous
            if not candidate_ids or len(candidate_ids) > 512:
                raise MemoryError("BATCH_INVALID", "Batch requires 1-512 candidates.")
            candidates = [self._load_candidate(value) for value in candidate_ids]
            if any(item.get("status") != "candidate" for item in candidates):
                raise MemoryError(
                    "BATCH_INVALID",
                    "Quarantined or non-candidate record cannot enter approval batch.",
                )
            identities = [
                (item.get("scope"), item.get("project_id"), item.get("class"), item.get("key"))
                for item in candidates
            ]
            if len(identities) != len(set(identities)):
                raise MemoryError(
                    "BATCH_CONFLICT", "Batch contains duplicate canonical identities."
                )
            # Pre-policy candidates are preserved and attributed to the one
            # migration Agent that created the legacy queue. New candidates
            # must always carry an explicit submitting Agent.
            submitting_agents = {
                str(item.get("submitted_by_agent") or "legacy-migration-agent")
                for item in candidates
            }
            if len(submitting_agents) != 1:
                raise MemoryError(
                    "BATCH_AGENT_MIXED",
                    "One approval batch must belong to exactly one submitting agent.",
                )
            batch: dict[str, object] = {
                "schema_version": 1,
                "type": BATCH_TYPE,
                "batch_id": batch_id,
                "store_head": head,
                "candidate_ids": [item["candidate_id"] for item in candidates],
                "candidates": candidates,
                "submitting_agent": next(iter(submitting_agents)),
                "created_at": iso(),
                "status": "pending",
                "nonce": uuid.uuid4().hex,
            }
            batch["batch_digest"] = self._batch_digest(batch)
            atomic_json(self.queue / "batches" / f"{batch['batch_id']}.json", batch, 0o600)
            return copy.deepcopy(batch)

    def _load_batch(self, batch_id: str) -> dict[str, object]:
        batch = load_json(self.queue / "batches" / f"{safe_id(batch_id, 'batch id')}.json")
        if batch.get("type") != BATCH_TYPE or self._batch_digest(batch) != batch.get(
            "batch_digest"
        ):
            raise MemoryError("BATCH_CHANGED", "Pending batch digest failed.")
        candidates = batch.get("candidates")
        if not isinstance(candidates, list):
            raise MemoryError("BATCH_CHANGED", "Pending batch candidate list is malformed.")
        for sealed in candidates:
            if not isinstance(sealed, dict):
                raise MemoryError("BATCH_CHANGED", "Sealed candidate is malformed.")
            current = self._load_candidate(str(sealed["candidate_id"]))
            if current != sealed:
                raise MemoryError("CANDIDATE_CHANGED", "Candidate changed after batch sealing.")
        return batch

    def review(self, batch_id: str) -> dict[str, object]:
        batch = self._load_batch(batch_id)
        return {
            "status": batch["status"],
            "batch_id": batch["batch_id"],
            "batch_digest": batch["batch_digest"],
            "store_head": batch["store_head"],
            "candidate_count": len(batch["candidates"]),
            "submitting_agent": batch.get("submitting_agent"),
            "candidates": batch["candidates"],
            "required_confirmation": f"确认记忆批次 {batch['batch_id']} {batch['batch_digest']}",
            "accepted_confirmations": confirmation_phrases(
                batch["batch_id"], batch["batch_digest"]
            ),
        }

    def retire_review(self, batch_id: str) -> None:
        """Invalidate an obsolete sealed review without deleting its evidence."""
        with StoreLock(self.store):
            path = self.queue / "batches" / f"{safe_id(batch_id, 'batch id')}.json"
            if not path.exists():
                return
            batch = self._load_batch(batch_id)
            if batch["status"] == "pending":
                batch["status"] = "review_replaced"
                batch["batch_digest"] = self._batch_digest(batch)
                atomic_json(path, batch, 0o600)

    def _index(self, root: Path) -> dict[str, object]:
        return load_json(root / "active-index.json")

    def _nodes(self, root: Path) -> dict[str, object]:
        return load_json(root / "nodes.json")

    def _backup_paths(self, paths: list[Path]) -> dict[Path, bytes | None]:
        return {path: path.read_bytes() if path.is_file() else None for path in paths}

    def _restore_paths(self, backup: dict[Path, bytes | None]) -> None:
        for path, data in backup.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, data, 0o600)

    @contextmanager
    def canonical_transaction(self, paths: list[Path]):
        """Rollback only declared canonical files if no commit occurred. Caller holds store lock."""
        ensure_clean(self.store)
        paths = list(dict.fromkeys(paths))
        relative = []
        for path in paths:
            try:
                rel = path.relative_to(self.store)
            except ValueError as error:
                raise MemoryError(
                    "TRANSACTION_PATH_INVALID", "Path is outside this store."
                ) from error
            if not rel.parts or rel.parts[0] in {".git", ".queue"} or ".." in rel.parts:
                raise MemoryError("TRANSACTION_PATH_INVALID", "Only canonical files are allowed.")
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise MemoryError("TRANSACTION_PATH_INVALID", "Transaction symlinks are rejected.")
            relative.append(rel.as_posix())
        head = git_head(self.store)
        backup = self._backup_paths(paths)
        try:
            yield
        except Exception:
            # A post-commit error cannot authorize rewriting committed state.
            if git_head(self.store) == head:
                self._restore_paths(backup)
                if relative:
                    run_git(self.store, ["restore", "--staged", "--", *relative], check=False)
            raise

    def approve(
        self,
        batch_id: str,
        expected_digest: str,
        confirmation_text: str,
        *,
        committing_agent: str,
        backup_root: Path | None = None,
        automatic_policy_digest: str | None = None,
    ) -> dict[str, object]:
        with StoreLock(self.store):
            ensure_queue_ready(self.store)
            ensure_clean(self.store)
            batch = self._load_batch(batch_id)
            if batch.get("status") != "pending" or batch.get("batch_digest") != expected_digest:
                raise MemoryError("BATCH_CHANGED", "Batch status or expected digest failed.")
            if automatic_policy_digest is not None:
                try:
                    from .write_policy import check_automatic, policy_digest
                except ImportError:
                    from write_policy import check_automatic, policy_digest
                policy = load_json(self.store / "policy.json")
                if policy_digest(policy) != automatic_policy_digest:
                    raise MemoryError("POLICY_CHANGED", "Personal policy changed before commit.")
                try:
                    check_automatic(policy, batch["candidates"], committing_agent)
                except ValueError as error:
                    raise MemoryError("OWNER_REVIEW_REQUIRED", str(error)) from error
            elif confirmation_text not in confirmation_phrases(batch_id, expected_digest).values():
                raise MemoryError(
                    "CONFIRMATION_REQUIRED", "Exact owner confirmation phrase is required."
                )
            committing_agent = safe_id(committing_agent, "committing agent")
            submitting_agent = safe_id(
                batch.get("submitting_agent")
                or batch["candidates"][0].get("submitted_by_agent")
                or "legacy-migration-agent",
                "submitting agent",
            )
            if committing_agent != submitting_agent:
                raise MemoryError(
                    "AGENT_MISMATCH", "The originating agent must commit its own approved batch."
                )
            if git_head(self.store) != batch.get("store_head"):
                raise MemoryError("HEAD_CHANGED", "Canonical Git head changed after batch review.")
            candidates = batch["candidates"]
            assert isinstance(candidates, list)
            touched: list[Path] = []
            prepared: list[
                tuple[dict[str, object], Path, dict[str, object], dict[str, object], Path]
            ] = []
            index_cache: dict[Path, dict[str, object]] = {}
            node_cache: dict[Path, dict[str, object]] = {}
            for candidate in candidates:
                assert isinstance(candidate, dict)
                scope = str(candidate["scope"])
                project_id = str(candidate["project_id"]) if candidate.get("project_id") else None
                source = self._validate_source(candidate["source"], scope, project_id)
                if source != candidate["source"]:
                    raise MemoryError("SOURCE_CHANGED", "Candidate source normalization changed.")
                root = self._scope_root(scope, project_id)
                index = index_cache.setdefault(root, self._index(root))
                nodes = node_cache.setdefault(root, self._nodes(root))
                unique = f"{candidate['class']}:{candidate['key']}"
                active = index.get("entries")
                if not isinstance(active, dict):
                    raise MemoryError("STORE_INVALID", "Active index is malformed.")
                existing = active.get(unique)
                operation = candidate["operation"]
                if operation == "promote" and existing is not None:
                    raise MemoryError(
                        "ACTIVE_CONFLICT", "Canonical identity already has an active record."
                    )
                if operation == "supersede" and existing != candidate.get("expected_current_id"):
                    raise MemoryError(
                        "HEAD_CHANGED", "Supersede target no longer matches active record."
                    )
                record_id = "memory-" + uuid.uuid4().hex
                record = {
                    "schema_version": 1,
                    "type": "memorygraph-simple-record",
                    "id": record_id,
                    "authority": "accepted",
                    "status": "active",
                    "scope": scope,
                    "project_id": project_id,
                    "class": candidate["class"],
                    "key": candidate["key"],
                    "body": candidate["body"],
                    "source": candidate["source"],
                    "nodes": candidate["nodes"],
                    "valid_from": candidate["valid_from"],
                    "stale_when": candidate["stale_when"],
                    "supersedes": existing,
                    "approval_batch_id": batch_id,
                    "approval_batch_digest": expected_digest,
                    "submitted_by_agent": candidate.get("submitted_by_agent") or submitting_agent,
                    "source_agent": candidate.get("source_agent")
                    or candidate.get("submitted_by_agent")
                    or submitting_agent,
                    "committed_by_agent": committing_agent,
                    "created_at": iso(),
                }
                if candidate.get("upload_id"):
                    record["upload_id"] = candidate["upload_id"]
                record_path = root / "active" / f"{record_id}.json"
                active[unique] = record_id
                node_map = nodes.get("nodes")
                if not isinstance(node_map, dict):
                    raise MemoryError("STORE_INVALID", "Node map is malformed.")
                for definition in candidate["nodes"]:
                    assert isinstance(definition, dict)
                    node_id = str(definition["id"])
                    current = node_map.get(node_id)
                    comparable = (
                        {
                            key: current.get(key)
                            for key in ("id", "title", "keywords", "aliases", "related_node_ids")
                        }
                        if isinstance(current, dict)
                        else None
                    )
                    if comparable is not None and comparable != definition:
                        raise MemoryError(
                            "NODE_CONFLICT", "Existing routing node definition differs."
                        )
                    entry_ids = (
                        list(current.get("entry_ids", [])) if isinstance(current, dict) else []
                    )
                    node_map[node_id] = {
                        **definition,
                        "entry_ids": list(dict.fromkeys([*entry_ids, record_id])),
                    }
                if existing is not None:
                    for node in node_map.values():
                        if isinstance(node, dict):
                            node["entry_ids"] = [
                                value for value in node.get("entry_ids", []) if value != existing
                            ]
                prepared.append((record, record_path, index, nodes, root))
                touched.extend([record_path, root / "active-index.json", root / "nodes.json"])
            for nodes in node_cache.values():
                node_map = nodes.get("nodes")
                assert isinstance(node_map, dict)
                for node in node_map.values():
                    if not isinstance(node, dict):
                        raise MemoryError("STORE_INVALID", "Routing node is malformed.")
                    if any(value not in node_map for value in node.get("related_node_ids", [])):
                        raise MemoryError(
                            "UNKNOWN_NODE", "Routing relation references an unknown node."
                        )
            approval_path = self.store / "approvals" / f"{batch_id}.json"
            touched.append(approval_path)
            with self.canonical_transaction(touched):
                records: list[dict[str, object]] = []
                written_roots: set[Path] = set()
                for record, path, index, nodes, root in prepared:
                    atomic_json(path, record, 0o600)
                    records.append(record)
                    if root not in written_roots:
                        atomic_json(root / "active-index.json", index, 0o600)
                        atomic_json(root / "nodes.json", nodes, 0o600)
                        written_roots.add(root)
                receipt = {
                    "schema_version": 1,
                    "type": "memorygraph-simple-approval",
                    "batch_id": batch_id,
                    "batch_digest": expected_digest,
                    "parent_head": batch["store_head"],
                    "confirmation_text_hash": sha256_bytes(confirmation_text.encode("utf-8")),
                    "approved_at": iso(),
                    "submitting_agent": submitting_agent,
                    "committed_by_agent": committing_agent,
                    "records": records,
                }
                if automatic_policy_digest is not None:
                    receipt.pop("confirmation_text_hash", None)
                    receipt["authorization_kind"] = "personal-policy"
                    receipt["policy_digest"] = automatic_policy_digest
                    receipt["owner_batch_confirmation"] = False
                atomic_json(approval_path, receipt, 0o600)
                relative_paths = [
                    str(path.relative_to(self.store)) for path in dict.fromkeys(touched)
                ]
                run_git(self.store, ["add", "--", *relative_paths])
                run_git(
                    self.store,
                    ["commit", "-q", "-m", f"memory: approve {batch_id} by {committing_agent}"],
                )
                committed_head = git_head(self.store)
            batch["status"] = "committed"
            batch["committed_head"] = committed_head
            batch["committed_at"] = iso()
            archive_batch = self.queue / "archive" / f"{batch_id}.json"
            atomic_json(archive_batch, batch, 0o600)
            (self.queue / "batches" / f"{batch_id}.json").unlink(missing_ok=True)
            for candidate in candidates:
                (self.queue / "candidates" / f"{candidate['candidate_id']}.json").unlink(
                    missing_ok=True
                )
            backup_result = None
            if backup_root is not None:
                try:
                    backup_result = self.create_backup(backup_root)
                except MemoryError as exc:
                    backup_result = {
                        "status": "BACKUP_FAILED",
                        "code": exc.code,
                        "message": exc.message,
                    }
            return {
                "status": "ACTIVE_COMMITTED"
                if not isinstance(backup_result, dict)
                or backup_result.get("status") != "BACKUP_FAILED"
                else "ACTIVE_COMMITTED_BACKUP_FAILED",
                "batch_id": batch_id,
                "batch_digest": expected_digest,
                "commit": committed_head,
                "record_ids": [value[0]["id"] for value in prepared],
                "submitting_agent": submitting_agent,
                "committed_by_agent": committing_agent,
                "backup": backup_result,
            }

    def _all_active(self, project_id: str | None) -> list[dict[str, object]]:
        roots = [self.store / "global"]
        if project_id:
            roots.append(self.store / "projects" / project_id)
        output: list[dict[str, object]] = []
        for root in roots:
            index = self._index(root)
            entries = index.get("entries")
            if not isinstance(entries, dict):
                raise MemoryError("STORE_INVALID", "Active index is malformed.")
            for record_id in sorted({str(value) for value in entries.values()}):
                record = load_json(root / "active" / f"{safe_id(record_id, 'record id')}.json")
                if record.get("authority") == "accepted" and record.get("status") == "active":
                    output.append(record)
        return output

    def recall(self, cwd: Path, query: str, limit: int = 8) -> dict[str, object]:
        project_id, _project = self._project_for_cwd(cwd)
        query = clean_text(query, "query", 1024)
        folded = query.casefold()
        tokens = {value for value in re.findall(r"[\w\u3400-\u9fff]+", folded) if value}
        scored: list[tuple[float, dict[str, object]]] = []
        for record in self._all_active(project_id):
            score = 0.0
            haystacks = [str(record.get("key", "")), str(record.get("body", ""))]
            for node in record.get("nodes", []):
                if isinstance(node, dict):
                    values = [
                        str(node.get("title", "")),
                        *map(str, node.get("keywords", [])),
                        *map(str, node.get("aliases", [])),
                    ]
                    haystacks.extend(values)
                    for value in values:
                        value_folded = value.casefold()
                        if value_folded == folded:
                            score += 1000
                        elif value_folded in folded or folded in value_folded:
                            score += 200
            combined = " ".join(haystacks).casefold()
            for token in tokens:
                count = combined.count(token)
                if count:
                    score += 10 + math.log2(1 + count)
            if folded in combined:
                score += 50
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("key")), str(item[1].get("id"))))
        return {
            "status": "OK",
            "mode": "simple-git-confirmed",
            "project_id": project_id,
            "head": git_head(self.store),
            "query": query,
            "entries": [{**record, "score": score} for score, record in scored[:limit]],
            "candidate_entries_included": False,
        }

    def start(self, cwd: Path, role: str, query: str | None) -> dict[str, object]:
        result = {
            "status": "READY",
            "mode": "simple-git-confirmed",
            "capabilities": {
                "role": role,
                "read_active": True,
                "submit_candidates": True,
                "commit_own_batch_after_owner_confirmation": True,
                "approve_without_exact_owner_confirmation": False,
                "same_uid_security_boundary": False,
                "snapshots_enabled": bool(
                    load_json(self.store / "policy.json").get("snapshots_enabled", False)
                ),
            },
            "memory": self.status(cwd),
            "results": [],
            "instructions_from_memory_executable": False,
        }
        if query:
            result["results"] = self.recall(cwd, query)["entries"]
        return result

    def import_pending(
        self,
        inventory_path: Path,
        cwd: Path,
        *,
        submitting_agent: str,
        confirmed: bool,
    ) -> dict[str, object]:
        if not confirmed:
            raise MemoryError(
                "CONFIRMATION_REQUIRED",
                "Pending inventory import requires explicit task authorization.",
            )
        inventory = load_json(inventory_path.expanduser().resolve())
        if inventory.get("type") != IMPORT_TYPE:
            raise MemoryError("IMPORT_INVALID", "Pending inventory type is invalid.")
        stored_digest = inventory.get("batch_digest")
        core = {key: value for key, value in inventory.items() if key != "batch_digest"}
        if stored_digest != sha256_bytes(canonical_json(core)):
            raise MemoryError("IMPORT_CHANGED", "Pending inventory digest failed.")
        candidates = inventory.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise MemoryError(
                "IMPORT_INVALID", "Pending inventory candidate list is empty or malformed."
            )
        submitting_agent = safe_id(submitting_agent, "submitting agent")
        created: list[dict[str, object]] = []
        try:
            for raw in candidates:
                if not isinstance(raw, dict):
                    raise MemoryError("IMPORT_INVALID", "Pending inventory candidate is malformed.")
                attributed = copy.deepcopy(raw)
                attributed["submitted_by_agent"] = submitting_agent
                created.append(self.propose(attributed, cwd))
            batch = self.create_batch([str(value["candidate_id"]) for value in created])
        except Exception:
            for value in created:
                (self.queue / "candidates" / f"{value['candidate_id']}.json").unlink(
                    missing_ok=True
                )
            raise
        return {
            "status": "PENDING_IMPORTED_NOT_ACTIVE",
            "source_batch_id": inventory.get("batch_id"),
            "source_batch_digest": inventory.get("batch_digest"),
            "batch": self.review(str(batch["batch_id"])),
        }

    def backup_state_files(self, extra_pending_roots=None):
        """Explicit durable state coverage; excludes rebuildable indexes and locks."""
        if (self.queue / "chmemx/maintenance/active.json").exists():
            raise MemoryError(
                "MAINTENANCE_RECOVERY_REQUIRED",
                "Recover the queue transaction before taking a backup.",
            )
        files = {}
        families = (
            ("candidates", "candidate-*.json"),
            ("batches", "batch-*.json"),
            ("archive", "batch-*.json"),
            ("chmemx/uploads", "*.json"),
            ("chmemx/events", "*.json"),
            ("chmemx/nonces", "*.json"),
            ("chmemx/receipts", "*.json"),
            ("chmemx/history", "*.gz"),
            ("chmemx/archives", "**/*.gz"),
            ("chmemx/maintenance/receipts", "*.json"),
        )
        for folder, pattern in families:
            for path in (self.queue / folder).glob(pattern):
                if path.is_symlink() or any(
                    p.is_symlink() for p in path.parents if p != self.queue
                ):
                    raise MemoryError("BACKUP_SOURCE_INVALID", "Symlink in durable queue state.")
                files["pending/" + path.relative_to(self.queue).as_posix()] = path
        if (self.queue / "chmemx/state.json").is_file():
            if any(
                p.is_symlink()
                for p in (self.queue / "chmemx/state.json", self.queue / "chmemx", self.queue)
            ):
                raise MemoryError("BACKUP_SOURCE_INVALID", "Symlink in queue metadata.")
            files["pending/chmemx/state.json"] = self.queue / "chmemx/state.json"
        for name, root in (extra_pending_roots or {}).items():
            safe_id(name, "external queue label")
            root = Path(root).expanduser().absolute()
            if any(p.is_symlink() for p in (root, *root.parents)):
                raise MemoryError("BACKUP_SOURCE_INVALID", "Symlink external root is not followed.")
            root = root.resolve()
            if not root.is_dir() or root in (Path("/"), Path.home()):
                raise MemoryError(
                    "BACKUP_SOURCE_INVALID",
                    "External pending root must be a specific real directory.",
                )
            for path in root.rglob("*.json"):
                if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root):
                    raise MemoryError("BACKUP_SOURCE_INVALID", "Symlink in external pending state.")
                files["external/" + name + "/" + path.relative_to(root).as_posix()] = path
        return files

    def create_backup(
        self, backup_root: Path | None = None, *, extra_pending_roots=None
    ) -> dict[str, object]:
        policy = load_json(self.store / "policy.json")
        if not bool(policy.get("snapshots_enabled", False)):
            raise MemoryError(
                "SNAPSHOT_DISABLED",
                "Memory snapshots are disabled until the Owner explicitly enables them.",
            )
        root_value = backup_root or (
            Path(str(policy["backup_root"])) if policy.get("backup_root") else None
        )
        if root_value is None:
            raise MemoryError("BACKUP_ROOT_REQUIRED", "Plain backup root is not configured.")
        backup_root = root_value.expanduser().resolve()
        for outer, inner in ((self.store, backup_root), (backup_root, self.store)):
            try:
                inner.relative_to(outer)
            except ValueError:
                continue
            raise MemoryError(
                "BACKUP_ROOT_INVALID", "Backup root and canonical store must not overlap."
            )
        backup_root.mkdir(parents=True, exist_ok=True)
        if backup_root.is_symlink() or not backup_root.is_dir():
            raise MemoryError("BACKUP_ROOT_INVALID", "Plain backup root is unavailable.")
        ensure_clean(self.store)
        head = git_head(self.store)
        backup_id = (
            "backup-"
            + utc_now().strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + head[:12]
            + "-"
            + uuid.uuid4().hex[:8]
        )
        destination = backup_root / backup_id
        if destination.exists():
            raise MemoryError("BACKUP_EXISTS", "Plain backup destination already exists.")
        destination.mkdir(mode=0o700)
        bundle = destination / "MemoryGraph-Simple.bundle"
        try:
            run_git(self.store, ["bundle", "create", str(bundle), "--all"])
            with tempfile.TemporaryDirectory(prefix="memorygraph-bundle-verify-") as value:
                probe = Path(value)
                subprocess.run([shutil.which("git") or "git", "init", "-q", str(probe)], check=True)
                completed = subprocess.run(
                    [
                        shutil.which("git") or "git",
                        "-C",
                        str(probe),
                        "bundle",
                        "verify",
                        str(bundle),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
                if completed.returncode:
                    raise MemoryError("BACKUP_VERIFY_FAILED", "Git bundle verification failed.")
            manifest = {
                "schema_version": 1,
                "type": "memorygraph-simple-plain-backup",
                "backup_id": backup_id,
                "created_at": iso(),
                "store": str(self.store),
                "head": head,
                "bundle": bundle.name,
                "bundle_hash": sha256_file(bundle),
                "encrypted": False,
                "pending_files": [],
                "coverage": {
                    "canonical_git": True,
                    "managed_queue": True,
                    "external_pending_labels": sorted((extra_pending_roots or {}).keys()),
                    "excluded_rebuildable": ["derived indexes", "taxonomy caches", "process locks"],
                    "undeclared_external_inboxes_covered": False,
                },
            }
            sources = self.backup_state_files(extra_pending_roots)
            source_hashes = {}
            for relative, source in sorted(sources.items()):
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # ExFAT rejects macOS chflags used by shutil.copy2/copystat.
                # Copy bytes atomically; queue metadata is represented in the
                # signed/hash manifest rather than filesystem flags.
                raw = source.read_bytes()
                if source.suffix == ".gz":
                    try:
                        from . import queue_archive
                    except ImportError:
                        import queue_archive
                    _, archived_files = queue_archive.unpack(raw)
                    unsafe = any(
                        secret_reasons(json.loads(data)) for data in archived_files.values()
                    )
                else:
                    unsafe = secret_reasons(json.loads(raw))
                if unsafe:
                    raise MemoryError(
                        "BACKUP_UNSAFE_STATE",
                        "Durable state contains possible secrets; backup refused.",
                    )
                source_hashes[relative] = sha256_bytes(raw)
                atomic_bytes(target, raw, 0o600)
                if sha256_file(target) != sha256_file(source):
                    raise MemoryError("BACKUP_VERIFY_FAILED", "Pending backup copy hash failed.")
                manifest["pending_files"].append(
                    {"path": relative, "hash": sha256_file(target), "size": target.stat().st_size}
                )
            current_sources = self.backup_state_files(extra_pending_roots)
            if (
                git_head(self.store) != head
                or set(current_sources) != set(sources)
                or any(sha256_file(p) != source_hashes[k] for k, p in current_sources.items())
            ):
                raise MemoryError(
                    "BACKUP_SOURCE_CHANGED",
                    "Source state changed during backup; retry from a stable state.",
                )
            atomic_json(destination / "manifest.json", manifest, 0o600)
            lines = [
                f"{sha256_file(bundle).split(':', 1)[1]}  {bundle.name}",
                f"{sha256_file(destination / 'manifest.json').split(':', 1)[1]}  manifest.json",
            ]
            lines.extend(
                f"{str(item['hash']).split(':', 1)[1]}  {item['path']}"
                for item in manifest["pending_files"]
            )
            atomic_bytes(destination / "SHA256SUMS", ("\n".join(lines) + "\n").encode(), 0o600)
            # macOS may materialize AppleDouble sidecars on ExFAT for extended
            # attributes. They are non-authoritative backup noise and are never
            # declared in the manifest.
            for sidecar in destination.rglob("._*"):
                if sidecar.is_file() and not sidecar.is_symlink():
                    sidecar.unlink()
            return {"status": "BACKUP_VERIFIED", "directory": str(destination), **manifest}
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    @staticmethod
    def verify_backup(directory: Path) -> dict[str, object]:
        directory = directory.expanduser().absolute()
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise MemoryError("BACKUP_VERIFY_FAILED", "Backup symlinks are not followed.")
        directory = directory.resolve()
        manifest = load_json(directory / "manifest.json")
        if manifest.get("bundle") != "MemoryGraph-Simple.bundle":
            raise MemoryError("BACKUP_VERIFY_FAILED", "Unexpected bundle path.")
        bundle = directory / manifest["bundle"]
        if bundle.is_symlink():
            raise MemoryError("BACKUP_VERIFY_FAILED", "Bundle must not be a symlink.")
        if manifest.get("type") != "memorygraph-simple-plain-backup" or sha256_file(
            bundle
        ) != manifest.get("bundle_hash"):
            raise MemoryError("BACKUP_VERIFY_FAILED", "Plain backup manifest/hash failed.")
        sums = directory / "SHA256SUMS"
        expected_lines = {
            f"{sha256_file(bundle).split(':', 1)[1]}  {bundle.name}",
            f"{sha256_file(directory / 'manifest.json').split(':', 1)[1]}  manifest.json",
        }
        pending_files = manifest.get("pending_files")
        if not isinstance(pending_files, list):
            raise MemoryError(
                "BACKUP_VERIFY_FAILED", "Plain backup pending file manifest is malformed."
            )
        seen = set()
        for item in pending_files:
            if not isinstance(item, dict):
                raise MemoryError(
                    "BACKUP_VERIFY_FAILED", "Plain backup pending entry is malformed."
                )
            relative = safe_relative(str(item.get("path") or ""))
            if (
                relative.as_posix() in seen
                or len(relative.parts) < 2
                or relative.parts[0] not in {"pending", "external"}
            ):
                raise MemoryError("BACKUP_VERIFY_FAILED", "Duplicate or unknown state family.")
            seen.add(relative.as_posix())
            path = directory.joinpath(*relative.parts)
            if (
                any(p.is_symlink() for p in (path, *path.parents))
                or not path.is_file()
                or path.stat().st_size != item.get("size")
                or sha256_file(path) != item.get("hash")
            ):
                raise MemoryError("BACKUP_VERIFY_FAILED", "Plain backup pending file hash failed.")
            expected_lines.add(f"{str(item['hash']).split(':', 1)[1]}  {relative.as_posix()}")
        declared = {
            bundle.resolve(),
            (directory / "manifest.json").resolve(),
            sums.resolve(),
            *{
                directory.joinpath(*safe_relative(str(item["path"])).parts).resolve()
                for item in pending_files
                if isinstance(item, dict)
            },
        }
        actual = {
            path.resolve()
            for path in directory.rglob("*")
            if path.is_file() and not path.name.startswith("._")
        }
        if actual != declared:
            raise MemoryError("BACKUP_VERIFY_FAILED", "Plain backup contains undeclared files.")
        if (
            sums.is_symlink()
            or not sums.is_file()
            or set(sums.read_text(encoding="utf-8").splitlines()) != expected_lines
        ):
            raise MemoryError("BACKUP_VERIFY_FAILED", "Plain backup SHA256SUMS failed.")
        with tempfile.TemporaryDirectory(prefix="memorygraph-bundle-verify-") as value:
            probe = Path(value)
            subprocess.run([shutil.which("git") or "git", "init", "-q", str(probe)], check=True)
            completed = subprocess.run(
                [shutil.which("git") or "git", "-C", str(probe), "bundle", "verify", str(bundle)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            if completed.returncode:
                raise MemoryError("BACKUP_VERIFY_FAILED", "Plain Git bundle verification failed.")
        return {
            "status": "BACKUP_VERIFIED",
            "backup_id": manifest["backup_id"],
            "head": manifest["head"],
        }

    @staticmethod
    def restore_backup(directory: Path, destination: Path):
        """Restore to a new directory only; external queues stay staged."""
        SimpleMemory.verify_backup(directory)
        directory = directory.resolve()
        destination = destination.expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise MemoryError("RESTORE_EXISTS", "Restore target must not exist.")
        if any(p.is_symlink() for p in destination.parents):
            raise MemoryError("RESTORE_EXISTS", "Restore parents must not be symlinks.")
        manifest = load_json(directory / "manifest.json")
        subprocess.run(
            [
                shutil.which("git") or "git",
                "clone",
                "-q",
                *[
                    arg
                    for key, value in APP_GIT_CONFIG.items()
                    for arg in ("--config", f"{key}={value}")
                ],
                str(directory / manifest["bundle"]),
                str(destination),
            ],
            check=True,
        )
        for item in manifest["pending_files"]:
            relative = safe_relative(item["path"])
            parts = relative.parts
            if parts[0] == "pending":
                target = destination / ".queue" / Path(*parts[1:])
            elif parts[0] == "external":
                target = destination / ".queue/restored-external" / Path(*parts[1:])
            else:
                raise MemoryError("BACKUP_VERIFY_FAILED", "Unknown restored state family.")
            if any(p.is_symlink() for p in (target, *target.parents)):
                raise MemoryError(
                    "BACKUP_VERIFY_FAILED", "Restored state path must not follow symlinks."
                )
            data = (directory / relative).read_bytes()
            if sha256_bytes(data) != item["hash"]:
                raise MemoryError(
                    "BACKUP_CHANGED",
                    "Backup state changed during restore; partial target retained.",
                )
            atomic_bytes(target, data, 0o600)
        if git_head(destination) != manifest["head"]:
            raise MemoryError("BACKUP_CHANGED", "Restored HEAD does not match manifest.")
        return {
            "status": "RESTORED",
            "store": str(destination),
            "head": git_head(destination),
            "external_queues_staged": str(destination / ".queue/restored-external"),
        }

    def revert(
        self, commit: str, confirmation_text: str, backup_root: Path | None = None
    ) -> dict[str, object]:
        with StoreLock(self.store):
            ensure_queue_ready(self.store, canonical_only=True)
            ensure_clean(self.store)
            if not COMMIT_RE.fullmatch(commit):
                raise MemoryError(
                    "COMMIT_INVALID", "Revert commit must be a full lowercase Git commit."
                )
            required = f"确认回滚记忆提交 {commit}"
            if confirmation_text != required:
                raise MemoryError(
                    "CONFIRMATION_REQUIRED", "Exact owner revert confirmation is required."
                )
            if run_git(
                self.store, ["cat-file", "-e", commit + "^{commit}"], check=False
            ).returncode:
                raise MemoryError("COMMIT_INVALID", "Revert commit is unavailable.")
            completed = run_git(self.store, ["revert", "--no-edit", commit], check=False)
            if completed.returncode:
                run_git(self.store, ["revert", "--abort"], check=False)
                raise MemoryError(
                    "REVERT_FAILED", "Git revert failed without changing canonical history."
                )
            head = git_head(self.store)
            backup = None
            if backup_root is not None:
                try:
                    backup = self.create_backup(backup_root)
                except MemoryError as exc:
                    backup = {"status": "BACKUP_FAILED", "code": exc.code, "message": exc.message}
            status = (
                "REVERT_COMMITTED_BACKUP_FAILED"
                if isinstance(backup, dict) and backup.get("status") == "BACKUP_FAILED"
                else "REVERT_COMMITTED"
            )
            return {"status": status, "reverted_commit": commit, "commit": head, "backup": backup}


def default_store() -> str:
    return os.environ.get(
        "MEMORY_GRAPH_HOME",
        os.environ.get("MEMORY_GRAPH_SIMPLE_HOME", str(Path.home() / ".memory-graph" / "store")),
    )


def read_object(path: str) -> dict[str, object]:
    return load_json(Path(path).expanduser().resolve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default=default_store())
    parser.add_argument("--cwd", default=os.getcwd())
    commands = parser.add_subparsers(dest="command", required=True)

    item = commands.add_parser("init")
    item.add_argument("--project-root", required=True)
    item.add_argument("--project-id", required=True)
    item.add_argument("--title", required=True)
    item.add_argument("--backup-root")
    item.add_argument("--confirmed", action="store_true")
    item = commands.add_parser("register-project")
    item.add_argument("--project-root", required=True)
    item.add_argument("--project-id", required=True)
    item.add_argument("--title", required=True)
    item.add_argument("--confirmed", action="store_true")
    commands.add_parser("status")
    for name in ("start", "recall"):
        item = commands.add_parser(name)
        if name == "start":
            item.add_argument("--role", choices=["main", "subagent"], required=True)
        item.add_argument("--query")
    item = commands.add_parser("propose")
    item.add_argument("--candidate", required=True)
    item.add_argument("--agent-id", required=True)
    item = commands.add_parser("batch-create")
    item.add_argument("--candidate-id", action="append", required=True)
    item = commands.add_parser("batch-review")
    item.add_argument("--batch-id", required=True)
    item = commands.add_parser("approve")
    item.add_argument("--batch-id", required=True)
    item.add_argument("--expected-digest", required=True)
    item.add_argument("--confirmation-text", required=True)
    item.add_argument("--agent-id", required=True)
    item.add_argument("--backup-root")
    item = commands.add_parser("import-pending")
    item.add_argument("--inventory", required=True)
    item.add_argument("--agent-id", required=True)
    item.add_argument("--confirmed", action="store_true")
    item = commands.add_parser("backup")
    item.add_argument("--backup-root")
    item = commands.add_parser("verify-backup")
    item.add_argument("--directory", required=True)
    item = commands.add_parser("revert")
    item.add_argument("--commit", required=True)
    item.add_argument("--confirmation-text", required=True)
    item.add_argument("--backup-root")

    args = parser.parse_args(argv)
    store = SimpleMemory(Path(args.store))
    cwd = Path(args.cwd)
    try:
        if args.command == "init":
            result = store.init(
                Path(args.project_root),
                args.project_id,
                args.title,
                Path(args.backup_root) if args.backup_root else None,
                confirmed=args.confirmed,
            )
        elif args.command == "register-project":
            result = store.register_project(
                Path(args.project_root), args.project_id, args.title, confirmed=args.confirmed
            )
        elif args.command == "status":
            result = store.status(cwd)
        elif args.command == "start":
            result = store.start(cwd, args.role, args.query)
        elif args.command == "recall":
            result = store.recall(cwd, args.query or "")
        elif args.command == "propose":
            candidate = read_object(args.candidate)
            candidate["submitted_by_agent"] = args.agent_id
            result = store.propose(candidate, cwd)
        elif args.command == "batch-create":
            result = store.create_batch(args.candidate_id)
        elif args.command == "batch-review":
            result = store.review(args.batch_id)
        elif args.command == "approve":
            result = store.approve(
                args.batch_id,
                args.expected_digest,
                args.confirmation_text,
                committing_agent=args.agent_id,
                backup_root=Path(args.backup_root) if args.backup_root else None,
            )
        elif args.command == "import-pending":
            result = store.import_pending(
                Path(args.inventory),
                cwd,
                submitting_agent=args.agent_id,
                confirmed=args.confirmed,
            )
        elif args.command == "backup":
            result = store.create_backup(Path(args.backup_root) if args.backup_root else None)
        elif args.command == "verify-backup":
            result = SimpleMemory.verify_backup(Path(args.directory))
        else:
            result = store.revert(
                args.commit,
                args.confirmation_text,
                Path(args.backup_root) if args.backup_root else None,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (
        MemoryError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        if isinstance(exc, MemoryError):
            code, message, details = exc.code, exc.message, exc.details
        else:
            code, message, details = "MEMORY_FAILED", type(exc).__name__, {}
        print(
            json.dumps(
                {"status": code, "message": message, **details},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
