"""Conservative, explicit personal policy. This is not a same-user sandbox."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

BLOCKED = re.compile(
    r"ignore\s+(all\s+)?(previous|prior|system)\s+instructions|"
    r"(ignore|override|reveal)\s+(the\s+)?system\s+prompt|"
    r"忽略.{0,8}(之前|先前|系统).{0,6}(指令|规则)|"
    r"<\s*/?\s*(system|developer|tool_call)\b|"
    r'(["\x27]role["\x27]\s*:\s*["\x27](system|developer)["\x27])',
    re.IGNORECASE,
)
HIGH_RISK = re.compile(
    r"\b(exec|execute|sudo|shell|delete|grant|permission|credential|token|password|approval|autorun)\b|"
    r"(执行命令|删除|授权|权限|凭据|密码|密钥|自动批准|绕过|无需确认|写入规则)|"
    r"https?://|\b(?:curl|wget|bash|powershell)\s",
    re.IGNORECASE,
)
HIGH_PREFIX = (
    "workflow.",
    "memory.",
    "policy.",
    "security.",
    "identity.",
    "authorization.",
    "stock.",
    "medical.",
    "finance.",
)


def risk(candidate: dict) -> dict:
    text = unicodedata.normalize("NFKC", str(candidate.get("body", "")))
    if BLOCKED.search(text):
        return {"level": "quarantine", "reasons": ["instruction-override-structure"]}
    reasons = []
    if candidate.get("operation", "promote") != "promote":
        reasons.append("replacement")
    if str(candidate.get("key", "")).lower().startswith(HIGH_PREFIX):
        reasons.append("sensitive-namespace")
    if HIGH_RISK.search(text):
        reasons.append("sensitive-action-or-link")
    if candidate.get("class") not in ("preference", "decision", "evidence"):
        reasons.append("nonstable-class")
    if len(text) > 8192:
        reasons.append("long-form-content")
    return {"level": "high" if reasons else "low", "reasons": reasons}


def policy_digest(policy: dict) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def check_automatic(policy: dict, candidates: list[dict], agent: str) -> None:
    if policy.get("write_mode", "team") != "personal":
        raise ValueError("PERSONAL_MODE_REQUIRED")
    if agent not in policy.get("trusted_source_agents", []):
        raise ValueError("SOURCE_REVIEW_REQUIRED")
    for item in candidates:
        if item.get("class") != "preference":
            raise ValueError("FACTS_REQUIRE_REVIEW")
        if risk(item)["level"] != "low" or item.get("expected_current_id"):
            raise ValueError("OWNER_REVIEW_REQUIRED")
        # Deliberately cover only explicit low-risk key namespaces.
        if not str(item["key"]).startswith(
            tuple(policy.get("auto_key_prefixes", ["preference."]))
        ):
            raise ValueError("OWNER_REVIEW_REQUIRED")
