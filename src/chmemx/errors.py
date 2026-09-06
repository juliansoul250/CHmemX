"""One error envelope for command-line and MCP callers."""

import re


def error_result(error):
    code = getattr(error, "code", None)
    message = str(error)
    if code is None:
        code = message if re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", message) else type(error).__name__
    details = dict(getattr(error, "details", {}))
    if hasattr(error, "nonce_consumed"):
        details["nonce_consumed"] = error.nonce_consumed
    return {
        "status": "ERROR",
        "code": code,
        "message": message,
        "details": details,
        "retryable": bool(details.get("retryable", False)),
    }
