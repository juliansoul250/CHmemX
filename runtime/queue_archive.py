"""Portable bounded queue archives; no executable formats or pickle."""

import base64
import gzip
import hashlib
import io
import json
from pathlib import PurePosixPath

MAX_BYTES = 64 * 1024 * 1024


def sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def pack(files, metadata):
    envelope = {
        "type": "chmemx-queue-archive-v1",
        "metadata": metadata,
        "files": {
            name: {
                "sha256": sha(data),
                "bytes": len(data),
                "base64": base64.b64encode(data).decode("ascii"),
            }
            for name, data in sorted(files.items())
        },
    }
    raw = canonical(envelope)
    if len(raw) > MAX_BYTES:
        raise ValueError("QUEUE_ARCHIVE_TOO_LARGE")
    return gzip.compress(raw, mtime=0)


def unpack(data):
    if len(data) > MAX_BYTES:
        raise ValueError("QUEUE_ARCHIVE_TOO_LARGE")
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("QUEUE_ARCHIVE_TOO_LARGE")
    envelope = json.loads(raw)
    if envelope.get("type") != "chmemx-queue-archive-v1" or not isinstance(
        envelope.get("files"), dict
    ):
        raise ValueError("QUEUE_ARCHIVE_INVALID")
    files = {}
    for name, item in envelope["files"].items():
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("QUEUE_ARCHIVE_PATH_INVALID")
        decoded = base64.b64decode(item["base64"], validate=True)
        if len(decoded) != item["bytes"] or sha(decoded) != item["sha256"]:
            raise ValueError("QUEUE_ARCHIVE_CHANGED")
        files[name] = decoded
    return envelope["metadata"], files
