from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import db_path, ensure_layout
from orchestrator.db import Store
from orchestrator.lock import WorktreeLock
from orchestrator.runner import BudgetExceeded, Orchestrator, TaskError

try:
    from tests.test_support import test_policy
except ImportError:
    from test_support import test_policy

PY = sys.executable


def make_orch(base: Path, expert_mode: str = "simulated", expert_enabled: bool = True,
              max_calls: int = 6) -> tuple[Orchestrator, Path]:
    root = base / "orch"
    policy = test_policy(root)
    policy["expert"]["mode"] = expert_mode
    policy["expert"]["enabled"] = expert_enabled
    policy["limits"]["max_model_calls_per_task"] = max_calls
    policy["telegram"]["enabled"] = False
    ensure_layout(root)
    store = Store(db_path(root))
    return Orchestrator(policy, root, store), root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def payload(work: Path, **overrides):
    data = {
        "summary": "test task",
        "working_directory": str(work),
        "allowed_paths": [str(work)],
        "acceptance_commands": [[PY, "-c", "import sys; sys.exit(0)"]],
        "timeout_seconds": 30,
        "risk_level": "low",
        "executor": "simulated",
        "simulation": {},
    }
    data.update(overrides)
    return data


def test_read_only_success(base: Path) -> None:
    work = base / "w1"; work.mkdir()
    (work / "a.txt").write_text("x", encoding="utf-8")
    orch, _ = make_orch(base)
    try:
        task = orch.create_task(payload(work, read_only=True, allowed_paths=[],
            acceptance_commands=[[PY, "-c", "import os,sys; sys.exit(0 if os.path.isdir('.') else 1)"]],
            simulation={"opencode_attempts": [{"exit_code": 0, "stdout": "listed"}]}))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "succeeded", report
        assert report["task"]["model_calls"] == 0
        assert Path(report["task"]["result_path"]).exists()
    finally:
        orch.close()


def test_retry_then_success(base: Path) -> None:
    work = base / "w2"; work.mkdir()
    write(work / "app.py", "value = 1\n")
    orch, _ = make_orch(base)
    try:
        task = orch.create_task(payload(work,
            acceptance_commands=[[PY, "-c", "import pathlib; t=pathlib.Path('app.py').read_text(); raise SystemExit(0 if 'value = 2' in t else 1)"]],
            simulation={"opencode_attempts": [
                {"exit_code": 1, "stderr": "AssertionError: expected 2", "stdout": "failed"},
                {"exit_code": 0, "stdout": "fixed", "write": {"app.py": "value = 2\n"}},
            ]}))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "succeeded", report
        assert report["task"]["attempt_count"] == 2
        result = json.loads(Path(report["task"]["result_path"]).read_text(encoding="utf-8"))
        assert result["attempts"][1]["diff"]["modified"] == ["app.py"]
        patch = Path(result["attempts"][1]["patch_path"]).read_text(encoding="utf-8")
        assert "-value = 1" in patch and "+value = 2" in patch
    finally:
        orch.close()


def test_acceptance_blocks_false_success(base: Path) -> None:
    work = base / "w3"; work.mkdir()
    orch, _ = make_orch(base, expert_enabled=False)
    try:
        task = orch.create_task(payload(work,
            acceptance_commands=[[PY, "-c", "import pathlib; raise SystemExit(0 if pathlib.Path('made.txt').exists() else 1)"]],
            simulation={"opencode_attempts": [{"exit_code": 0, "stdout": "I claim success"}]}))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "blocked", report
        assert report["task"]["last_error_category"] == "acceptance", report
        assert report["task"]["attempt_count"] == 2
    finally:
        orch.close()


def test_expert_analysis_then_takeover(base: Path) -> None:
    work = base / "w4"; work.mkdir()
    write(work / "calc.py", "def add(a, b):\n    return a - b\n")
    orch, _ = make_orch(base)
    try:
        task = orch.create_task(payload(work,
            acceptance_commands=[[PY, "-c", "import calc; raise SystemExit(0 if calc.add(2,3)==5 else 1)"]],
            simulation={
                "opencode_attempts": [
                    {"exit_code": 1, "stderr": "AssertionError: add(2,3) != 5"},
                    {"exit_code": 1, "stderr": "AssertionError: still wrong"},
                    {"exit_code": 1, "stderr": "AssertionError: plan not applied"},
                ],
                "expert_analysis": [{"exit_code": 0, "stdout": '{"root_cause":"wrong operator","plan":["replace - with +"]}'}],
                "expert_takeover": [{"exit_code": 0, "stdout": "fixed", "write": {"calc.py": "def add(a, b):\n    return a + b\n"}}],
            }))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "succeeded", report
        assert report["task"]["attempt_count"] == 4
        executors = [a["executor"] for a in report["attempts"]]
        assert "deepseek_expert" in executors, executors
        assert (orch.root / "tasks" / task.task_id / "analysis.json").exists()
        events = [e["kind"] for e in orch.store.list_events(task.task_id)]
        assert "expert_takeover" in events
    finally:
        orch.close()


def test_retry_cap_stops(base: Path) -> None:
    work = base / "w5"; work.mkdir()
    orch, _ = make_orch(base, expert_enabled=False)
    try:
        task = orch.create_task(payload(work,
            acceptance_commands=[[PY, "-c", "import sys; sys.exit(1)"]],
            simulation={"opencode_attempts": [{"exit_code": 1, "stderr": "AssertionError: broken"}]}))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "blocked", report
        assert report["task"]["attempt_count"] == 2
        assert len(report["attempts"]) == 2
    finally:
        orch.close()


def test_write_lock_blocks_second_writer(base: Path) -> None:
    work = base / "w6"; work.mkdir()
    orch, root = make_orch(base)
    lock = WorktreeLock(root / "locks" / "worktree.lock", "OTHER-TASK")
    lock.acquire()
    try:
        task = orch.create_task(payload(work, simulation={"opencode_attempts": [{"exit_code": 0}]}))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "blocked", report
        assert report["task"]["last_error_category"] == "lock_busy", report
    finally:
        lock.release()
    orch.resume(task.task_id)
    report = orch.run(task.task_id)
    assert report["task"]["status"] == "succeeded", report
    orch.close()


def test_rollback_restores_snapshot(base: Path) -> None:
    work = base / "w7"; work.mkdir()
    write(work / "data.txt", "original\n")
    orch, _ = make_orch(base)
    try:
        task = orch.create_task(payload(work,
            acceptance_commands=[[PY, "-c", "import pathlib; raise SystemExit(0 if pathlib.Path('data.txt').read_text().startswith('changed') else 1)"]],
            simulation={"opencode_attempts": [{"exit_code": 0, "write": {"data.txt": "changed\n", "new.txt": "new\n"}}]}))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "succeeded", report
        assert (work / "new.txt").exists()
        actions = orch.rollback(task.task_id)
        assert (work / "data.txt").read_text(encoding="utf-8") == "original\n"
        assert not (work / "new.txt").exists()
        assert actions
    finally:
        orch.close()


def test_recover_orphan(base: Path) -> None:
    work = base / "w8"; work.mkdir()
    orch, _ = make_orch(base)
    try:
        task = orch.create_task(payload(work))
        orch.store.transition(task.task_id, "running", runner_pid=999999)
        findings = orch.recover(apply=True)
        assert any(f.get("task_id") == task.task_id for f in findings)
        assert orch.store.get_task(task.task_id).status == "blocked"
    finally:
        orch.close()


def test_budget_guard(base: Path) -> None:
    work = base / "w9"; work.mkdir()
    orch, _ = make_orch(base, max_calls=1)
    try:
        task = orch.create_task(payload(work))
        orch.store.set_fields(task.task_id, model_calls=1)
        task = orch.store.get_task(task.task_id)
        executor = orch._executor("opencode")
        try:
            orch._check_budget(task, executor)
        except BudgetExceeded:
            pass
        else:
            raise AssertionError("budget guard did not fire")
    finally:
        orch.close()


def test_scope_violation(base: Path) -> None:
    work = base / "w10"; work.mkdir()
    allowed = work / "allowed"; allowed.mkdir()
    orch, _ = make_orch(base, expert_enabled=False)
    try:
        task = orch.create_task(payload(work, allowed_paths=[str(allowed)],
            acceptance_commands=[[PY, "-c", "import sys; sys.exit(0)"]],
            simulation={"opencode_attempts": [{"exit_code": 0, "write": {"outside.txt": "bad\n"}}]}))
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "blocked", report
        assert report["task"]["last_error_category"] == "scope_violation", report
    finally:
        orch.close()


def test_idempotent_task_reuse(base: Path) -> None:
    work = base / "w11"; work.mkdir()
    orch, _ = make_orch(base)
    try:
        p = payload(work, idempotency_key="same-key-1")
        first = orch.create_task(p)
        second = orch.create_task(p)
        assert first.task_id == second.task_id
        assert len([t for t in orch.store.list_tasks() if t.idempotency_key == "same-key-1"]) == 1
    finally:
        orch.close()


def test_resume_requires_force(base: Path) -> None:
    work = base / "w12"; work.mkdir()
    orch, _ = make_orch(base, expert_enabled=False)
    try:
        task = orch.create_task(payload(work,
            acceptance_commands=[[PY, "-c", "import sys; sys.exit(1)"]],
            simulation={"opencode_attempts": [{"exit_code": 1, "stderr": "AssertionError: always"}]}))
        orch.run(task.task_id)
        assert orch.store.get_task(task.task_id).status == "blocked"
        try:
            orch.resume(task.task_id)
        except TaskError:
            pass
        else:
            raise AssertionError("resume should require --force after attempts")
        resumed = orch.resume(task.task_id, force=True)
        assert resumed.status == "queued"
        assert resumed.attempt_count == 2
    finally:
        orch.close()


def test_cancel_queued(base: Path) -> None:
    work = base / "w13"; work.mkdir()
    orch, _ = make_orch(base)
    try:
        task = orch.create_task(payload(work))
        cancelled = orch.cancel(task.task_id)
        assert cancelled.status == "cancelled"
        report = orch.run(task.task_id)
        assert report["task"]["status"] == "cancelled"
    finally:
        orch.close()


def main() -> int:
    base = ROOT / "test-runs" / ("run-" + __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S"))
    base.mkdir(parents=True, exist_ok=True)
    failures = 0
    tests = [
        test_read_only_success,
        test_retry_then_success,
        test_acceptance_blocks_false_success,
        test_expert_analysis_then_takeover,
        test_retry_cap_stops,
        test_write_lock_blocks_second_writer,
        test_rollback_restores_snapshot,
        test_recover_orphan,
        test_budget_guard,
        test_scope_violation,
        test_idempotent_task_reuse,
        test_resume_requires_force,
        test_cancel_queued,
    ]
    for test in tests:
        case_dir = base / test.__name__
        case_dir.mkdir()
        try:
            test(case_dir)
            print("PASS", test.__name__)
        except Exception:
            failures += 1
            print("FAIL", test.__name__)
            traceback.print_exc()
    print("RESULT", "PASS" if failures == 0 else "FAIL", "failures=", failures, "dir=", base)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
