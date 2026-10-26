from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import ALLOWED_TRANSITIONS, Task, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    working_directory TEXT NOT NULL,
    allowed_paths TEXT NOT NULL,
    acceptance_commands TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL,
    executor TEXT NOT NULL,
    result_path TEXT,
    last_error_category TEXT,
    read_only INTEGER NOT NULL DEFAULT 0,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    approved INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    approved_at TEXT,
    runner_pid INTEGER,
    current_phase TEXT,
    needs_approval_reason TEXT,
    next_action TEXT,
    simulation TEXT NOT NULL DEFAULT '{}',
    model_calls INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    hermes_task_id TEXT,
    idempotency_key TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    executor TEXT NOT NULL,
    phase TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    exit_code INTEGER,
    status TEXT,
    error_category TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS budget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    task_id TEXT,
    provider TEXT NOT NULL,
    model TEXT,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_task ON attempts(task_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_budget_task ON budget(task_id);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._ensure_columns()
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_hermes ON tasks(hermes_task_id) "
            "WHERE hermes_task_id IS NOT NULL"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idem ON tasks(idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )

    def _ensure_columns(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()}
        additions = {
            "hermes_task_id": "TEXT",
            "idempotency_key": "TEXT",
        }
        for name, ddl in additions.items():
            if name not in existing:
                self._conn.execute("ALTER TABLE tasks ADD COLUMN " + name + " " + ddl)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _tx(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(sql, tuple(params))
                self._conn.execute("COMMIT")
                return cur
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def insert_task(self, task: Task) -> None:
        self._tx(
            """INSERT INTO tasks (task_id, summary, status, created_at, updated_at, working_directory,
            allowed_paths, acceptance_commands, timeout_seconds, attempt_count, risk_level, executor,
            result_path, last_error_category, read_only, requires_approval, approved, approved_by,
            approved_at, runner_pid, current_phase, needs_approval_reason, next_action, simulation,
            model_calls, estimated_cost_usd, hermes_task_id, idempotency_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.task_id, task.summary, task.status, task.created_at, task.updated_at,
                task.working_directory, json.dumps(task.allowed_paths, ensure_ascii=False),
                json.dumps(task.acceptance_commands, ensure_ascii=False), task.timeout_seconds,
                task.attempt_count, task.risk_level, task.executor, task.result_path,
                task.last_error_category, int(task.read_only), int(task.requires_approval),
                int(task.approved), task.approved_by, task.approved_at, task.runner_pid,
                task.current_phase, task.needs_approval_reason, task.next_action,
                json.dumps(task.simulation, ensure_ascii=False), task.model_calls,
                task.estimated_cost_usd, task.hermes_task_id, task.idempotency_key,
            ),
        )

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return Task.from_row(row) if row else None

    def get_task_by_hermes_id(self, hermes_task_id: str) -> Optional[Task]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE hermes_task_id = ?", (hermes_task_id,)
            ).fetchone()
        return Task.from_row(row) if row else None

    def get_task_by_idempotency(self, key: str) -> Optional[Task]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return Task.from_row(row) if row else None

    def list_tasks(self, status: Optional[str] = None) -> List[Task]:
        with self._lock:
            if status:
                rows = self._conn.execute("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [Task.from_row(row) for row in rows]

    def transition(self, task_id: str, new_status: str, **fields: Any) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError("unknown task: " + task_id)
        if new_status != task.status:
            allowed = ALLOWED_TRANSITIONS.get(task.status, set())
            if new_status not in allowed:
                raise ValueError("illegal transition {} -> {} for {}".format(task.status, new_status, task_id))
        sets = ["status = ?", "updated_at = ?"]
        params: List[Any] = [new_status, now_iso()]
        for key, value in fields.items():
            if not hasattr(task, key):
                raise ValueError("unknown task field: " + key)
            sets.append(key + " = ?")
            params.append(int(value) if isinstance(value, bool) else value)
        params.append(task_id)
        self._tx("UPDATE tasks SET " + ", ".join(sets) + " WHERE task_id = ?", params)
        updated = self.get_task(task_id)
        assert updated is not None
        return updated

    def set_fields(self, task_id: str, **fields: Any) -> Task:
        if not fields:
            return self.get_task(task_id)  # type: ignore[return-value]
        sets = []
        params: List[Any] = []
        for key, value in fields.items():
            sets.append(key + " = ?")
            params.append(int(value) if isinstance(value, bool) else value)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(task_id)
        self._tx("UPDATE tasks SET " + ", ".join(sets) + " WHERE task_id = ?", params)
        return self.get_task(task_id)  # type: ignore[return-value]

    def add_attempt(self, task_id: str, attempt_no: int, executor: str, phase: str) -> int:
        cur = self._tx(
            "INSERT INTO attempts (task_id, attempt_no, executor, phase, started_at) VALUES (?,?,?,?,?)",
            (task_id, attempt_no, executor, phase, now_iso()),
        )
        return int(cur.lastrowid)

    def finish_attempt(self, attempt_id: int, **fields: Any) -> None:
        fields.setdefault("finished_at", now_iso())
        sets = []
        params: List[Any] = []
        for key, value in fields.items():
            sets.append(key + " = ?")
            params.append(value)
        params.append(attempt_id)
        self._tx("UPDATE attempts SET " + ", ".join(sets) + " WHERE id = ?", params)

    def add_event(self, task_id: Optional[str], kind: str, detail: str = "") -> None:
        self._tx("INSERT INTO events (task_id, at, kind, detail) VALUES (?,?,?,?)", (task_id, now_iso(), kind, detail))

    def list_attempts(self, task_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM attempts WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, task_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit)
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_budget(self, task_id: Optional[str], provider: str, model: Optional[str], calls: int = 1,
                   cost_usd: float = 0.0, note: str = "") -> None:
        self._tx(
            "INSERT INTO budget (at, task_id, provider, model, calls, cost_usd, note) VALUES (?,?,?,?,?,?,?)",
            (now_iso(), task_id, provider, model, calls, cost_usd, note),
        )

    def budget_totals(self, task_id: Optional[str] = None) -> Dict[str, float]:
        with self._lock:
            if task_id:
                row = self._conn.execute(
                    "SELECT COALESCE(SUM(calls),0) AS calls, COALESCE(SUM(cost_usd),0) AS cost FROM budget WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
            else:
                row = self._conn.execute("SELECT COALESCE(SUM(calls),0) AS calls, COALESCE(SUM(cost_usd),0) AS cost FROM budget").fetchone()
        return {"calls": int(row["calls"]), "cost_usd": float(row["cost"])}

    def find_inflight(self) -> List[Task]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status IN ('running','verifying','executing','needs_expert') ORDER BY updated_at"
            ).fetchall()
        return [Task.from_row(row) for row in rows]
