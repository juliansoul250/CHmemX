"""Derived source validity, independent from accepted/active ownership.

No canonical edits; no expiry inferred from free-form stale_when text.
"""

from __future__ import annotations
import datetime as dt
import hashlib
from pathlib import Path
import subprocess

PRIVATE = {".agents", ".codex-root", ".codex", ".claude", ".zcode", ".config", ".ssh"}


def check(record):
    checked = dt.datetime.now(dt.timezone.utc).isoformat()

    def result(status, reason, **data):
        return {
            "status": status,
            "reason": reason,
            "checked_at": checked,
            "current_use_allowed": status in ("CURRENT", "USER_CONTROLLED"),
            **data,
        }

    source = record.get("source") or {}
    if record.get("scope") == "global" and source.get("kind") == "user-instruction":
        return result("USER_CONTROLLED", "Changes only through a new Owner-confirmed preference.")
    if source.get("kind") != "git":
        return result("UNVERIFIED", "No machine-checkable project dependency.")
    root = Path(source.get("project_root", "")).expanduser()
    relative = Path(source.get("path", ""))
    if not root.is_absolute() or relative.is_absolute() or ".." in relative.parts:
        return result("UNVERIFIED", "Invalid dependency path.")
    if any(x in PRIVATE for x in (*root.parts, *relative.parts)):
        return result("UNVERIFIED", "Private source is not inspected by recall.")
    try:
        target = root / relative
        if any(x.is_symlink() for x in (target, *target.parents)):
            return result("UNVERIFIED", "Symlink dependency is not followed.")

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, check=True, timeout=10
            ).stdout

        head = git("rev-parse", "HEAD").decode().strip()
        current = git("show", f"HEAD:{relative.as_posix()}")
        expected = source.get("hash", "")
        committed = "sha256:" + hashlib.sha256(current).hexdigest()
        working = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if committed != expected or working != expected:
            state = "HISTORICAL" if record.get("class") == "lesson" else "REVIEW_REQUIRED"
            return result(
                state,
                "Referenced file changed; not evidence that every statement became false.",
                checked_head=head,
                dependency_hash=committed,
                original_commit=source.get("commit"),
            )
        if record.get("class") in ("state", "evidence") and head != source.get("commit"):
            return result(
                "REVIEW_REQUIRED",
                "Version-bound progress/test evidence belongs to another project revision.",
                checked_head=head,
            )
        if git("rev-parse", "HEAD").decode().strip() != head:
            return result("UNVERIFIED", "Source changed during validation.")
        return result(
            "CURRENT",
            "Referenced committed and working bytes still match.",
            checked_head=head,
            dependency_hash=committed,
        )
    except (OSError, subprocess.SubprocessError):
        return result(
            "HISTORICAL" if record.get("class") == "lesson" else "UNVERIFIED",
            "Source unavailable, removed or unreadable; no current-fact assertion.",
        )
