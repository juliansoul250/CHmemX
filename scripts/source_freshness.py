"""Derived source validity, independent from accepted/active ownership.

No canonical edits; no expiry inferred from free-form stale_when text.
"""

from __future__ import annotations
import datetime as dt
import hashlib
from pathlib import Path
import subprocess

PRIVATE = {".agents", ".codex-root", ".codex", ".claude", ".zcode", ".config", ".ssh"}


def _text_eol_matches(git, relative, head, committed, working):
    """Compare only Git-declared text conversion, without invoking content filters."""
    if b"\0" in committed or b"\0" in working or working.replace(b"\r\n", b"\n") != committed:
        return False
    names = ("text", "eol", "crlf", "filter", "ident", "working-tree-encoding")

    def attributes(*options):
        parts = git("check-attr", "-z", *options, *names, "--", relative).split(b"\0")
        if parts[-1:] != [b""] or len(parts) != len(names) * 3 + 1:
            return None
        result = {}
        for i in range(0, len(parts) - 1, 3):
            path, attribute, value = (p.decode("utf-8") for p in parts[i : i + 3])
            if path != relative or attribute not in names or attribute in result:
                return None
            result[attribute] = value
        return result

    declared = attributes(f"--source={head}")
    if declared is None or declared != attributes():
        return False
    if any(
        declared[k] not in {"unset", "unspecified"}
        for k in ("filter", "ident", "working-tree-encoding")
    ):
        return False
    text, eol = declared["text"], declared["eol"]
    if text == "unspecified":
        if declared["crlf"] == "unset":
            text = "unset"
        elif declared["crlf"] == "set":
            text = "set"
        elif declared["crlf"] == "input":
            eol = "lf"
    if text == "unset":
        return False
    enabled = text in {"set", "auto"} or eol in {"lf", "crlf"}
    if not enabled:
        try:
            autocrlf = git("config", "--get", "core.autocrlf").decode("ascii").strip().lower()
        except subprocess.CalledProcessError as error:
            if error.returncode != 1:
                raise
            autocrlf = "false"
        enabled = autocrlf in {"true", "yes", "on", "1", "input"}
    if not enabled:
        return False
    # Git's own text identification avoids treating arbitrary binary bytes as text.
    rows = git("ls-files", "--eol", "-z", "--", relative).split(b"\0")
    if len(rows) != 2 or rows[-1] != b"":
        return False
    info, separator, path = rows[0].partition(b"\t")
    fields = info.split()
    return bool(
        separator
        and path.decode("utf-8") == relative
        and len(fields) >= 3
        and fields[0] in {b"i/lf", b"i/none"}
        and fields[1] in {b"w/crlf", b"w/mixed"}
    )


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
        working_bytes = target.read_bytes()
        working = "sha256:" + hashlib.sha256(working_bytes).hexdigest()
        eol_match = False
        if committed == expected and working != expected:
            eol_match = _text_eol_matches(git, relative.as_posix(), head, current, working_bytes)
            if eol_match and target.read_bytes() != working_bytes:
                return result("UNVERIFIED", "Working source changed during validation.")
        if committed != expected or (working != expected and not eol_match):
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
            "Committed bytes and Git-normalized working text match."
            if eol_match
            else "Referenced committed and working bytes still match.",
            checked_head=head,
            dependency_hash=committed,
            comparison="git-text-eol" if eol_match else "exact-bytes",
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return result(
            "HISTORICAL" if record.get("class") == "lesson" else "UNVERIFIED",
            "Source unavailable, removed or unreadable; no current-fact assertion.",
        )
