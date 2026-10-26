"""Human-readable task reports: timeline of attempts, gates, and evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import result_path, task_dir


def _diff_stats(patch_path: Optional[str]) -> Dict[str, int]:
    stats = {"patch_lines": 0, "additions": 0, "deletions": 0}
    if not patch_path:
        return stats
    p = Path(patch_path)
    if not p.exists():
        return stats
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    stats["patch_lines"] = len(lines)
    stats["additions"] = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    stats["deletions"] = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return stats


def build_task_report(task_id: str, root: Path) -> Dict[str, Any]:
    """Assemble a report dict for one task.

    Sources: runtime/results/<id>.json (per-attempt records + budget),
    runtime/tasks/<id>/ (diff patches, expert analysis.json), and the
    store's task row. Anything missing degrades gracefully.
    """
    report: Dict[str, Any] = {"task_id": task_id}

    result_file = result_path(task_id, root)
    result: Optional[Dict[str, Any]] = None
    if result_file.exists():
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = None
    if result:
        task = result.get("task") or {}
        report["status"] = result.get("status")
        report["reason"] = result.get("reason")
        report["summary"] = str(task.get("summary", ""))[:120]
        report["working_directory"] = task.get("working_directory")
        report["budget"] = result.get("budget")
        report["finished_at"] = result.get("finished_at")

        attempts: List[Dict[str, Any]] = []
        for a in result.get("attempts", []):
            acc = a.get("acceptance") or []
            attempts.append({
                "attempt_no": a.get("attempt_no"),
                "phase": a.get("phase"),
                "executor": a.get("executor"),
                "provider": a.get("provider"),
                "success": a.get("success"),
                "duration_seconds": a.get("duration_seconds"),
                "timed_out": a.get("timed_out"),
                "error_category": a.get("error_category"),
                "acceptance": [
                    {"index": x.get("index"), "passed": x.get("passed"), "exit_code": x.get("exit_code")}
                    for x in acc
                ],
                "diff": a.get("diff"),
                "diff_stats": _diff_stats(a.get("patch_path")),
                "patch_path": a.get("patch_path"),
            })
        report["attempts"] = attempts
        winner = next((a for a in reversed(attempts) if a.get("success")), None)
        if winner:
            report["deciding_attempt"] = {
                "attempt_no": winner["attempt_no"],
                "executor": winner["executor"],
                "phase": winner["phase"],
            }

    tdir = task_dir(task_id, root)
    analysis_file = tdir / "analysis.json"
    if analysis_file.exists():
        try:
            plan = json.loads(analysis_file.read_text(encoding="utf-8"))
            report["expert_analysis"] = {
                "root_cause": plan.get("root_cause"),
                "confidence": plan.get("confidence"),
                "files_to_change": plan.get("files_to_change"),
                "verification": plan.get("verification"),
            }
        except (OSError, ValueError):
            report["expert_analysis"] = {"error": "unreadable analysis.json"}

    if result is not None:
        report["raw_result"] = result

    rate_file = root / "state" / "rate_gate.json"
    if rate_file.exists():
        try:
            report["rate_gate"] = json.loads(rate_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    report["links"] = {
        "result": str(result_file),
        "task_dir": str(tdir),
        "analysis": str(analysis_file),
    }
    return report
