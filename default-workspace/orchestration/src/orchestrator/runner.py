from __future__ import annotations

import json
import os
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import classify as classify_mod
from .config import (
    ORCH_ROOT,
    db_path,
    ensure_layout,
    executor_env,
    public_env,
    is_within,
    log_path,
    resolve_under,
    result_path,
    task_dir,
)
from .db import Store
from .executors.base import ExecOutcome, ExecRequest, Executor, ExecutorUnavailable
from .executors.expert import DshHeadlessExpert
from .executors.opencode import OpenCodeExecutor
from .executors.simulated import SimulatedExecutor
from .lock import LockBusy, WorktreeLock, pid_alive
from .models import Task, now_iso, validate_task_payload
from .rate import RateGate
from .redact import Redactor
import time
from .snapshot import Manifest, diff_manifests, make_patch, restore, save, scan, scope_violations
from .telegram import TelegramReporter


class BudgetExceeded(RuntimeError):
    pass


class TaskError(RuntimeError):
    pass


@dataclass
class AttemptResult:
    attempt_no: int
    phase: str
    executor: str
    outcome: ExecOutcome
    acceptance: List[Dict[str, Any]]
    diff: Dict[str, List[str]]
    violations: List[str]
    patch_path: Optional[str]
    error_category: Optional[str]
    success: bool
    before_manifest: Optional[Manifest] = None
    after_manifest: Optional[Manifest] = None
    before_dir: Optional[Path] = None

    def public(self) -> Dict[str, Any]:
        return {
            "attempt_no": self.attempt_no,
            "phase": self.phase,
            "executor": self.executor,
            "exit_code": self.outcome.exit_code,
            "timed_out": self.outcome.timed_out,
            "duration_seconds": self.outcome.duration_seconds,
            "provider": self.outcome.provider,
            "model": self.outcome.model,
            "acceptance": self.acceptance,
            "diff": self.diff,
            "scope_violations": self.violations,
            "patch_path": self.patch_path,
            "error_category": self.error_category,
            "success": self.success,
            "stdout_tail": self.outcome.stdout[-2000:],
            "stderr_tail": self.outcome.stderr[-2000:],
        }


class Orchestrator:
    def __init__(self, policy: Dict[str, Any], root: Path = ORCH_ROOT, store: Optional[Store] = None):
        self.policy = policy
        self.root = Path(root)
        ensure_layout(self.root)
        self.store = store or Store(db_path(self.root))
        self.redactor = Redactor(policy.get("secrets", {}).get("env_file"))
        self.public_env = public_env()
        self.expert_env = executor_env(
            policy, {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"}
        )
        self.telegram = TelegramReporter(policy, self.redactor, self.root)

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------ setup
    def create_task(self, payload: Dict[str, Any]) -> Task:
        errors = validate_task_payload(payload)
        if errors:
            raise TaskError("; ".join(errors))
        raw = json.dumps(payload, ensure_ascii=False)
        if self.redactor.contains_secret(raw):
            raise TaskError("task payload appears to contain a credential; refusing to store it")

        hermes_task_id = payload.get("hermes_task_id")
        idempotency_key = payload.get("idempotency_key")
        if hermes_task_id:
            existing = self.store.get_task_by_hermes_id(str(hermes_task_id))
            if existing:
                self.store.add_event(existing.task_id, "idempotent_reuse", "hermes_task_id=" + str(hermes_task_id))
                return existing
        if idempotency_key:
            existing = self.store.get_task_by_idempotency(str(idempotency_key))
            if existing:
                self.store.add_event(existing.task_id, "idempotent_reuse", "idempotency_key=" + str(idempotency_key))
                return existing

        working_directory = Path(str(payload["working_directory"])).resolve()
        if not working_directory.is_dir():
            raise TaskError("working_directory does not exist: " + str(working_directory))
        read_only = bool(payload.get("read_only", False))
        allowed_raw = payload.get("allowed_paths", []) or []
        allowed: List[str] = []
        for item in allowed_raw:
            path = resolve_under(working_directory, item)
            if not is_within(path, working_directory):
                raise TaskError("allowed path escapes working_directory: " + item)
            allowed.append(str(path))
        if not read_only and not allowed:
            raise TaskError("a writing task needs at least one allowed path")

        task_id = payload.get("task_id") or ("T-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
        directory = task_dir(task_id, self.root)
        if directory.exists():
            raise TaskError("task directory already exists: " + str(directory))
        now = now_iso()
        task = Task(
            task_id=task_id,
            summary=str(payload["summary"]),
            status="queued",
            created_at=now,
            updated_at=now,
            working_directory=str(working_directory),
            allowed_paths=allowed,
            acceptance_commands=list(payload["acceptance_commands"]),
            timeout_seconds=int(payload["timeout_seconds"]),
            attempt_count=0,
            risk_level=str(payload["risk_level"]),
            executor=str(payload.get("executor", "opencode")),
            result_path=str(result_path(task_id, self.root)),
            last_error_category=None,
            read_only=read_only,
            requires_approval=bool(payload.get("requires_approval", False)) or str(payload["risk_level"]) == "high",
            simulation=payload.get("simulation", {}) or {},
            hermes_task_id=str(hermes_task_id) if hermes_task_id else None,
            idempotency_key=str(idempotency_key) if idempotency_key else None,
        )
        self.store.insert_task(task)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "task.json").write_text(
            json.dumps(self.redactor.data(payload), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.store.add_event(task_id, "created", "queued by CLI")
        self.telegram.notify("task_created", task, {"phase": "queued", "next": "run"})
        return task

    # --------------------------------------------------------------- execution
    def run(self, task_id: str, executor_name: Optional[str] = None) -> Dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskError("unknown task: " + task_id)
        if task.status in {"succeeded", "failed", "cancelled"}:
            return self._report(task, "no-op: task is already terminal")
        if task.status == "needs_approval":
            return self._report(task, "blocked: task needs explicit approval")
        if task.status == "blocked":
            return self._report(task, "blocked: inspect, then run resume before retrying")

        if task.requires_approval and not task.approved:
            self._set_status(task.task_id, "needs_approval", needs_approval_reason="high risk or requires_approval flag")
            self.store.add_event(task_id, "approval_required", "risk=" + task.risk_level)
            return self._report(self.store.get_task(task_id), "needs_approval")

        lock: Optional[WorktreeLock] = None
        if not task.read_only:
            lock = WorktreeLock(self.root / "locks" / "worktree.lock", task.task_id)
            try:
                lock.acquire()
            except LockBusy as exc:
                self.store.set_fields(task_id, last_error_category="lock_busy", next_action="wait for the writing task to finish, then resume")
                self._set_status(task_id, "blocked", needs_approval_reason="worktree lock held by " + str(exc.owner.get("task_id")))
                self.store.add_event(task_id, "lock_busy", json.dumps(exc.owner, ensure_ascii=False))
                return self._report(self.store.get_task(task_id), "blocked: " + str(exc))

        try:
            return self._run_locked(task, executor_name)
        except BudgetExceeded as exc:
            self.store.add_event(task_id, "budget_exceeded", self.redactor.text(str(exc)))
            self.store.set_fields(task_id, last_error_category="budget", next_action="raise the local call budget or stop")
            current = self.store.get_task(task_id)
            if current and current.status not in {"succeeded", "failed", "cancelled", "blocked"}:
                self._set_status(task_id, "blocked", needs_approval_reason=str(exc)[:300], runner_pid=None)
            current = self.store.get_task(task_id)
            if current:
                self._write_result(current, [], "blocked", "budget exceeded: " + str(exc))
                self.telegram.notify("budget_exceeded", current, {"phase": "blocked", "reason": str(exc)})
            return self._report(current, "budget exceeded: " + str(exc))
        except Exception as exc:
            self.store.add_event(task_id, "runner_exception", self.redactor.text(repr(exc)))
            self.store.set_fields(task_id, last_error_category="unknown", next_action="inspect logs and resume")
            current = self.store.get_task(task_id)
            if current and current.status in {"running", "verifying", "executing", "needs_expert", "queued"}:
                self._set_status(task_id, "blocked", needs_approval_reason="runner exception: " + str(exc)[:300], runner_pid=None)
            return self._report(self.store.get_task(task_id), "runner exception: " + str(exc))
        finally:
            if lock is not None:
                lock.release()

    def _run_locked(self, task: Task, executor_name: Optional[str]) -> Dict[str, Any]:
        task = self._set_status(task.task_id, "running", runner_pid=os.getpid(), current_phase="opencode_attempt_1")
        before_dir, before = self._snapshot(task, 1)
        attempts: List[AttemptResult] = []

        executor = self._executor(executor_name or task.executor)
        prompt = self._opencode_prompt(task, 1, None)
        result = self._attempt(task, executor, executor.name, prompt, 1, before_dir, before)
        attempts.append(result)
        self._record_budget(task, result)

        if result.violations:
            return self._scope_stop(task, attempts, result)

        if result.success:
            return self._finish_success(task, attempts, result)

        if classify_mod.is_infrastructure(result.error_category or "unknown"):
            return self._finish_blocked(task, attempts, "infrastructure failure: " + str(result.error_category),
                                        "restore the dependency, then resume")

        # One explicitly different OpenCode retry.
        max_attempts = int(self.policy["limits"]["max_opencode_attempts"])
        if len(attempts) < max_attempts:
            prompt = self._opencode_prompt(task, 2, result)
            result = self._attempt(task, executor, executor.name, prompt, 2, *self._snapshot(task, 2))
            attempts.append(result)
            self._record_budget(task, result)
            if result.violations:
                return self._scope_stop(task, attempts, result)
            if result.success:
                return self._finish_success(task, attempts, result)
            if classify_mod.is_infrastructure(result.error_category or "unknown"):
                return self._finish_blocked(task, attempts, "infrastructure failure: " + str(result.error_category),
                                            "restore the dependency, then resume")

        if task.read_only:
            return self._finish_blocked(task, attempts, "read-only task failed; expert takeover would write", "review the read-only acceptance command")

        # Expert tier.
        if not self.policy.get("expert", {}).get("enabled", False):
            return self._finish_blocked(task, attempts, "expert tier disabled by policy", "enable expert or fix manually")
        current = self.store.get_task(task.task_id) or task
        if int(current.attempt_count) >= int(self.policy["limits"]["max_total_attempts"]):
            return self._finish_blocked(task, attempts, "max_total_attempts reached", "manual review")

        self._set_status(task.task_id, "needs_expert", current_phase="expert_analysis", runner_pid=os.getpid())
        analysis = self._run_expert_analysis(task, attempts)
        self._record_budget(task, analysis)
        if analysis.violations:
            return self._scope_stop(task, attempts, analysis, phase="expert_analysis")
        if analysis.error_category in classify_mod.INFRASTRUCTURE or analysis.outcome.exit_code != 0:
            return self._finish_blocked(
                task,
                attempts,
                "expert analysis unavailable: " + str(analysis.error_category or analysis.outcome.exit_code),
                "verify DSH headless credentials and resume",
            )
        plan = analysis.outcome.notes.get("parsed") or {"root_cause": "unparsed", "plan": [analysis.outcome.stdout[-2000:]]}
        analysis_path = task_dir(task.task_id, self.root) / "analysis.json"
        analysis_path.write_text(json.dumps(self.redactor.data(plan), ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.add_event(task.task_id, "expert_analysis", "plan written to " + str(analysis_path))
        self.telegram.notify("expert_analysis", task, {"phase": "needs_expert", "next": "opencode implements the plan"})

        # OpenCode implements the expert plan exactly once.
        self._set_status(task.task_id, "running", current_phase="opencode_plan_implementation")
        prompt = self._opencode_prompt(task, 3, result, plan=plan)
        result = self._attempt(task, executor, executor.name, prompt, 3, *self._snapshot(task, 3))
        attempts.append(result)
        self._record_budget(task, result)
        if result.violations:
            return self._scope_stop(task, attempts, result)
        if result.success:
            return self._finish_success(task, attempts, result)
        if classify_mod.is_infrastructure(result.error_category or "unknown"):
            return self._finish_blocked(task, attempts, "infrastructure failure: " + str(result.error_category),
                                        "restore the dependency, then resume")

        # Expert direct takeover, holding the same worktree lock.
        self.store.add_event(task.task_id, "expert_takeover", "opencode stopped; lock retained by " + str(os.getpid()))
        self.telegram.notify("expert_takeover", task, {"phase": "executing", "next": "expert fixes and verifies"})
        self._set_status(task.task_id, "executing", executor="deepseek_expert", current_phase="expert_takeover")
        expert = self._expert_executor()
        takeover_prompt = self._takeover_prompt(task, plan, attempts)
        takeover = self._attempt(task, expert, "deepseek_expert", takeover_prompt, 4, *self._snapshot(task, 4),
                                 takeover_mode=True)
        attempts.append(takeover)
        self._record_budget(task, takeover)
        if takeover.violations:
            return self._scope_stop(task, attempts, takeover, phase="expert_takeover")
        if takeover.success:
            return self._finish_success(task, attempts, takeover)
        return self._finish_blocked(
            task,
            attempts,
            "expert direct takeover failed: " + str(takeover.error_category),
            "automation stopped; inspect the diff and logs",
        )

    # ----------------------------------------------------------------- pieces
    def _executor(self, name: str) -> Executor:
        if name == "simulated":
            return SimulatedExecutor()
        if name == "opencode":
            return OpenCodeExecutor(self.policy["opencode"], self.public_env)
        if name in {"deepseek_expert", "expert"}:
            return DshHeadlessExpert(self.policy["expert"], self.expert_env)
        raise TaskError("unknown executor: " + name)

    def _set_status(self, task_id: str, status: str, **fields: Any) -> Task:
        task = self.store.transition(task_id, status, **fields)
        self.store.add_event(task_id, "status", status)
        return task

    def _snapshot(self, task: Task, attempt_no: int) -> tuple[Path, Manifest]:
        cwd = Path(task.working_directory)
        manifest = scan(
            cwd,
            exclude_dirs=self.policy["paths"]["exclude_dirs"],
            max_files=int(self.policy["limits"]["max_snapshot_files"]),
            max_bytes=int(self.policy["limits"]["max_snapshot_bytes"]),
        )
        dest = task_dir(task.task_id, self.root) / "attempts" / ("%02d" % attempt_no) / "before"
        save(cwd, dest, manifest)
        return dest, manifest

    def _run_acceptance(self, task: Task, attempt_no: int) -> List[Dict[str, Any]]:
        from .proc import run_capture

        results: List[Dict[str, Any]] = []
        env = dict(self.public_env)
        for index, item in enumerate(task.acceptance_commands, start=1):
            if isinstance(item, dict):
                argv = item.get("argv")
                timeout = int(item.get("timeout_seconds", min(120, task.timeout_seconds)))
                shell = bool(item.get("shell", False))
            else:
                argv = item
                timeout = min(120, task.timeout_seconds)
                shell = False
            if not isinstance(argv, list) or not argv:
                results.append({"index": index, "argv": argv, "error": "invalid argv", "exit_code": None, "passed": False})
                continue
            if shell:
                results.append({"index": index, "argv": argv, "error": "shell acceptance is not allowed", "exit_code": None, "passed": False})
                continue
            proc = run_capture(
                argv,
                cwd=Path(task.working_directory),
                timeout_seconds=timeout,
                env=env,
                stdout_path=log_path(task.task_id, "acceptance-%d-%d.stdout.log" % (attempt_no, index), self.root),
                stderr_path=log_path(task.task_id, "acceptance-%d-%d.stderr.log" % (attempt_no, index), self.root),
            )
            results.append(
                {
                    "index": index,
                    "argv": argv,
                    "exit_code": proc.exit_code,
                    "timed_out": proc.timed_out,
                    "duration_seconds": proc.duration_seconds,
                    "passed": proc.exit_code == 0 and not proc.timed_out,
                    "stdout_tail": self.redactor.text(proc.stdout[-1000:]),
                    "stderr_tail": self.redactor.text(proc.stderr[-1000:]),
                }
            )
        return results

    def _attempt(
        self,
        task: Task,
        executor: Executor,
        executor_name: str,
        prompt: str,
        attempt_no: int,
        before_dir: Path,
        before: Manifest,
        takeover_mode: bool = False,
    ) -> AttemptResult:
        self._check_budget(task, executor)
        self.store.set_fields(task.task_id, attempt_count=max(int(task.attempt_count), attempt_no))
        attempt_id = self.store.add_attempt(task.task_id, attempt_no, executor_name, "expert_takeover" if takeover_mode else "implement")
        phase = "expert_takeover" if takeover_mode else "implement"
        log_dir = self.root / "logs" / task.task_id
        log_dir.mkdir(parents=True, exist_ok=True)
        request = ExecRequest(
            task=task,
            prompt=prompt,
            cwd=Path(task.working_directory),
            timeout_seconds=int(task.timeout_seconds) if not takeover_mode else int(self.policy["expert"]["timeout_seconds"]),
            phase="expert_takeover" if takeover_mode else "opencode",
            attempt_no=attempt_no,
            log_dir=log_dir,
        )
        outcome: ExecOutcome
        gate = RateGate(self.root, self.policy)
        deadline = time.monotonic() + request.timeout_seconds
        calls = 0
        while True:
            if getattr(executor, "counts_as_model_call", False):
                waited = gate.wait()
                if waited > 0:
                    self.store.add_event(task.task_id, "rate_wait",
                                         "paced %.1fs before model call" % waited)
            try:
                if takeover_mode and hasattr(executor, "takeover"):
                    outcome = executor.takeover(request)
                else:
                    outcome = executor.run(request)
            except Exception as exc:
                outcome = ExecOutcome(
                    exit_code=None, stdout="", stderr=repr(exc), timed_out=False, duration_seconds=0.0,
                    provider=executor_name, error_category=classify_mod.classify(None, "", repr(exc)),
                )
            gate.mark_call()
            calls += 1
            if outcome.error_category == "quota" and gate.retry_enabled \
                    and calls - 1 < gate.max_retries \
                    and time.monotonic() + gate.backoff_sleep(calls - 1) + 5 <= deadline:
                sleep_s = gate.backoff_sleep(calls - 1)
                self.store.add_event(
                    task.task_id, "rate_retry",
                    "rate-limited; backing off %.0fs (retry %d of %d)"
                    % (sleep_s, calls, gate.max_retries))
                time.sleep(sleep_s)
                continue
            break
        outcome.stdout = self.redactor.text(outcome.stdout)
        outcome.stderr = self.redactor.text(outcome.stderr)
        (log_dir / ("attempt-%d-%s.combined.log" % (attempt_no, executor_name))).write_text(
            "STDOUT:\n" + outcome.stdout + "\n\nSTDERR:\n" + outcome.stderr, encoding="utf-8", errors="replace"
        )

        acceptance = self._run_acceptance(task, attempt_no)
        acceptance_ok = all(item.get("passed") for item in acceptance) and bool(acceptance)

        after = scan(
            Path(task.working_directory),
            exclude_dirs=self.policy["paths"]["exclude_dirs"],
            max_files=int(self.policy["limits"]["max_snapshot_files"]),
            max_bytes=int(self.policy["limits"]["max_snapshot_bytes"]),
        )
        diff = diff_manifests(before, after)
        violations = scope_violations(
            sorted(set(diff["added"]) | set(diff["removed"]) | set(diff["modified"])),
            Path(task.working_directory),
            task.allowed_paths,
            task.read_only,
        )
        patch = make_patch(
            Path(task.working_directory), before, after, before_dir,
            task_dir(task.task_id, self.root) / "attempts" / ("%02d" % attempt_no) / "diff.patch",
        )
        success = (
            outcome.exit_code == 0
            and not outcome.timed_out
            and acceptance_ok
            and not violations
        )
        if success:
            category = None
        elif violations:
            category = "scope_violation"
        elif not acceptance_ok:
            category = "acceptance"
        else:
            category = outcome.error_category or classify_mod.classify(outcome.exit_code, outcome.stdout, outcome.stderr, outcome.timed_out)

        self.store.finish_attempt(
            attempt_id,
            exit_code=outcome.exit_code,
            status="succeeded" if success else "failed",
            error_category=category,
            stdout_path=str(log_dir / ("attempt-%d-%s.combined.log" % (attempt_no, executor_name))),
            stderr_path=str(log_dir / ("attempt-%d-%s.combined.log" % (attempt_no, executor_name))),
            detail=json.dumps({"diff": diff, "violations": violations, "timed_out": outcome.timed_out}, ensure_ascii=False),
        )
        self.store.set_fields(task.task_id, last_error_category=category, executor=executor_name)
        self.store.add_event(
            task.task_id,
            "attempt",
            json.dumps({"n": attempt_no, "executor": executor_name, "exit": outcome.exit_code,
                        "acceptance": acceptance_ok, "violations": violations, "category": category}, ensure_ascii=False),
        )
        return AttemptResult(
            attempt_no=attempt_no,
            phase=phase,
            executor=executor_name,
            outcome=outcome,
            acceptance=acceptance,
            diff=diff,
            violations=violations,
            patch_path=patch["patch_path"],
            error_category=category,
            success=success,
            before_manifest=before,
            after_manifest=after,
            before_dir=before_dir,
        )

    def _check_budget(self, task: Task, executor: Executor) -> None:
        if not getattr(executor, "counts_as_model_call", True):
            return
        limits = self.policy["limits"]
        current = self.store.get_task(task.task_id) or task
        calls = int(current.model_calls) + 1
        cost = float(current.estimated_cost_usd) + float(limits["assumed_cost_per_call_usd"])
        if calls > int(limits["max_model_calls_per_task"]):
            raise BudgetExceeded("max_model_calls_per_task reached (%d)" % int(limits["max_model_calls_per_task"]))
        if cost > float(limits["max_cost_usd_per_task"]):
            raise BudgetExceeded("max_cost_usd_per_task reached (%.2f)" % float(limits["max_cost_usd_per_task"]))

    def _record_budget(self, task: Task, attempt: AttemptResult) -> None:
        if not attempt.outcome.model_call_started:
            return
        if attempt.executor == "simulated" or attempt.outcome.transport == "simulation":
            return
        cost = float(self.policy["limits"]["assumed_cost_per_call_usd"])
        self.store.add_budget(
            task.task_id, attempt.executor, attempt.outcome.model, calls=1, cost_usd=cost,
            note="attempt %d phase %s" % (attempt.attempt_no, attempt.phase),
        )
        current = self.store.get_task(task.task_id)
        if current:
            self.store.set_fields(
                task.task_id,
                model_calls=int(current.model_calls) + 1,
                estimated_cost_usd=round(float(current.estimated_cost_usd) + cost, 4),
            )

    def _expert_executor(self) -> Executor:
        if str(self.policy.get("expert", {}).get("mode", "dsh_headless")) == "simulated":
            return SimulatedExecutor()
        return DshHeadlessExpert(self.policy["expert"], self.expert_env)

    def _run_expert_analysis(self, task: Task, attempts: List[AttemptResult]) -> AttemptResult:
        expert = self._expert_executor()
        history = self._history_text(task, attempts)
        prompt = "Task summary:\n" + task.summary + "\n\nWorking directory: " + task.working_directory + "\n\n" + history
        before_dir, before = self._snapshot(task, 5)
        self._check_budget(task, expert)
        attempt_id = self.store.add_attempt(task.task_id, 5, "deepseek_expert", "expert_analysis")
        request = ExecRequest(
            task=task, prompt=prompt, cwd=Path(task.working_directory),
            timeout_seconds=int(self.policy["expert"]["timeout_seconds"]),
            phase="expert_analysis", attempt_no=5, log_dir=self.root / "logs" / task.task_id,
        )
        outcome: ExecOutcome
        gate = RateGate(self.root, self.policy)
        deadline = time.monotonic() + int(self.policy["expert"]["timeout_seconds"])
        calls = 0
        while True:
            if getattr(expert, "counts_as_model_call", False):
                waited = gate.wait()
                if waited > 0:
                    self.store.add_event(task.task_id, "rate_wait",
                                         "paced %.1fs before expert analysis" % waited)
            try:
                if hasattr(expert, "analyze"):
                    outcome = expert.analyze(request)
                else:
                    outcome = expert.run(request)
            except Exception as exc:
                outcome = ExecOutcome(exit_code=None, stdout="", stderr=repr(exc), timed_out=False, duration_seconds=0,
                                      provider="deepseek_expert", error_category="tool_missing")
            gate.mark_call()
            calls += 1
            if outcome.error_category == "quota" and gate.retry_enabled \
                    and calls - 1 < gate.max_retries \
                    and time.monotonic() + gate.backoff_sleep(calls - 1) + 5 <= deadline:
                sleep_s = gate.backoff_sleep(calls - 1)
                self.store.add_event(
                    task.task_id, "rate_retry",
                    "expert analysis rate-limited; backing off %.0fs (retry %d of %d)"
                    % (sleep_s, calls, gate.max_retries))
                time.sleep(sleep_s)
                continue
            break
        outcome.stdout = self.redactor.text(outcome.stdout)
        outcome.stderr = self.redactor.text(outcome.stderr)
        (self.root / "logs" / task.task_id / "attempt-5-expert-analysis.log").write_text(
            "STDOUT:\n" + outcome.stdout + "\n\nSTDERR:\n" + outcome.stderr, encoding="utf-8", errors="replace"
        )
        self.store.finish_attempt(
            attempt_id, exit_code=outcome.exit_code, status="succeeded" if outcome.exit_code == 0 else "failed",
            error_category=outcome.error_category,
            detail=json.dumps({"phase": "expert_analysis"}, ensure_ascii=False),
        )
        # Analysis must not modify the tree.
        after = scan(Path(task.working_directory), exclude_dirs=self.policy["paths"]["exclude_dirs"])
        changed = diff_manifests(before, after)
        modified = sorted(set(changed["added"]) | set(changed["removed"]) | set(changed["modified"]))
        if modified:
            self.store.add_event(task.task_id, "expert_analysis_modified_tree", json.dumps(modified, ensure_ascii=False))
            outcome.exit_code = 1
            outcome.error_category = "scope_violation"
        return AttemptResult(
            attempt_no=5, phase="expert_analysis", executor="deepseek_expert", outcome=outcome,
            acceptance=[], diff=changed, violations=modified, patch_path=None,
            error_category=outcome.error_category, success=outcome.exit_code == 0,
            before_manifest=before, after_manifest=after, before_dir=before_dir,
        )

    def _history_text(self, task: Task, attempts: List[AttemptResult]) -> str:
        lines = ["Previous attempt evidence:"]
        for item in attempts:
            lines.append("Attempt %d (%s) exit=%s category=%s timeout=%s" % (
                item.attempt_no, item.executor, item.outcome.exit_code, item.error_category, item.outcome.timed_out))
            lines.append("diff: " + json.dumps(item.diff, ensure_ascii=False))
            lines.append("stderr tail:\n" + item.outcome.stderr[-1500:])
            lines.append("acceptance: " + json.dumps(item.acceptance, ensure_ascii=False)[:1500])
        lines.append("Allowed paths: " + json.dumps(task.allowed_paths, ensure_ascii=False))
        lines.append("Acceptance commands: " + json.dumps(task.acceptance_commands, ensure_ascii=False))
        return "\n".join(lines)

    def _opencode_prompt(self, task: Task, attempt_no: int, previous: Optional[AttemptResult], plan: Optional[Dict[str, Any]] = None) -> str:
        lines = [
            "You are the local implementation executor for one bounded task.",
            "Working directory: " + task.working_directory,
            "Allowed paths (do not modify anything else): " + json.dumps(task.allowed_paths, ensure_ascii=False),
            "Read-only task: " + ("yes" if task.read_only else "no"),
            "Task: " + task.summary,
            "",
            "Acceptance commands that must pass (run them yourself before finishing):",
            json.dumps(task.acceptance_commands, ensure_ascii=False, indent=2),
            "",
        ]
        if attempt_no == 1:
            lines.append("Strategy: inspect the repository first, make the smallest correct change, then run the acceptance commands.")
        elif attempt_no == 2:
            lines.append("Your previous attempt failed. Use a materially different approach. Read the failure evidence below before editing.")
            if previous:
                lines.append("Previous exit: " + str(previous.outcome.exit_code) + " category: " + str(previous.error_category))
                lines.append("Previous stderr tail:\n" + previous.outcome.stderr[-2500:])
        else:
            lines.append("A DeepSeek expert analysed the failures. Implement this plan exactly; do not redesign it.")
            lines.append(json.dumps(plan or {}, ensure_ascii=False, indent=2))
        lines.append("Never read, print, or modify credentials. Do not touch paths outside the allowed list.")
        return "\n".join(lines)

    def _takeover_prompt(self, task: Task, plan: Dict[str, Any], attempts: List[AttemptResult]) -> str:
        return "\n".join([
            "Task: " + task.summary,
            "Working directory: " + task.working_directory,
            "Allowed paths: " + json.dumps(task.allowed_paths, ensure_ascii=False),
            "Acceptance commands: " + json.dumps(task.acceptance_commands, ensure_ascii=False),
            "Expert plan: " + json.dumps(plan, ensure_ascii=False, indent=2),
            "Previous failure evidence:",
            self._history_text(task, attempts)[-4000:],
            "OpenCode has stopped. You are the only writer. Fix the code and verify.",
        ])

    # ------------------------------------------------------------------ finish
    def _scope_stop(self, task: Task, attempts: List[AttemptResult], result: AttemptResult,
                    phase: str = "implement") -> Dict[str, Any]:
        self.store.set_fields(task.task_id, last_error_category="scope_violation")
        try:
            actions = self.rollback(task.task_id)
        except Exception as exc:
            actions = ["rollback_failed: " + str(exc)]
        self.store.add_event(task.task_id, "scope_rollback", json.dumps(actions, ensure_ascii=False))
        reason = "scope violation during " + phase + ": " + ", ".join(result.violations)
        return self._finish_blocked(task, attempts, reason, "review the diff and logs; out-of-scope writes were rolled back")

    def _finish_success(self, task: Task, attempts: List[AttemptResult], last: AttemptResult) -> Dict[str, Any]:
        updated = self._set_status(
            task.task_id, "succeeded", runner_pid=None, current_phase="done",
            last_error_category=None, next_action=None, result_path=str(result_path(task.task_id, self.root)),
        )
        self._write_result(updated, attempts, "succeeded", None)
        self.store.add_event(task.task_id, "succeeded", "acceptance passed")
        self.telegram.notify("task_succeeded", updated, {"phase": "succeeded", "next": "none"})
        return self._report(updated, "succeeded")

    def _finish_blocked(self, task: Task, attempts: List[AttemptResult], reason: str, next_action: str) -> Dict[str, Any]:
        updated = self._set_status(
            task.task_id, "blocked", runner_pid=None, current_phase="stopped",
            needs_approval_reason=reason, next_action=next_action,
            result_path=str(result_path(task.task_id, self.root)),
        )
        self._write_result(updated, attempts, "blocked", reason)
        self.store.add_event(task.task_id, "blocked", reason)
        self.telegram.notify("task_blocked", updated, {"phase": "blocked", "next": next_action, "reason": reason})
        return self._report(updated, "blocked")

    def _write_result(self, task: Task, attempts: List[AttemptResult], status: str, reason: Optional[str]) -> None:
        budgets = self.store.budget_totals(task.task_id)
        data = {
            "task": task.public(),
            "status": status,
            "reason": reason,
            "finished_at": now_iso(),
            "attempts": [item.public() for item in attempts],
            "budget": budgets,
            "workspace": task.working_directory,
            "rollback": {
                "snapshot": str(task_dir(task.task_id, self.root) / "attempts" / "01" / "before"),
                "command": "python run.py rollback " + task.task_id + " --yes",
            },
        }
        path = result_path(task.task_id, self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.redactor.data(data), ensure_ascii=False, indent=2), encoding="utf-8")
        task_json = task_dir(task.task_id, self.root) / "task.json"
        if task_json.exists():
            pass

    def _report(self, task: Task, message: str) -> Dict[str, Any]:
        if task is None:
            return {"message": message}
        return {"task": task.public(), "message": message, "attempts": self.store.list_attempts(task.task_id)}

    # --------------------------------------------------------------- recovery
    def recover(self, apply: bool = False) -> List[Dict[str, Any]]:
        findings = []
        for task in self.store.find_inflight():
            alive = pid_alive(int(task.runner_pid or 0))
            finding = {"task_id": task.task_id, "status": task.status, "runner_pid": task.runner_pid, "alive": alive}
            findings.append(finding)
            if not alive and apply:
                self.store.set_fields(task.task_id, runner_pid=None, last_error_category="orphaned",
                                      next_action="inspect logs and resume explicitly")
                self._set_status(task.task_id, "blocked", needs_approval_reason="runner process died before completion")
                self.store.add_event(task.task_id, "recovered_orphan", "previous pid " + str(task.runner_pid))
        for lock_file in sorted((self.root / "locks").glob("*.lock")):
            try:
                owner = json.loads(lock_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(owner, dict) and not pid_alive(int(owner.get("pid") or 0)):
                findings.append({"lock": str(lock_file), "stale": True, "owner": owner})
        return findings

    def resume(self, task_id: str, force: bool = False) -> Task:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskError("unknown task")
        if task.status not in {"blocked", "needs_approval"}:
            raise TaskError("resume is only valid from blocked or needs_approval, got " + task.status)
        if task.status == "needs_approval" and not task.approved:
            raise TaskError("task still needs approval; run approve first")
        if int(task.attempt_count) > 0 and not force:
            raise TaskError(
                "task already has %d attempt(s); resuming could repeat execution and charges. "
                "Use --force after reviewing the diff, or create a new task." % int(task.attempt_count)
            )
        self.store.set_fields(task_id, runner_pid=None, last_error_category=None,
                              needs_approval_reason=None, next_action=None)
        return self._set_status(task_id, "queued")

    def cancel(self, task_id: str) -> Task:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskError("unknown task")
        if task.status in {"succeeded", "failed", "cancelled"}:
            return task
        if task.status in {"running", "executing", "verifying", "needs_expert"} and pid_alive(int(task.runner_pid or 0)):
            raise TaskError("task is actively running under pid %s; refusing to cancel without stopping it" % task.runner_pid)
        self.store.set_fields(task_id, runner_pid=None, needs_approval_reason="cancelled by user")
        return self._set_status(task_id, "cancelled")

    def approve(self, task_id: str, approved_by: str) -> Task:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskError("unknown task")
        if task.status != "needs_approval":
            raise TaskError("approve is only valid from needs_approval, got " + task.status)
        self.store.set_fields(task_id, approved=True, approved_by=approved_by, approved_at=now_iso())
        return self._set_status(task_id, "queued", needs_approval_reason=None)

    def rollback(self, task_id: str) -> List[str]:
        directory = task_dir(task_id, self.root) / "attempts" / "01" / "before"
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            raise TaskError("no initial snapshot for " + task_id)
        from .snapshot import load_manifest

        manifest = load_manifest(manifest_path)
        current = scan(Path(manifest.root), exclude_dirs=self.policy["paths"]["exclude_dirs"])
        added = sorted(set(current.files) - set(manifest.files))
        actions = restore(Path(manifest.root), directory, manifest, added)
        self.store.add_event(task_id, "rollback", str(len(actions)) + " actions")
        return actions
