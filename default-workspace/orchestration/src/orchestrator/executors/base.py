from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from ..models import Task


class ExecutorUnavailable(RuntimeError):
    pass


@dataclass
class ExecRequest:
    task: Task
    prompt: str
    cwd: Path
    timeout_seconds: int
    phase: str
    attempt_no: int
    log_dir: Path


@dataclass
class ExecOutcome:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float
    provider: str
    model: Optional[str] = None
    error_category: Optional[str] = None
    transport: str = "local"
    model_call_started: bool = False
    notes: Dict[str, Any] = field(default_factory=dict)


class Executor:
    name = "base"
    counts_as_model_call = True

    def run(self, request: ExecRequest) -> ExecOutcome:
        raise NotImplementedError

    def preflight(self) -> Dict[str, Any]:
        return {"ok": True, "detail": "no preflight implemented"}
