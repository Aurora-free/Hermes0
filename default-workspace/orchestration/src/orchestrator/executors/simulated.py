from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import ExecOutcome, ExecRequest, Executor


def _pick(steps: Any, index: int) -> Dict[str, Any]:
    if not steps:
        return {}
    if isinstance(steps, list):
        if index < len(steps):
            return steps[index] or {}
        return steps[-1] or {}
    return steps or {}


class SimulatedExecutor(Executor):
    """Deterministic executor used only for orchestration tests.

    It never calls a model. Task JSON may carry a "simulation" object with
    opencode_attempts / expert_analysis / expert_takeover steps.
    """

    name = "simulated"
    counts_as_model_call = False

    def run(self, request: ExecRequest) -> ExecOutcome:
        sim = request.task.simulation or {}
        if request.phase == "expert_analysis":
            step = _pick(sim.get("expert_analysis"), 0)
            kind = "analysis"
        elif request.phase == "expert_takeover":
            step = _pick(sim.get("expert_takeover"), 0)
            kind = "takeover"
        else:
            step = _pick(sim.get("opencode_attempts"), max(0, request.attempt_no - 1))
            kind = "opencode"
        if not step:
            step = {"exit_code": 0, "stdout": "simulated no-op"}

        for rel, content in (step.get("write") or {}).items():
            target = (request.cwd / rel).resolve()
            try:
                target.relative_to(request.cwd.resolve())
            except ValueError:
                raise ValueError("simulation write escapes cwd: " + rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")

        for rel in (step.get("delete") or []):
            target = (request.cwd / rel).resolve()
            try:
                target.relative_to(request.cwd.resolve())
            except ValueError:
                raise ValueError("simulation delete escapes cwd: " + rel)
            if target.exists():
                target.unlink()

        delay = float(step.get("sleep_seconds") or 0)
        if delay:
            time.sleep(delay)

        stdout = str(step.get("stdout") or "")
        stderr = str(step.get("stderr") or "")
        exit_code = step.get("exit_code", 0)
        timed_out = bool(step.get("timed_out", False))
        if timed_out:
            exit_code = None
        return ExecOutcome(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_seconds=round(delay, 3),
            provider="simulated:" + kind,
            model=None,
            error_category=None,
            transport="simulation",
            notes={"simulated": True},
        )

    def preflight(self) -> Dict[str, Any]:
        return {"ok": True, "detail": "simulated executor is always available; never calls a model"}
