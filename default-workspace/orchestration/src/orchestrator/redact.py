from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List

from .config import SECRET_ENV_NAMES

_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "<redacted:api-key>"),
    (re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}"), "<redacted:telegram-token>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "<redacted:github-token>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted:aws-key>"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "<redacted:jwt>"),
    (re.compile(r"(?i)\b(authorization|bearer)\s*[:=]?\s*[A-Za-z0-9._~+/=-]{12,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b(api[_-]?key|bot[_-]?token|access[_-]?token|client[_-]?secret|password)\b\s*[:=]\s*[\"']?[^\s\"']{6,}"), r"\1=<redacted>"),
]


class Redactor:
    def __init__(self, env_file: str | None = None):
        self._values: List[tuple[str, str]] = []
        if env_file and Path(env_file).exists():
            for line in Path(env_file).read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                name, _, value = line.partition("=")
                name = name.strip()
                value = value.strip().strip('"').strip("'")
                if name in SECRET_ENV_NAMES and len(value) >= 8:
                    self._values.append((name, value))

    def text(self, value: str | None) -> str:
        if not value:
            return value or ""
        out = value
        for name, secret in self._values:
            if secret in out:
                out = out.replace(secret, "<redacted:" + name + ">")
        for pattern, replacement in _PATTERNS:
            out = pattern.sub(replacement, out)
        return out

    def data(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.data(item) for item in value]
        if isinstance(value, dict):
            return {key: self.data(item) for key, item in value.items()}
        return value

    def contains_secret(self, value: str) -> bool:
        if any(secret in value for _, secret in self._values):
            return True
        return any(pattern.search(value) for pattern, _ in _PATTERNS)
