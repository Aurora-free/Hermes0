"""Rate gate: model-call pacing and rate-limit backoff.

Two jobs:
  1. Pacing: keep a minimum interval between model calls (persisted across
     separate runner invocations, i.e. across consecutive dispatches).
  2. Backoff: when a model call fails with a rate-limit / quota classification,
     wait with exponential backoff and let the caller retry the same call.

Config (policy.json "rate_limit"):
  min_interval_seconds   (default 8)   pacing floor between model calls
  retry_on_rate_limit   (default true) enable in-attempt retries
  max_rate_retries      (default 3)    retries per attempt, on quota errors
  backoff_base_seconds  (default 90)   first backoff sleep
  backoff_max_seconds   (default 300)  per-retry sleep cap
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


class RateGate:
    def __init__(self, root: Path, policy: Dict[str, Any]):
        rl = policy.get("rate_limit", {})
        self.state_file = Path(root) / "state" / "rate_gate.json"
        self.min_interval = float(rl.get("min_interval_seconds", 8))
        self.retry_enabled = bool(rl.get("retry_on_rate_limit", True))
        self.max_retries = int(rl.get("max_rate_retries", 3))
        self.backoff_base = float(rl.get("backoff_base_seconds", 90))
        self.backoff_max = float(rl.get("backoff_max_seconds", 300))

    # -- persistence ------------------------------------------------------
    def _read(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def last_call_epoch(self) -> float:
        return float(self._read().get("last_call_epoch", 0.0))

    def mark_call(self) -> None:
        data = self._read()
        data["last_call_epoch"] = time.time()
        data["last_call_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._write(data)

    # -- pacing -----------------------------------------------------------
    def wait(self, note: Optional[str] = None) -> float:
        """Sleep so that at least `min_interval` seconds separate model calls.

        Returns the number of seconds actually slept (0.0 if none needed).
        """
        last = self.last_call_epoch()
        delta = self.min_interval - (time.time() - last) if last else 0.0
        if delta <= 0:
            return 0.0
        time.sleep(delta)
        return delta

    # -- backoff ----------------------------------------------------------
    def backoff_sleep(self, retry_index: int) -> float:
        """Exponential backoff: base * 2**i, capped. Caller sleeps this long."""
        return min(self.backoff_base * (2 ** retry_index), self.backoff_max)

    def should_retry(self, error_category: Optional[str], retry_index: int) -> bool:
        """True when the outcome looks rate-limited and retries remain."""
        if not self.retry_enabled:
            return False
        if retry_index >= self.max_retries:
            return False
        return error_category in {"quota"}
