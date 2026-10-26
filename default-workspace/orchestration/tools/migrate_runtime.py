from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import ORCH_ROOT, is_onedrive_path, load_policy, runtime_root  # noqa: E402
from orchestrator.lock import pid_alive  # noqa: E402


def db_counts(path: Path) -> dict:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        out = {}
        for table in ("tasks", "attempts", "events", "budget"):
            out[table] = conn.execute("SELECT COUNT(*) AS n FROM " + table).fetchone()["n"]
        return out
    finally:
        conn.close()


def live_activity(source: Path) -> list[dict]:
    findings = []
    db = source / "state" / "orchestrator.db"
    if db.exists():
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT task_id, status, runner_pid FROM tasks "
                "WHERE status IN ('running','verifying','executing','needs_expert')"
            ).fetchall()
            for row in rows:
                if pid_alive(int(row["runner_pid"] or 0)):
                    findings.append({"kind": "active_task", "task_id": row["task_id"],
                                     "status": row["status"], "pid": row["runner_pid"]})
        finally:
            conn.close()
    lock = source / "locks" / "worktree.lock"
    if lock.exists():
        try:
            owner = json.loads(lock.read_text(encoding="utf-8"))
            if pid_alive(int(owner.get("pid") or 0)):
                findings.append({"kind": "active_lock", "owner": owner})
        except (OSError, json.JSONDecodeError):
            pass
    return findings


def copy_db(source_db: Path, dest_db: Path) -> None:
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source_db))
    dst = sqlite3.connect(str(dest_db))
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=WAL")
        dst.execute("PRAGMA synchronous=FULL")
    finally:
        dst.close()
        src.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--source", default=str(ORCH_ROOT))
    parser.add_argument("--dest", default=None)
    args = parser.parse_args()

    policy = load_policy()
    source = Path(args.source).resolve()
    dest = Path(args.dest).resolve() if args.dest else runtime_root(policy)
    if source == dest:
        print(json.dumps({"ok": False, "error": "source and destination are identical"}, indent=2))
        return 2
    if is_onedrive_path(dest):
        print(json.dumps({"ok": False, "error": "destination is still inside OneDrive", "dest": str(dest)}, indent=2))
        return 2
    findings = live_activity(source)
    if findings:
        print(json.dumps({"ok": False, "error": "active orchestration activity; stop it before migration",
                          "findings": findings}, indent=2))
        return 3
    if dest.exists() and any(dest.iterdir()):
        print(json.dumps({"ok": False, "error": "destination already exists and is not empty", "dest": str(dest)}, indent=2))
        return 4

    plan = {
        "source": str(source),
        "dest": str(dest),
        "source_on_onedrive": is_onedrive_path(source),
        "dest_on_onedrive": is_onedrive_path(dest),
        "live_findings": findings,
        "apply": bool(args.apply),
    }
    if not args.apply:
        print(json.dumps({"ok": True, "dry_run": True, "plan": plan}, indent=2))
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    source_db = source / "state" / "orchestrator.db"
    counts_before = db_counts(source_db) if source_db.exists() else {}
    if source_db.exists():
        conn = sqlite3.connect(str(source_db))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        copy_db(source_db, dest / "state" / "orchestrator.db")
    counts_after = db_counts(dest / "state" / "orchestrator.db") if (dest / "state" / "orchestrator.db").exists() else {}

    copy_stats = {}
    for name in ("tasks", "results", "logs", "snapshots"):
        src = source / name
        if not src.exists():
            copy_stats[name] = 0
            continue
        target = dest / name
        shutil.copytree(src, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        copy_stats[name] = sum(1 for p in target.rglob("*") if p.is_file())
    (dest / "locks").mkdir(parents=True, exist_ok=True)
    (dest / "config").mkdir(parents=True, exist_ok=True)

    record = {
        "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source),
        "dest": str(dest),
        "counts_before": counts_before,
        "counts_after": counts_after,
        "file_counts": copy_stats,
        "source_preserved": True,
        "note": "Source directory is preserved unchanged as a backup. New runtime data uses dest.",
    }
    (dest / "MIGRATION.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (source / "RUNTIME_MOVED.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = counts_before == counts_after
    print(json.dumps({"ok": ok, "record": record}, ensure_ascii=False, indent=2))
    return 0 if ok else 5


if __name__ == "__main__":
    raise SystemExit(main())
