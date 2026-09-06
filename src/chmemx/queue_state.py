"""Bounded, reconstructable queue metadata. Pending bodies remain separate files.

No automatic deletion. Statistics use logical uploads, not raw event count.
"""

from __future__ import annotations
from contextlib import contextmanager
import copy
import json
from itertools import islice
import time
from pathlib import Path

from .runtime import simple_memory as core
from .runtime import queue_archive

TERMINAL = {
    "ACTIVE_COMMITTED",
    "COMMITTED_NOT_ACTIVE",
    "COMMIT_NOT_CURRENT",
    "EXACT_DUPLICATE",
    "REJECTED",
    "CANCELLED",
    "UPLOAD_DATA_MISSING",
}

DEFAULT_LIMITS = {
    "max_uploads": 10000,
    "max_events": 1000,
    "max_sources": 256,
    "max_nonces": 10000,
    "requests_per_minute": 120,
    "max_review_revisions": 16,
}


class QueueState:
    def __init__(self, store):
        self.store = Path(store)
        self.root = self.store / ".queue/chmemx"
        self.path = self.root / "state.json"

    @contextmanager
    def locked(self):
        with core.StoreLock(self.root / "locks/metadata"):
            if self.path.exists():
                data = core.load_json(self.path)
            else:
                data = {
                    "schema_version": 1,
                    "uploads": {},
                    "batches": {},
                    "statistics": {},
                    "rates": {},
                    "nonces": {},
                    "event_files": 0,
                    "suppressed_events": 0,
                }
                # One-time bounded legacy indexing, never on every upload.
                files = list(islice((self.root / "events").glob("*.json"), 20001))
                uploads = list(islice((self.root / "uploads").glob("*.json"), 20001))
                if len(files) > 20000 or len(uploads) > 20000:
                    raise core.MemoryError(
                        "QUEUE_MIGRATION_REQUIRED",
                        "Legacy queue needs an explicit bounded migration.",
                    )
                for p, event in sorted(
                    ((p, core.load_json(p)) for p in files),
                    key=lambda item: (item[1].get("created_at", ""), item[0].name),
                ):
                    self._stat(
                        data,
                        event.get("source_agent", "legacy"),
                        event.get("status", "UNKNOWN"),
                        event.get("upload_id", p.stem),
                    )
                data["event_files"] = len(files)
                for p in uploads:
                    job = core.load_json(p)
                    data["uploads"][p.stem] = {
                        "status": job.get("status", "PENDING_CURATION"),
                        "source_agent": job.get("source_agent"),
                        "input_digest": job.get("input_digest"),
                    }
                    if job.get("batch_id"):
                        data["batches"][job["batch_id"]] = p.stem
            before = core.canonical_json(data)
            data.setdefault("event_sequence", data["event_files"])
            try:
                yield data
            finally:
                if core.canonical_json(data) != before or not self.path.exists():
                    core.atomic_json(self.path, data, 0o600)

    def limits(self):
        values = {
            **DEFAULT_LIMITS,
            **core.load_json(self.store / "policy.json").get("queue_limits", {}),
        }
        if any(type(v) != int or v < 1 for v in values.values()):
            raise core.MemoryError(
                "QUEUE_POLICY_INVALID", "Queue limits must be positive integers."
            )
        return values

    def admit(self, agent):
        limits = self.limits()
        bucket = int(time.time() // 60)
        with self.locked() as d:
            if agent not in d["statistics"] and len(d["statistics"]) >= limits["max_sources"]:
                raise core.MemoryError(
                    "SOURCE_CAPACITY_REACHED", "Source capacity reached; operator review required."
                )
            old = d["rates"].get(agent, {"bucket": bucket, "count": 0})
            n = old["count"] if old["bucket"] == bucket else 0
            if n >= limits["requests_per_minute"]:
                raise core.MemoryError(
                    "RATE_LIMITED", "Source request budget exhausted.", retryable=True
                )
            d["rates"][agent] = {"bucket": bucket, "count": n + 1}
            d["statistics"].setdefault(agent, {"counts": {}, "recent": []})

    @staticmethod
    def _stat(d, agent, status, logical_id):
        stats = d["statistics"].setdefault(agent, {"counts": {}, "recent": []})
        stats["counts"][status] = stats["counts"].get(status, 0) + 1
        if logical_id:
            old = next((x for x in stats["recent"] if x["id"] == logical_id), None)
            if old:
                old["outcome"] = status
            else:
                stats["recent"] = (stats["recent"] + [{"id": logical_id, "outcome": status}])[-5:]

    def record(self, agent, status, logical_id=None, raw_event=None):
        limits = self.limits()
        with self.locked() as d:
            if agent not in d["statistics"] and len(d["statistics"]) >= limits["max_sources"]:
                d["suppressed_events"] += 1
                return
            self._stat(d, agent, status, logical_id)
            if raw_event is not None and d["event_files"] < limits["max_events"]:
                d["event_files"] += 1
                d["event_sequence"] += 1
                prior_uncertainty = d.get("event_write_incomplete", False)
                d["event_write_incomplete"] = True
                core.atomic_json(
                    self.path, d, 0o600
                )  # reserve before writing; crash cannot reuse an audit filename
                core.atomic_json(
                    self.root / "events" / f"event-{d['event_sequence']:012d}.json",
                    raw_event,
                    0o600,
                )
                d["event_write_incomplete"] = prior_uncertainty
            else:
                d["suppressed_events"] += 1

    def stats(self, agent):
        with self.locked() as d:
            s = copy.deepcopy(d["statistics"].get(agent, {"counts": {}, "recent": []}))
        return {
            **s["counts"],
            "high_review": sum(x["outcome"] == "CONFLICT" for x in s["recent"]) >= 3,
            "window_unit": "last five distinct logical uploads",
            "recent": s["recent"],
        }

    def event_accounting(self):
        """Cheap health signal, not a filesystem audit or automatic counter repair."""
        with self.locked() as d:
            return {
                "status": "RECONCILIATION_RECOMMENDED"
                if d.get("event_write_incomplete")
                else "NO_INTERRUPTED_WRITE_RECORDED",
                "reserved_files": d["event_files"],
                "verification": "metadata-only; explicit maintenance compares physical files",
            }

    def reserve(self, upload_id, digest, agent, context=None):
        limits = self.limits()
        with self.locked() as d:
            old = d["uploads"].get(upload_id)
            if old:
                if old.get("input_digest", digest) != digest:
                    raise core.MemoryError(
                        "IDEMPOTENCY_CONFLICT", "Request ID was already used for different content."
                    )
                return
            if len(d["uploads"]) >= limits["max_uploads"]:
                raise core.MemoryError(
                    "QUEUE_CAPACITY_REACHED", "Pending data was preserved; maintenance required."
                )
            d["uploads"][upload_id] = {
                "status": "UPLOADING",
                "input_digest": digest,
                "source_agent": agent,
                "context": context,
            }

    def update(self, job):
        job = {k: v for k, v in job.items() if not k.startswith("_")}
        core.atomic_json(self.root / "uploads" / f"{job['upload_id']}.json", job, 0o600)
        with self.locked() as d:
            d["uploads"][job["upload_id"]] = {
                k: job.get(k)
                for k in (
                    "status",
                    "input_digest",
                    "source_agent",
                    "batch_id",
                    "candidate_id",
                    "context",
                )
            }
            if job.get("batch_id"):
                d["batches"][job["batch_id"]] = job["upload_id"]

    def by_batch(self, batch_id):
        with self.locked() as d:
            return d["batches"].get(batch_id)

    def assert_ready(self):
        core.ensure_queue_ready(self.store)

    def exists(self, upload_id):
        core.safe_id(upload_id, "upload id")
        return any(
            (self.root / folder / f"{upload_id}.json").exists()
            for folder in ("uploads", "receipts")
        )

    def load_job(self, upload_id):
        core.safe_id(upload_id, "upload id")
        path = self.root / "uploads" / f"{upload_id}.json"
        if path.exists():
            return self.checked_job(core.load_json(path), upload_id, "hot")
        receipt = core.load_json(self.root / "receipts" / f"{upload_id}.json")
        self.checked_job(receipt, upload_id, "receipt")
        if receipt.get("archive_path"):
            path = self.root / core.safe_relative(receipt["archive_path"])
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                raise core.MemoryError("ARCHIVE_PATH_INVALID", "Archive symlink rejected.")
            return self.checked_archive(receipt, path.read_bytes(), upload_id)
        return self.checked_job(receipt, upload_id, "receipt")

    @staticmethod
    def checked_archive(receipt, raw, upload_id):
        """One binding check for reads and destructive archive maintenance."""
        QueueState.checked_job(receipt, upload_id, "receipt")
        if core.sha256_bytes(raw) != receipt.get("archive_hash"):
            raise core.MemoryError("ARCHIVE_CHANGED", "Queue archive failed its byte seal.")
        try:
            metadata, files = queue_archive.unpack(raw)
            job = json.loads(files[f"chmemx/uploads/{upload_id}.json"])
        except (ValueError, KeyError, OSError) as error:
            raise core.MemoryError("ARCHIVE_INVALID", "Queue archive is unreadable.") from error
        job = QueueState.checked_job(job, upload_id, "archive")
        if metadata.get("upload_id") != upload_id or metadata.get("kind") != "upload":
            raise core.MemoryError("ARCHIVE_RECEIPT_MISMATCH", "Archive owner differs.")
        for field in (
            "upload_id",
            "input_digest",
            "source_agent",
            "identity_version",
            "context",
            "batch_id",
            "batch_digest",
        ):
            if job.get(field) != receipt.get(field):
                raise core.MemoryError(
                    "ARCHIVE_RECEIPT_MISMATCH", "Archive and receipt identity differ.", field=field
                )
        if not job.get("context"):
            candidate = job.get("candidate", {})
            if receipt.get("legacy_scope") != candidate.get("scope") or receipt.get(
                "legacy_project_root"
            ) != candidate.get("source", {}).get("project_root"):
                raise core.MemoryError("ARCHIVE_RECEIPT_MISMATCH", "Legacy context differs.")
        # Source bytes stay immutable; only the bound lifecycle receipt supplies proof.
        for field in ("status", "commit", "record_ids", "closed_at", "reason", "archived_at"):
            if field in receipt:
                job[field] = receipt[field]
        return job

    @staticmethod
    def checked_job(value, upload_id, storage):
        if (
            not isinstance(value, dict)
            or value.get("upload_id") != upload_id
            or not value.get("source_agent")
        ):
            raise core.MemoryError(
                "UPLOAD_STATE_INVALID", "Stored upload identity does not match its locator."
            )
        core.safe_id(value["source_agent"], "source agent")
        return {**value, "_storage": storage}

    def save_receipt(self, job):
        fields = (
            "upload_id",
            "input_digest",
            "source_agent",
            "context",
            "identity_version",
            "status",
            "created_at",
            "closed_at",
            "record_id",
            "commit_created",
            "record_ids",
            "batch_id",
            "batch_digest",
            "commit",
            "reason",
            "archive_path",
            "archive_hash",
            "archived_at",
            "purged_at",
            "audit_bucket",
            "legacy_scope",
            "legacy_project_root",
        )
        receipt = {k: job[k] for k in fields if k in job}
        receipt["type"] = "chmemx-request-receipt-v1"
        core.atomic_json(self.root / "receipts" / f"{job['upload_id']}.json", receipt, 0o600)

    def persist_if_hot(self, job):
        if job.get("_storage", "hot") == "hot":
            self.update(job)

    def consume_nonce(self, agent, nonce, expires_at):
        key = core.sha256_bytes(core.canonical_json([agent, nonce]))
        with self.locked() as d:
            if key in d["nonces"]:
                raise core.MemoryError("SIGNATURE_REPLAY", "Signed request already consumed.")
            # Keep legacy markers effective; migration must not resurrect old requests.
            if (self.root / "nonces" / f"{agent}-{nonce}.json").exists():
                raise core.MemoryError(
                    "SIGNATURE_REPLAY", "Legacy signed request already consumed."
                )
            if len(d["nonces"]) >= self.limits()["max_nonces"]:
                raise core.MemoryError(
                    "NONCE_CAPACITY_REACHED", "Expired nonce maintenance required."
                )
            d["nonces"][key] = {
                "agent": agent,
                "expires_at": expires_at,
                "consumed_at": time.time(),
            }

    def maintenance_plan(self):
        with self.locked() as d:
            return self._maintenance_plan(d)

    def _maintenance_plan(self, d):
        expired = sorted(k for k, x in d["nonces"].items() if x["expires_at"] + 300 < time.time())
        plan = {
            "type": "queue-maintenance-plan",
            "automatic_deletion": False,
            "uploads": len(d["uploads"]),
            "raw_events": d["event_files"],
            "suppressed_events": d["suppressed_events"],
            "nonce_entries": len(d["nonces"]),
            "expired_nonce_entries": len(expired),
            "expired_nonce_ids": expired,
            "state_digest": core.sha256_bytes(core.canonical_json(d)),
            "limits": self.limits(),
            "preserve": "Pending uploads, unresolved reviews, replay protection inside expiry plus skew.",
            "cleanup_status": "PLAN_ONLY; explicit nonce maintenance only; no upload/event file purge implemented",
        }
        plan["digest"] = core.sha256_bytes(core.canonical_json(plan))
        return plan

    def maintain_nonces(self, expected_digest):
        """Operator-only expiry cleanup; never removes Pending or event files."""
        with core.StoreLock(self.root / "locks/operation"), self.locked() as d:
            self.assert_ready()
            plan = self._maintenance_plan(d)
            if plan["digest"] != expected_digest:
                raise core.MemoryError(
                    "MAINTENANCE_PLAN_CHANGED", "Queue changed; review a fresh plan."
                )
            for key in plan["expired_nonce_ids"]:
                del d["nonces"][key]
            d["last_nonce_maintenance"] = {
                "plan_digest": expected_digest,
                "count": len(plan["expired_nonce_ids"]),
                "at": core.iso(),
            }
            return {
                "status": "EXPIRED_NONCES_PRUNED",
                **d["last_nonce_maintenance"],
                "uploads_removed": 0,
                "files_removed": 0,
            }
