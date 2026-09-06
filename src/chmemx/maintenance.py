"""Explicit queue lifecycle maintenance. Never edits canonical memory or Git history.

Plans bind the current inventory. Interrupted filesystem transactions retain a
journal and block queue mutations until an operator completes or rolls them back.
"""

import copy
from contextlib import contextmanager
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from .runtime import simple_memory as core, queue_archive
from .queue_state import TERMINAL


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


class Maintenance:
    def __init__(self, service):
        self.service = service
        self.queue = service.queue
        self.base = service.runtime.queue
        self.control = self.queue.root / "maintenance"
        self._git_process = None
        self._canonical_cache = {}
        self._proof_cache = {}
        if any(p.is_symlink() for p in (self.control, *self.control.parents)):
            raise core.MemoryError(
                "MAINTENANCE_PATH_INVALID", "Maintenance directories must not be symlinks."
            )

    def path(self, relative):
        relative = core.safe_relative(relative)
        path = self.base.joinpath(*relative.parts)
        allowed = {"candidates", "batches", "archive", "chmemx"}
        if not relative.parts or relative.parts[0] not in allowed:
            raise core.MemoryError(
                "MAINTENANCE_PATH_INVALID", "Only managed queue paths are eligible."
            )
        if relative.parts[0] == "chmemx" and (
            len(relative.parts) < 2
            or relative.parts[1]
            not in {"uploads", "events", "receipts", "archives", "history", "state.json"}
        ):
            raise core.MemoryError(
                "MAINTENANCE_PATH_INVALID", "Path is outside durable queue state."
            )
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise core.MemoryError("MAINTENANCE_PATH_INVALID", "Symlinks are never followed.")
        return path

    def inventory(self):
        files, parsed, groups = {}, {}, {}
        patterns = {
            "uploads": "chmemx/uploads/*.json",
            "candidates": "candidates/*.json",
            "batches": "batches/*.json",
            "batch_history": "archive/*.json",
            "events": "chmemx/events/*.json",
            "receipts": "chmemx/receipts/*.json",
            "archives": "chmemx/archives/**/*.gz",
            "review_history": "chmemx/history/*.gz",
            "maintenance_receipts": "chmemx/maintenance/receipts/*.json",
        }
        for family, pattern in patterns.items():
            names = []
            for p in self.base.glob(pattern):
                if len(files) >= 500000:
                    raise core.MemoryError(
                        "INVENTORY_TOO_LARGE", "Inventory exceeds the bounded maintenance scan."
                    )
                rel = p.relative_to(self.base).as_posix()
                if family == "maintenance_receipts":
                    if any(x.is_symlink() for x in (p, *p.parents)):
                        raise core.MemoryError(
                            "MAINTENANCE_PATH_INVALID", "Receipt symlink rejected."
                        )
                else:
                    p = self.path(rel)
                files[rel] = {"sha256": core.sha256_file(p), "bytes": p.stat().st_size}
                names.append(rel)
                if p.suffix == ".json":
                    value = core.load_json(p)
                    if family == "uploads":
                        fields = (
                            "upload_id",
                            "input_digest",
                            "identity_version",
                            "context",
                            "source_agent",
                            "status",
                            "created_at",
                            "closed_at",
                            "batch_id",
                            "batch_digest",
                            "candidate_id",
                            "review_history",
                            "commit",
                            "record_ids",
                            "reason",
                        )
                        value = {k: value[k] for k in fields if k in value} | {
                            "candidate": {
                                "scope": value.get("candidate", {}).get("scope"),
                                "source": {
                                    "project_root": value.get("candidate", {})
                                    .get("source", {})
                                    .get("project_root")
                                },
                            }
                        }
                    elif family in {"batches", "batch_history"}:
                        value = {
                            k: value[k]
                            for k in ("batch_id", "status", "created_at", "candidate_ids")
                            if k in value
                        }
                    elif family == "candidates":
                        value = {"candidate_id": value.get("candidate_id")}
                    elif family == "events":
                        value = {
                            k: value[k] for k in ("upload_id", "status", "created_at") if k in value
                        }
                    parsed[rel] = value
            groups[family] = sorted(names)
        state_path = self.path("chmemx/state.json")
        state = (
            core.load_json(state_path)
            if state_path.exists()
            else {
                "schema_version": 1,
                "uploads": {},
                "batches": {},
                "statistics": {},
                "rates": {},
                "nonces": {},
                "event_files": 0,
                "event_sequence": 0,
                "suppressed_events": 0,
            }
        )
        if state_path.exists():
            files["chmemx/state.json"] = {
                "sha256": core.sha256_file(state_path),
                "bytes": state_path.stat().st_size,
            }
        return files, parsed, groups, state

    @contextmanager
    def canonical_reader(self):
        self._canonical_head = core.git_head(self.service.store)
        self._canonical_cache = {}
        self._proof_cache = {}
        self._history_index = None
        process = subprocess.Popen(
            [shutil.which("git") or "git", "-C", str(self.service.store), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._git_process = process
        try:
            yield
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            process.stdout.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            self._git_process = None

    def canonical_json(self, relative, revision=None):
        relative = core.safe_relative(relative).as_posix()
        revision = revision or self._canonical_head
        key = (revision, relative)
        if key in self._canonical_cache:
            return self._canonical_cache[key]
        process = self._git_process
        if process is None:
            raise core.MemoryError("MAINTENANCE_READER_REQUIRED", "Canonical reader is not active.")
        process.stdin.write(f"{revision}:{relative}\n".encode("utf-8"))
        process.stdin.flush()
        header = process.stdout.readline()
        if header.rstrip().endswith(b" missing"):
            self._canonical_cache[key] = None
            return None
        parts = header.split()
        if (
            len(parts) != 3
            or parts[1] != b"blob"
            or not parts[2].isdigit()
            or int(parts[2]) > queue_archive.MAX_BYTES
        ):
            raise core.MemoryError("CANONICAL_READ_FAILED", "Unexpected canonical object response.")
        raw = process.stdout.read(int(parts[2]))
        if len(raw) != int(parts[2]) or process.stdout.read(1) != b"\n":
            raise core.MemoryError("CANONICAL_READ_FAILED", "Canonical object was truncated.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise core.MemoryError("STORE_INVALID", "Canonical JSON is not an object.")
        # Approval bodies are not retained in the maintenance cache.
        if relative.startswith("approvals/"):
            if (
                value.get("type") != "memorygraph-simple-approval"
                or not isinstance(value.get("records"), list)
                or not value["records"]
                or any(not isinstance(r, dict) for r in value["records"])
            ):
                raise core.MemoryError("APPROVAL_PROOF_INVALID", "Canonical approval is invalid.")
            value = {
                **{k: value[k] for k in ("batch_id", "batch_digest", "committed_by_agent")},
                "records": [
                    {
                        k: r.get(k)
                        for k in ("id", "scope", "project_id", "class", "key", "upload_id")
                    }
                    for r in value["records"]
                ],
            }
        self._canonical_cache[key] = value
        return value

    def historical_commit(self, batch_id):
        """One bounded, streamed ancestry walk shared by all missing receipts in this operation."""
        if self._history_index is None:
            index = {}
            command = [
                shutil.which("git") or "git",
                "-C",
                str(self.service.store),
                "log",
                "--format=commit:%H",
                "--name-only",
                "--diff-filter=A",
                "--no-renames",
                self._canonical_head,
                "--",
                "approvals",
            ]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                commit = None
                for n, raw in enumerate(process.stdout):
                    if n >= 1500000:
                        raise core.MemoryError(
                            "APPROVAL_HISTORY_LIMIT", "Approval history needs operator review."
                        )
                    line = raw.decode("utf-8").strip()
                    if line.startswith("commit:"):
                        commit = line.removeprefix("commit:")
                        if not core.COMMIT_RE.fullmatch(commit):
                            raise core.MemoryError(
                                "APPROVAL_HISTORY_INVALID", "Invalid history revision."
                            )
                    elif (
                        line.startswith("approvals/")
                        and line.endswith(".json")
                        and line.count("/") == 1
                    ):
                        if commit is None:
                            raise core.MemoryError(
                                "APPROVAL_HISTORY_INVALID", "Missing history revision."
                            )
                        bid = core.safe_id(Path(line).stem, "batch id")
                        index.setdefault(bid, commit)
                if process.wait(timeout=5):
                    raise core.MemoryError(
                        "APPROVAL_HISTORY_UNAVAILABLE", "Cannot read approval history."
                    )
            finally:
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            self._history_index = index
        return self._history_index.get(batch_id)

    def proof_result(self, batch_id, expected_digest=None, need_commit=False, upload_id=None):
        batch_id = core.safe_id(batch_id, "batch id")
        receipt = self.canonical_json(f"approvals/{batch_id}.json")
        historical = None
        if receipt is None:
            historical = self.historical_commit(batch_id)
            if historical is None:
                return None
            receipt = self.canonical_json(f"approvals/{batch_id}.json", historical)
        if receipt is None or receipt.get("batch_id") != batch_id:
            raise core.MemoryError("APPROVAL_PROOF_INVALID", "Canonical approval identity differs.")
        if upload_id is not None and any(
            r.get("upload_id") != upload_id for r in receipt["records"]
        ):
            raise core.MemoryError(
                "APPROVAL_UPLOAD_MISMATCH", "Approval belongs to another upload."
            )
        if expected_digest is not None and receipt["batch_digest"] != expected_digest:
            raise core.MemoryError(
                "BATCH_CHANGED", "Canonical receipt differs from the queued digest."
            )
        if batch_id not in self._proof_cache:
            active = []
            for record in receipt["records"] if historical is None else []:
                root = (
                    "global"
                    if record["scope"] == "global"
                    else "projects/" + core.safe_id(record["project_id"], "project id")
                )
                index = self.canonical_json(f"{root}/active-index.json") or {"entries": {}}
                if index["entries"].get(f"{record['class']}:{record['key']}") == record["id"]:
                    active.append(record["id"])
            self._proof_cache[batch_id] = {
                "status": "COMMIT_NOT_CURRENT"
                if historical
                else "ACTIVE_COMMITTED"
                if len(active) == len(receipt["records"])
                else "COMMITTED_NOT_ACTIVE",
                "batch_id": batch_id,
                "batch_digest": receipt["batch_digest"],
                "record_ids": [r["id"] for r in receipt["records"]],
                "active_record_ids": active,
                "checked_head": self._canonical_head,
                "committed_by_agent": receipt["committed_by_agent"],
            }
        result = copy.deepcopy(self._proof_cache[batch_id])
        if historical:
            result["commit"] = historical
        elif need_commit:
            result["commit"] = (
                core.run_git(
                    self.service.store,
                    [
                        "log",
                        "-1",
                        "--format=%H",
                        self._canonical_head,
                        "--",
                        f"approvals/{batch_id}.json",
                    ],
                )
                .stdout.decode()
                .strip()
            )
        return result

    def resolved_job(self, job, need_commit=False):
        result = copy.deepcopy(job)
        if result.get("status") not in {
            "EXACT_DUPLICATE",
            "CANCELLED",
            "REJECTED",
            "UPLOAD_DATA_MISSING",
        }:
            if result.get("batch_id") and result.get("batch_digest"):
                committed = self.proof_result(
                    result["batch_id"],
                    result["batch_digest"],
                    need_commit,
                    upload_id=result["upload_id"] if result.get("identity_version") == 2 else None,
                )
                if committed:
                    result.update(committed)
                elif result.get("commit"):
                    result["status"] = "COMMIT_NOT_CURRENT"
        return result

    def plan(self, action="archive", older_than_days=30, limit=100, *, cutoff=None):
        if (
            action not in {"archive", "purge", "reconcile", "expire-receipts"}
            or type(older_than_days) != int
            or not 0 <= older_than_days <= 36500
            or type(limit) != int
            or not 1 <= limit <= 500
        ):
            raise core.MemoryError(
                "MAINTENANCE_ARGUMENT_INVALID", "Invalid action, age or per-family limit."
            )
        cutoff = time.time() - older_than_days * 86400 if cutoff is None else cutoff
        if not isinstance(cutoff, (float, int)) or not 0 <= cutoff <= time.time():
            raise core.MemoryError("MAINTENANCE_ARGUMENT_INVALID", "Invalid cutoff.")
        if action == "expire-receipts" and (
            older_than_days < 180 or cutoff > time.time() - 180 * 86400
        ):
            raise core.MemoryError(
                "RECEIPT_RETENTION_MINIMUM",
                "Request receipts require at least 180 days before explicit expiry.",
            )
        with (
            core.StoreLock(self.queue.root / "locks/operation"),
            core.StoreLock(self.service.store),
            self.canonical_reader(),
        ):
            self.queue.assert_ready()
            return self._plan(action, older_than_days, limit, cutoff)[0]

    def _plan(self, action, days, limit, cutoff):
        core.ensure_clean(self.service.store)
        inventory = self.inventory()
        files, parsed, groups, state = inventory
        jobs = {}
        for rel in groups["uploads"]:
            job = parsed[rel]
            if (
                job.get("upload_id") != Path(rel).stem
                or not job.get("input_digest")
                or not job.get("source_agent")
            ):
                raise core.MemoryError(
                    "QUEUE_JOB_INVALID", "Upload metadata cannot be reconciled safely."
                )
            jobs[job["upload_id"]] = self.resolved_job(job)

        def aged(value):
            value = timestamp(value)
            return value is not None and value <= cutoff

        terminal = [
            uid
            for uid, job in jobs.items()
            if job["status"] in TERMINAL and aged(job.get("closed_at", job.get("created_at")))
        ]
        terminal.sort(
            key=lambda uid: (jobs[uid].get("closed_at", jobs[uid].get("created_at", "")), uid)
        )
        live_candidate_ids = {
            job.get("candidate_id") for job in jobs.values() if job["status"] not in TERMINAL
        }
        current_batch_candidates = {
            cid
            for rel in groups["batches"]
            if parsed[rel].get("status") == "pending"
            for cid in parsed[rel].get("candidate_ids", [])
        }
        batches_by_id = {}
        pending_owners = {}
        pending_paths = set(groups["batches"])
        for rel in groups["batches"] + groups["batch_history"]:
            batch = parsed[rel]
            batches_by_id.setdefault(batch.get("batch_id"), []).append((rel, batch))
            if rel in pending_paths and batch.get("status") == "pending":
                for cid in batch.get("candidate_ids", []):
                    pending_owners.setdefault(cid, set()).add(batch.get("batch_id"))
        targets = {"jobs": [], "reviews": [], "events": [], "archives": [], "receipts": []}
        selected_jobs = set(terminal[:limit]) if action == "archive" else set()

        def job_files(uid, closed):
            job = jobs[uid]
            bids = {x["batch_id"] for x in job.get("review_history", []) if x.get("batch_id")}
            if closed and job.get("batch_id"):
                bids.add(job["batch_id"])
            result = []
            for rel, batch in [item for bid in bids for item in batches_by_id.get(bid, [])]:
                if not closed and (
                    batch.get("status") != "review_replaced" or not aged(batch.get("created_at"))
                ):
                    continue
                cids = set(batch.get("candidate_ids", []))
                if cids & live_candidate_ids:
                    continue
                if not closed and cids & current_batch_candidates:
                    continue
                result.append(rel)
                for cid in cids:
                    c = f"candidates/{core.safe_id(cid, 'candidate id')}.json"
                    if c in files:
                        # Never remove a candidate referenced by a different current batch.
                        other_live = bool(pending_owners.get(cid, set()) - bids)
                        if not other_live:
                            result.append(c)
            if closed:
                result.append(f"chmemx/uploads/{uid}.json")
                if f"chmemx/history/{uid}.json.gz" in files:
                    result.append(f"chmemx/history/{uid}.json.gz")
            return sorted(set(result))

        if action == "archive":
            for uid in terminal[:limit]:
                targets["jobs"].append(
                    {"upload_id": uid, "status": jobs[uid]["status"], "files": job_files(uid, True)}
                )
            for uid in sorted(jobs):
                if uid in selected_jobs or len(targets["reviews"]) >= limit:
                    continue
                old = job_files(uid, False)
                if old:
                    targets["reviews"].append({"upload_id": uid, "files": old})
            for rel in groups["events"]:
                event = parsed[rel]
                if aged(event.get("created_at")) and (
                    event.get("status") in TERMINAL or event.get("upload_id") in selected_jobs
                ):
                    targets["events"].append(rel)
                    if len(targets["events"]) >= limit:
                        break
        if action == "purge":
            for rel in groups["archives"]:
                meta, _ = queue_archive.unpack(self.path(rel).read_bytes())
                if aged(meta.get("archived_at")):
                    targets["archives"].append(rel)
                    if len(targets["archives"]) >= limit:
                        break
        if action == "expire-receipts":
            for rel in groups["receipts"]:
                receipt = parsed[rel]
                if (
                    receipt.get("status") in TERMINAL - {"UPLOAD_DATA_MISSING"}
                    and not receipt.get("archive_path")
                    and receipt.get("upload_id") not in jobs
                    and aged(receipt.get("closed_at", receipt.get("created_at")))
                ):
                    targets["receipts"].append(rel)
                    if len(targets["receipts"]) >= limit:
                        break
        missing = sorted(uid for uid in state["uploads"] if uid not in jobs)
        unindexed = sorted(uid for uid in jobs if uid not in state["uploads"])
        result = {
            "type": "chmemx-maintenance-plan-v1",
            "store": str(self.service.store),
            "store_head": core.git_head(self.service.store),
            "action": action,
            "older_than_days": days,
            "cutoff": cutoff,
            "planned_at": dt.datetime.fromtimestamp(
                cutoff + days * 86400, dt.timezone.utc
            ).isoformat(),
            "expires_at": cutoff + days * 86400 + 86400,
            "limit_per_family": limit,
            "targets": targets,
            "inventory": {
                k: {"files": len(names), "bytes": sum(files[n]["bytes"] for n in names)}
                for k, names in groups.items()
            },
            "inventory_digest": core.sha256_bytes(core.canonical_json(files)),
            "state_digest": core.sha256_bytes(core.canonical_json(state)),
            "missing_upload_files": missing[:limit],
            "unindexed_upload_files": unindexed[:limit],
            "reconciliation_totals": {"missing": len(missing), "unindexed": len(unindexed)},
            "event_accounting": {
                "reserved_files": state["event_files"],
                "actual_files": len(groups["events"]),
                "interrupted_write_recorded": bool(state.get("event_write_incomplete")),
                "counts_match": state["event_files"] == len(groups["events"]),
            },
            "blockers": []
            if action == "reconcile" or not (missing or unindexed)
            else ["QUEUE_RECONCILIATION_REQUIRED"],
            "preserve": "Unreviewed/conflicting jobs, current candidates, canonical Active/Git, and minimal idempotency receipts.",
            "effect": "Archive with retained history"
            if action == "archive"
            else "Permanent compressed-payload removal; receipts remain"
            if action == "purge"
            else "Forget aged request-ID bindings; expired IDs may be treated as new requests; canonical memory stays intact"
            if action == "expire-receipts"
            else "Repair metadata; missing payloads become explicit tombstones, never invented content",
            "automatic": False,
        }
        result["archive_token"] = core.sha256_bytes(
            core.canonical_json([result["inventory_digest"], cutoff, targets])
        ).split(":")[1]
        preview = self._changes(result, inventory)
        result["target_files"] = {
            rel: {
                "before_hash": files.get(rel, {}).get("sha256"),
                "before_bytes": files.get(rel, {}).get("bytes", 0),
                "after_hash": core.sha256_bytes(data) if data is not None else None,
                "after_bytes": len(data) if data is not None else 0,
            }
            for rel, data in preview.items()
        }
        result["estimated_net_bytes_released"] = sum(
            item["before_bytes"] - item["after_bytes"] for item in result["target_files"].values()
        )
        result["journal_bytes_required"] = sum(
            item["before_bytes"] + item["after_bytes"] for item in result["target_files"].values()
        )
        result["digest"] = core.sha256_bytes(core.canonical_json(result))
        if core.git_head(self.service.store) != self._canonical_head:
            raise core.MemoryError("HEAD_CHANGED", "Canonical state moved during inventory.")
        return result, preview

    def _changes(self, plan, inventory):
        files, parsed, groups, state = inventory
        state = copy.deepcopy(state)
        changes = {}
        now = plan["planned_at"]

        def add_archive(rel, names, metadata, prior=None):
            data = {}
            if prior and self.path(prior).exists():
                _, data = queue_archive.unpack(self.path(prior).read_bytes())
            for name in names:
                raw = self.path(name).read_bytes()
                if core.sha256_bytes(raw) != files[name]["sha256"]:
                    raise core.MemoryError(
                        "MAINTENANCE_PLAN_CHANGED", "Archive input moved after inventory."
                    )
                extra = queue_archive.unpack(raw)[1] if name.endswith(".gz") else {name: raw}
                for key, value in extra.items():
                    if key in data and data[key] != value:
                        raise core.MemoryError(
                            "ARCHIVE_HISTORY_CONFLICT",
                            "A historical path already contains different bytes.",
                        )
                    data[key] = value
            changes[rel] = queue_archive.pack(data, {**metadata, "archived_at": now})

        for target in plan["targets"]["jobs"]:
            uid = target["upload_id"]
            job = self.resolved_job(parsed[f"chmemx/uploads/{uid}.json"], need_commit=True)
            archive = f"chmemx/archives/uploads/{uid}.json.gz"
            add_archive(archive, target["files"], {"upload_id": uid, "kind": "upload"})
            receipt = {
                k: job[k]
                for k in (
                    "upload_id",
                    "input_digest",
                    "context",
                    "identity_version",
                    "source_agent",
                    "status",
                    "batch_id",
                    "batch_digest",
                    "commit",
                    "record_ids",
                    "created_at",
                    "reason",
                )
                if k in job
            }
            # Preserve context of pre-v2 jobs without rewriting their IDs or sealed batches.
            if not receipt.get("context"):
                c = job.get("candidate", {})
                receipt["legacy_scope"] = c.get("scope")
                receipt["legacy_project_root"] = c.get("source", {}).get("project_root")
            receipt.update(
                type="chmemx-request-receipt-v1",
                archive_path=archive.removeprefix("chmemx/"),
                archive_hash=core.sha256_bytes(changes[archive]),
                archived_at=now,
            )
            changes[f"chmemx/receipts/{uid}.json"] = json_bytes(receipt)
            for name in target["files"]:
                changes[name] = None
            state["uploads"].pop(uid, None)
        for target in plan["targets"]["reviews"]:
            rel = f"chmemx/history/{target['upload_id']}.json.gz"
            add_archive(
                rel,
                target["files"],
                {"kind": "review-history", "upload_id": target["upload_id"]},
                prior=rel,
            )
            for name in target["files"]:
                changes[name] = None
        if plan["targets"]["events"]:
            rel = f"chmemx/archives/events/{plan['archive_token']}.json.gz"
            add_archive(rel, plan["targets"]["events"], {"kind": "events"})
            for name in plan["targets"]["events"]:
                changes[name] = None
        for rel in plan["targets"]["archives"]:
            raw = self.path(rel).read_bytes()
            meta, _ = queue_archive.unpack(raw)
            if meta.get("upload_id"):
                receipt_rel = f"chmemx/receipts/{core.safe_id(meta['upload_id'], 'upload id')}.json"
                receipt = parsed.get(receipt_rel)
                if not receipt or receipt.get("archive_hash") != files[rel]["sha256"]:
                    raise core.MemoryError(
                        "ARCHIVE_RECEIPT_MISMATCH",
                        "An archive cannot be purged without its bound receipt.",
                    )
                self.queue.checked_archive(receipt, raw, meta["upload_id"])
                receipt = {
                    k: v for k, v in receipt.items() if k not in {"archive_path", "archive_hash"}
                }
                receipt["purged_at"] = now
                changes[receipt_rel] = json_bytes(receipt)
            changes[rel] = None
        for rel in plan["targets"]["receipts"]:
            changes[rel] = None
        if plan["action"] == "reconcile":
            for uid in plan["missing_upload_files"]:
                meta = state["uploads"].pop(uid)
                receipt = f"chmemx/receipts/{core.safe_id(uid, 'upload id')}.json"
                if receipt not in files:
                    recovered = {}
                    if meta.get("batch_id"):
                        proof = self.canonical_json(
                            f"approvals/{core.safe_id(meta['batch_id'], 'batch id')}.json"
                        )
                        if proof is not None:
                            if any(r.get("upload_id") == uid for r in proof.get("records", [])):
                                recovered = self.proof_result(
                                    meta["batch_id"], proof["batch_digest"], need_commit=True
                                )
                    changes[receipt] = json_bytes(
                        {
                            **meta,
                            "upload_id": uid,
                            "status": "UPLOAD_DATA_MISSING",
                            "type": "chmemx-request-receipt-v1",
                            "reason": "Payload absent; restore from a verified archive or submit a new request.",
                            "created_at": now,
                        }
                    )
                    if recovered:
                        value = json.loads(changes[receipt])
                        value.update(
                            recovered,
                            reason="Recovered from canonical approval, not inferred from missing bytes.",
                        )
                        changes[receipt] = json_bytes(value)
            for uid in plan["unindexed_upload_files"]:
                job = parsed[f"chmemx/uploads/{uid}.json"]
                state["uploads"][uid] = {
                    k: job.get(k)
                    for k in (
                        "status",
                        "input_digest",
                        "source_agent",
                        "context",
                        "candidate_id",
                        "batch_id",
                    )
                }
        state["batches"] = {
            bid: uid
            for bid, uid in state["batches"].items()
            if uid in state["uploads"]
            and f"batches/{bid}.json" in files
            and changes.get(f"batches/{bid}.json", b"keep") is not None
        }
        for uid, job in state["uploads"].items():
            if job.get("batch_id") and f"batches/{job['batch_id']}.json" in files:
                state["batches"][job["batch_id"]] = uid
        numeric = [
            int(Path(p).stem.removeprefix("event-"))
            for p in groups["events"]
            if Path(p).stem.removeprefix("event-").isdigit()
        ]
        state["event_sequence"] = max(
            [state.get("event_sequence", 0), state["event_files"], *numeric]
        )
        state["event_files"] = len(
            [p for p in groups["events"] if changes.get(p, b"keep") is not None]
        )
        state["event_write_incomplete"] = False
        changes["chmemx/state.json"] = json_bytes(state)
        return changes

    def apply(self, plan, expected_digest):
        with (
            core.StoreLock(self.queue.root / "locks/operation"),
            core.StoreLock(self.service.store),
            self.canonical_reader(),
        ):
            self.queue.assert_ready()
            if (
                plan.get("digest") != expected_digest
                or core.sha256_bytes(
                    core.canonical_json({k: v for k, v in plan.items() if k != "digest"})
                )
                != expected_digest
            ):
                raise core.MemoryError("MAINTENANCE_PLAN_CHANGED", "Exact plan digest required.")
            prior = self.control / "receipts" / f"plan-{expected_digest.split(':')[-1]}.json"
            if prior.exists():
                return core.load_json(prior)
            if time.time() > plan.get("expires_at", 0):
                raise core.MemoryError(
                    "MAINTENANCE_PLAN_EXPIRED", "Prepare a fresh maintenance plan."
                )
            if (
                plan.get("action") not in {"archive", "purge", "reconcile", "expire-receipts"}
                or type(plan.get("older_than_days")) != int
                or not 0 <= plan["older_than_days"] <= 36500
                or type(plan.get("limit_per_family")) != int
                or not 1 <= plan["limit_per_family"] <= 500
            ):
                raise core.MemoryError("MAINTENANCE_ARGUMENT_INVALID", "Invalid plan parameters.")
            if plan["action"] == "expire-receipts" and (
                plan["older_than_days"] < 180 or plan["cutoff"] > time.time() - 180 * 86400
            ):
                raise core.MemoryError(
                    "RECEIPT_RETENTION_MINIMUM",
                    "Receipt expiry cannot shorten the retention minimum.",
                )
            fresh, changes = self._plan(
                plan["action"], plan["older_than_days"], plan["limit_per_family"], plan["cutoff"]
            )
            if fresh != plan or plan["blockers"]:
                raise core.MemoryError(
                    "MAINTENANCE_PLAN_CHANGED", "Inventory, HEAD or reconciliation state changed."
                )
            before = {
                rel: self.path(rel).read_bytes() if self.path(rel).exists() else None
                for rel in changes
            }
            described = {
                rel: {
                    "before_hash": core.sha256_bytes(before[rel])
                    if before[rel] is not None
                    else None,
                    "before_bytes": len(before[rel]) if before[rel] is not None else 0,
                    "after_hash": core.sha256_bytes(data) if data is not None else None,
                    "after_bytes": len(data) if data is not None else 0,
                }
                for rel, data in changes.items()
            }
            if described != plan["target_files"]:
                raise core.MemoryError(
                    "MAINTENANCE_PLAN_CHANGED",
                    "Staged changes differ from the reviewed byte manifest.",
                )
            changes = {rel: data for rel, data in changes.items() if before[rel] != data}
            before = {rel: before[rel] for rel in changes}
            if not changes:
                return {
                    "status": "MAINTENANCE_NOOP",
                    "plan_digest": expected_digest,
                    "canonical_modified": False,
                }
            needed = sum(len(x) for x in [*before.values(), *changes.values()] if x is not None)
            if shutil.disk_usage(self.base).free < needed + 1024 * 1024:
                raise core.MemoryError(
                    "MAINTENANCE_SPACE_REQUIRED",
                    "Insufficient space for the bounded undo journal.",
                    required_bytes=needed,
                )
            txid = "txn-" + uuid.uuid4().hex
            staging = self.control / txid
            staging.mkdir(parents=True)
            entries = {}
            for rel in sorted(
                changes, key=lambda r: (r == "chmemx/state.json", changes[r] is None, r)
            ):
                entry = {}
                for side, values in (("before", before), ("after", changes)):
                    data = values[rel]
                    name = "part-" + core.sha256_bytes(rel.encode()).split(":")[1] + "-" + side
                    entry[side] = (
                        {"hash": core.sha256_bytes(data), "file": name}
                        if data is not None
                        else None
                    )
                entries[rel] = entry
            journal = {
                "type": "chmemx-maintenance-journal-v1",
                "store": str(self.service.store),
                "transaction_id": txid,
                "plan_digest": expected_digest,
                "store_head": plan["store_head"],
                "action": plan["action"],
                "phase": "PREPARING",
                "entries": entries,
            }
            journal["digest"] = core.sha256_bytes(core.canonical_json(journal))
            try:
                core.atomic_json(self.control / "active.json", journal, 0o600)
                for rel, entry in entries.items():
                    for side, values in (("before", before), ("after", changes)):
                        if entry[side]:
                            core.atomic_bytes(staging / entry[side]["file"], values[rel], 0o600)
                journal["phase"] = "APPLYING"
                journal["digest"] = core.sha256_bytes(
                    core.canonical_json({k: v for k, v in journal.items() if k != "digest"})
                )
                core.atomic_json(self.control / "active.json", journal, 0o600)
                return self._finish(journal, "complete")
            except Exception as error:
                if not (self.control / "active.json").exists():
                    staging.rmdir()  # No payload is staged before the durable intent marker.
                raise core.MemoryError(
                    "MAINTENANCE_INTERRUPTED",
                    "Queue mutations are blocked until explicit recovery.",
                    transaction_id=txid,
                ) from error

    def recover(self, transaction_id, action="rollback"):
        if action not in {"rollback", "complete"}:
            raise core.MemoryError("RECOVERY_ACTION_INVALID", "Choose rollback or complete.")
        with (
            core.StoreLock(self.queue.root / "locks/operation"),
            core.StoreLock(self.service.store),
        ):
            core.safe_id(transaction_id, "transaction id")
            if not (self.control / "active.json").exists():
                receipt = self.control / "receipts" / f"{transaction_id}.json"
                if not receipt.exists() and not receipt.is_symlink():
                    raise core.MemoryError(
                        "TRANSACTION_NOT_FOUND",
                        "No active or completed maintenance transaction matches.",
                        transaction_id=transaction_id,
                    )
                return core.load_json(receipt)
            journal = core.load_json(self.control / "active.json")
            if journal.get("transaction_id") != transaction_id:
                raise core.MemoryError("RECOVERY_ID_MISMATCH", "Exact transaction ID required.")
            return self._finish(journal, action)

    def _finish(self, journal, action):
        if (
            journal.get("store") != str(self.service.store)
            or journal.get("type") != "chmemx-maintenance-journal-v1"
            or core.sha256_bytes(
                core.canonical_json({k: v for k, v in journal.items() if k != "digest"})
            )
            != journal.get("digest")
        ):
            raise core.MemoryError("RECOVERY_JOURNAL_CHANGED", "Journal seal failed.")
        staging = self.control / core.safe_id(journal["transaction_id"], "transaction id")
        if journal.get("phase") == "COMPLETED":
            if action == "rollback" and journal["completed_side"] == "after":
                raise core.MemoryError(
                    "RECOVERY_ALREADY_COMMITTED",
                    "Data changes are committed; use complete to finish journal cleanup.",
                )
            for rel, entry in journal["entries"].items():
                current = core.sha256_file(self.path(rel)) if self.path(rel).exists() else None
                expected = entry[journal["completed_side"]]
                if current != (expected["hash"] if expected else None):
                    raise core.MemoryError(
                        "RECOVERY_CONFLICT", "A completed target changed; no overwrite.", path=rel
                    )
            return self._cleanup_journal(journal)
        if action == "complete" and core.git_head(self.service.store) != journal["store_head"]:
            raise core.MemoryError(
                "RECOVERY_HEAD_CHANGED", "Canonical HEAD changed; only rollback is available."
            )
        if journal.get("phase") == "PREPARING":
            for rel, entry in journal["entries"].items():
                current = core.sha256_file(self.path(rel)) if self.path(rel).exists() else None
                if current != (entry["before"]["hash"] if entry["before"] else None):
                    raise core.MemoryError(
                        "RECOVERY_CONFLICT",
                        "Targets changed during preparation; preserved.",
                        path=rel,
                    )
                for side in ("before", "after"):
                    if entry[side]:
                        name = "part-" + core.sha256_bytes(rel.encode()).split(":")[1] + "-" + side
                        if entry[side]["file"] != name:
                            raise core.MemoryError(
                                "RECOVERY_JOURNAL_CHANGED", "Staging name changed."
                            )
                        p = staging / name
                        if p.exists() and (
                            any(x.is_symlink() for x in (p, *p.parents))
                            or core.sha256_file(p) != entry[side]["hash"]
                        ):
                            raise core.MemoryError(
                                "RECOVERY_JOURNAL_CHANGED", "Prepared bytes changed."
                            )
                        if action == "complete" and not p.exists():
                            raise core.MemoryError(
                                "RECOVERY_PREPARATION_INCOMPLETE",
                                "Preparation was interrupted; roll back and prepare a new plan.",
                            )
            if action == "rollback":
                return self._conclude(journal, action, 0)
            journal["phase"] = "APPLYING"
            journal["digest"] = core.sha256_bytes(
                core.canonical_json({k: v for k, v in journal.items() if k != "digest"})
            )
            core.atomic_json(self.control / "active.json", journal, 0o600)
        wanted = "after" if action == "complete" else "before"
        loaded = {}
        for rel, entry in journal["entries"].items():
            path = self.path(rel)
            current = core.sha256_file(path) if path.exists() else None
            if current not in {entry[s]["hash"] if entry[s] else None for s in ("before", "after")}:
                raise core.MemoryError(
                    "RECOVERY_CONFLICT",
                    "A target changed outside this transaction; preserved without overwrite.",
                    path=rel,
                )
            for side in ("before", "after"):
                if entry[side]:
                    name = "part-" + core.sha256_bytes(rel.encode()).split(":")[1] + "-" + side
                    if entry[side]["file"] != name:
                        raise core.MemoryError(
                            "RECOVERY_JOURNAL_CHANGED",
                            "Staging reference does not match its target.",
                        )
                    p = staging / name
                    if (
                        any(x.is_symlink() for x in (p, *p.parents))
                        or core.sha256_file(p) != entry[side]["hash"]
                    ):
                        raise core.MemoryError("RECOVERY_JOURNAL_CHANGED", "Staged bytes changed.")
            loaded[rel] = (staging / entry[wanted]["file"]).read_bytes() if entry[wanted] else None
        for rel, data in sorted(
            loaded.items(),
            key=lambda item: (item[0] == "chmemx/state.json", item[1] is None, item[0]),
        ):
            entry = journal["entries"][rel]
            current = core.sha256_file(self.path(rel)) if self.path(rel).exists() else None
            if current not in {entry[s]["hash"] if entry[s] else None for s in ("before", "after")}:
                raise core.MemoryError(
                    "RECOVERY_CONFLICT", "A target changed during recovery; no overwrite.", path=rel
                )
            if data is None:
                self.path(rel).unlink(missing_ok=True)
            else:
                core.atomic_bytes(self.path(rel), data, 0o600)
        return self._conclude(journal, action, len(loaded))

    def _conclude(self, journal, action, changed):
        receipt = {
            "status": "MAINTENANCE_COMPLETE" if action == "complete" else "MAINTENANCE_ROLLED_BACK",
            "transaction_id": journal["transaction_id"],
            "plan_digest": journal["plan_digest"],
            "action": journal["action"],
            "paths_changed": changed,
            "completed_at": core.iso(),
            "canonical_modified": False,
        }
        core.atomic_json(
            self.control / "receipts" / f"{journal['transaction_id']}.json", receipt, 0o600
        )
        core.atomic_json(
            self.control / "receipts" / f"plan-{journal['plan_digest'].split(':')[-1]}.json",
            receipt,
            0o600,
        )
        journal.update(
            phase="COMPLETED",
            completed_side="after" if action == "complete" else "before",
            receipt=receipt,
        )
        journal["digest"] = core.sha256_bytes(
            core.canonical_json({k: v for k, v in journal.items() if k != "digest"})
        )
        core.atomic_json(self.control / "active.json", journal, 0o600)
        return self._cleanup_journal(journal)

    def _cleanup_journal(self, journal):
        staging = self.control / core.safe_id(journal["transaction_id"], "transaction id")
        for rel, entry in journal["entries"].items():
            for side in ("before", "after"):
                if entry[side]:
                    name = "part-" + core.sha256_bytes(rel.encode()).split(":")[1] + "-" + side
                    if name != entry[side]["file"]:
                        raise core.MemoryError(
                            "RECOVERY_JOURNAL_CHANGED", "Staging cleanup reference changed."
                        )
                    p = staging / name
                    if p.exists() and (
                        any(x.is_symlink() for x in (p, *p.parents))
                        or core.sha256_file(p) != entry[side]["hash"]
                    ):
                        raise core.MemoryError(
                            "RECOVERY_JOURNAL_CHANGED", "Staging file changed; not deleted."
                        )
                    p.unlink(missing_ok=True)
        if staging.exists():
            staging.rmdir()
        (self.control / "active.json").unlink()
        return journal["receipt"]
