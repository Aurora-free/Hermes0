from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

STATES = [
    "queued",
    "running",
    "needs_expert",
    "executing",
    "verifying",
    "succeeded",
    "failed",
    "needs_approval",
    "blocked",
    "cancelled",
]

TERMINAL_STATES = {"succeeded", "failed", "blocked", "cancelled"}

ALLOWED_TRANSITIONS: Dict[str, set[str]] = {
    "queued": {"running", "cancelled", "needs_approval", "blocked"},
    "running": {"running", "verifying", "executing", "needs_expert", "succeeded", "failed", "blocked", "needs_approval", "cancelled"},
    "verifying": {"verifying", "succeeded", "failed", "needs_expert", "blocked"},
    "needs_expert": {"needs_expert", "executing", "running", "failed", "blocked", "needs_approval", "cancelled"},
    "executing": {"executing", "verifying", "succeeded", "failed", "blocked", "needs_approval", "cancelled"},
    "needs_approval": {"queued", "cancelled", "blocked"},
    "succeeded": set(),
    "failed": set(),
    "blocked": {"queued", "cancelled"},
    "cancelled": set(),
}

RISK_LEVELS = {"low", "medium", "high"}

TASK_REQUIRED_FIELDS = [
    "summary",
    "working_directory",
    "acceptance_commands",
    "timeout_seconds",
    "risk_level",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@dataclass
class Task:
    task_id: str
    summary: str
    status: str
    created_at: str
    updated_at: str
    working_directory: str
    allowed_paths: List[str]
    acceptance_commands: List[Any]
    timeout_seconds: int
    attempt_count: int
    risk_level: str
    executor: str
    result_path: Optional[str]
    last_error_category: Optional[str]
    read_only: bool = False
    requires_approval: bool = False
    approved: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    runner_pid: Optional[int] = None
    current_phase: Optional[str] = None
    needs_approval_reason: Optional[str] = None
    next_action: Optional[str] = None
    simulation: Dict[str, Any] = field(default_factory=dict)
    model_calls: int = 0
    estimated_cost_usd: float = 0.0
    hermes_task_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        return cls(
            task_id=row["task_id"],
            summary=row["summary"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            working_directory=row["working_directory"],
            allowed_paths=loads(row["allowed_paths"], []),
            acceptance_commands=loads(row["acceptance_commands"], []),
            timeout_seconds=row["timeout_seconds"],
            attempt_count=row["attempt_count"],
            risk_level=row["risk_level"],
            executor=row["executor"],
            result_path=row["result_path"],
            last_error_category=row["last_error_category"],
            read_only=bool(row["read_only"]),
            requires_approval=bool(row["requires_approval"]),
            approved=bool(row["approved"]),
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            runner_pid=row["runner_pid"],
            current_phase=row["current_phase"],
            needs_approval_reason=row["needs_approval_reason"],
            next_action=row["next_action"],
            simulation=loads(row["simulation"], {}),
            model_calls=row["model_calls"],
            estimated_cost_usd=row["estimated_cost_usd"],
            hermes_task_id=row["hermes_task_id"],
            idempotency_key=row["idempotency_key"],
        )

    def public(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "summary": self.summary,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "working_directory": self.working_directory,
            "allowed_paths": self.allowed_paths,
            "acceptance_commands": self.acceptance_commands,
            "timeout_seconds": self.timeout_seconds,
            "attempt_count": self.attempt_count,
            "risk_level": self.risk_level,
            "executor": self.executor,
            "result_path": self.result_path,
            "last_error_category": self.last_error_category,
            "read_only": self.read_only,
            "requires_approval": self.requires_approval,
            "approved": self.approved,
            "runner_pid": self.runner_pid,
            "current_phase": self.current_phase,
            "needs_approval_reason": self.needs_approval_reason,
            "next_action": self.next_action,
            "model_calls": self.model_calls,
            "estimated_cost_usd": self.estimated_cost_usd,
            "hermes_task_id": self.hermes_task_id,
            "idempotency_key": self.idempotency_key,
        }


def validate_task_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for name in TASK_REQUIRED_FIELDS:
        if name not in payload:
            errors.append("missing required field: " + name)
    if payload.get("risk_level") not in RISK_LEVELS:
        errors.append("risk_level must be one of: " + ", ".join(sorted(RISK_LEVELS)))
    commands = payload.get("acceptance_commands")
    if not isinstance(commands, list) or not commands:
        errors.append("acceptance_commands must be a non-empty list")
    elif any(not isinstance(item, (list, dict)) for item in commands):
        errors.append("each acceptance command must be an argv list or an object with argv")
    timeout = payload.get("timeout_seconds")
    if not isinstance(timeout, int) or timeout <= 0:
        errors.append("timeout_seconds must be a positive integer")
    if not isinstance(payload.get("working_directory"), str):
        errors.append("working_directory must be a string")
    paths = payload.get("allowed_paths", [])
    if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
        errors.append("allowed_paths must be a list of strings")
    if payload.get("risk_level") == "high" and not payload.get("approved", False):
        pass  # routed to needs_approval at run time, not a validation error
    return errors
