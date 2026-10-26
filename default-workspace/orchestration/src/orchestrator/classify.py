from __future__ import annotations

import re
from typing import Optional

CATEGORIES = [
    "auth",
    "quota",
    "network",
    "timeout",
    "permission",
    "tool_missing",
    "scope_violation",
    "acceptance",
    "code",
    "unknown",
]

INFRASTRUCTURE = {"auth", "quota", "network", "permission", "tool_missing"}

_PATTERNS = [
    ("auth", re.compile(r"(?i)(invalid api key|unauthorized|authentication failed|401|403|no auth|not logged in|credentials?)")),
    ("quota", re.compile(r"(?i)(insufficient (balance|quota)|quota exceeded|rate limit|429|payment required|billing)")),
    ("network", re.compile(r"(?i)(econnrefused|econnreset|etimedout|enotfound|getaddrinfo|network is unreachable|connection (refused|reset)|dns|socket hang up|proxy)")),
    ("permission", re.compile(r"(?i)(eperm|eacces|access is denied|permission denied|operation not permitted)")),
    ("tool_missing", re.compile(r"(?i)(enoent|command not found|is not recognized as an internal or external command|no such file or directory|not installed|executable not found)")),
]


def classify(exit_code: Optional[int], stdout: str, stderr: str, timed_out: bool = False) -> str:
    blob = (stderr or "") + "\n" + (stdout or "")
    if timed_out:
        return "timeout"
    for category, pattern in _PATTERNS:
        if pattern.search(blob):
            return category
    if exit_code not in (0, None):
        return "code"
    return "unknown"


def is_retryable(category: str) -> bool:
    return category in {"code", "acceptance", "unknown"}


def is_infrastructure(category: str) -> bool:
    return category in INFRASTRUCTURE
