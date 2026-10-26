from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ORCH_ROOT, is_onedrive_path, load_policy, runtime_root
from .hermes_bridge import HermesBridge
from .runner import Orchestrator, TaskError

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_BLOCKED = 3


def _print(value, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, dict) and "task" in value:
        task = value["task"]
        print("task:    " + task["task_id"])
        print("status:  " + task["status"])
        print("summary: " + task["summary"])
        print("message: " + str(value.get("message", "")))
        if task.get("needs_approval_reason"):
            print("reason:  " + str(task["needs_approval_reason"]))
        if task.get("next_action"):
            print("next:    " + str(task["next_action"]))
        print("result:  " + str(task.get("result_path")))
        return
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-orchestrator", description="Local multi-agent task orchestrator")
    parser.add_argument("--root", default=None, help="runtime root (default: policy runtime_root)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the local state database and directories")

    create = sub.add_parser("create", help="create a task from JSON")
    create.add_argument("--file", required=True)

    run = sub.add_parser("run", help="run a queued task")
    run.add_argument("task_id")
    run.add_argument("--executor", choices=["opencode", "simulated", "deepseek_expert"], default=None)

    status = sub.add_parser("status", help="show one task")
    status.add_argument("task_id")

    listing = sub.add_parser("list", help="list tasks")
    listing.add_argument("--status", default=None)

    recover = sub.add_parser("recover", help="find orphaned runs and stale locks")
    recover.add_argument("--apply", action="store_true")

    resume = sub.add_parser("resume", help="requeue a blocked task")
    resume.add_argument("task_id")
    resume.add_argument("--force", action="store_true", help="allow resume after attempts (may repeat work/charges)")

    approve = sub.add_parser("approve", help="approve a needs_approval task")
    approve.add_argument("task_id")
    approve.add_argument("--by", default="local-user")

    rollback = sub.add_parser("rollback", help="restore the initial snapshot")
    rollback.add_argument("task_id")
    rollback.add_argument("--yes", action="store_true")

    report = sub.add_parser("report", help="task timeline: attempts, gates, evidence (raw result embedded)")
    report.add_argument("task_id")

    sub.add_parser("preflight", help="check configured binaries without executing tasks")

    cancel = sub.add_parser("cancel", help="cancel a task that is not actively running")
    cancel.add_argument("task_id")


    hcreate = sub.add_parser("hermes-create", help="create a Hermes Kanban card from a JSON spec")
    hcreate.add_argument("--file", required=True)

    hdispatch = sub.add_parser("hermes-dispatch", help="claim and execute one Hermes Kanban card")
    hdispatch.add_argument("--card", required=True)

    hreconcile = sub.add_parser("hermes-reconcile", help="one-pass adapter: execute ready orchestrator cards")
    hreconcile.add_argument("--limit", type=int, default=1)

    hquery = sub.add_parser("hermes-query", help="show Hermes card and linked orchestrator state")
    hquery.add_argument("--card", required=True)

    hcancel = sub.add_parser("hermes-cancel", help="cancel a Hermes card and its orchestrator task")
    hcancel.add_argument("--card", required=True)

    sub.add_parser("telegram-check", help="check whether Hermes has authorized Telegram targets")

    telegram = sub.add_parser("telegram-test", help="build a sample report (dry run by default)")
    telegram.add_argument("--send", action="store_true")

    return parser


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    policy = load_policy()
    root = Path(args.root).resolve() if args.root else runtime_root(policy)
    orchestrator = Orchestrator(policy, root)
    try:
        if args.command == "init":
            _print({"ok": True, "runtime_root": str(root), "db": str(root / "state" / "orchestrator.db"),
                    "runtime_on_onedrive": is_onedrive_path(root)}, args.json)
            return EXIT_OK
        if args.command == "create":
            payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
            task = orchestrator.create_task(payload)
            _print({"task": task.public(), "message": "created"}, args.json)
            return EXIT_OK
        if args.command == "run":
            report = orchestrator.run(args.task_id, args.executor)
            _print(report, args.json)
            status = report.get("task", {}).get("status")
            return EXIT_BLOCKED if status in {"blocked", "needs_approval"} else EXIT_OK
        if args.command == "status":
            task = orchestrator.store.get_task(args.task_id)
            if task is None:
                _print({"error": "unknown task"}, args.json)
                return EXIT_ERROR
            data = {"task": task.public(), "attempts": orchestrator.store.list_attempts(task.task_id),
                    "events": orchestrator.store.list_events(task.task_id, 30)}
            _print(data, args.json)
            return EXIT_OK
        if args.command == "list":
            tasks = orchestrator.store.list_tasks(args.status)
            _print([task.public() for task in tasks], True)
            return EXIT_OK
        if args.command == "recover":
            findings = orchestrator.recover(apply=args.apply)
            _print({"findings": findings, "applied": args.apply}, args.json)
            return EXIT_OK
        if args.command == "resume":
            task = orchestrator.resume(args.task_id, force=args.force)
            _print({"task": task.public(), "message": "requeued"}, args.json)
            return EXIT_OK
        if args.command == "cancel":
            task = orchestrator.cancel(args.task_id)
            _print({"task": task.public(), "message": "cancelled"}, args.json)
            return EXIT_OK
        if args.command == "report":
            from .report import build_task_report
            _print(build_task_report(args.task_id, root), args.json)
            return EXIT_OK
        if args.command in {"hermes-create", "hermes-dispatch", "hermes-reconcile", "hermes-query", "hermes-cancel"}:
            bridge = HermesBridge(policy, orchestrator, root)
            if args.command == "hermes-create":
                spec = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
                card = bridge.create_card(spec)
                _print({"card": card, "message": "created"}, args.json)
                return EXIT_OK
            if args.command == "hermes-dispatch":
                result = bridge.dispatch_card(args.card)
                _print(result, args.json)
                return EXIT_BLOCKED if result.get("status") == "blocked" else EXIT_OK
            if args.command == "hermes-reconcile":
                results = bridge.reconcile(args.limit)
                _print({"results": results}, args.json)
                return EXIT_BLOCKED if any(r.get("status") == "blocked" for r in results) else EXIT_OK
            if args.command == "hermes-query":
                _print(bridge.query_card(args.card), args.json)
                return EXIT_OK
            if args.command == "hermes-cancel":
                _print(bridge.cancel_card(args.card), args.json)
                return EXIT_OK
        if args.command == "approve":
            task = orchestrator.approve(args.task_id, args.by)
            _print({"task": task.public(), "message": "approved"}, args.json)
            return EXIT_OK
        if args.command == "rollback":
            if not args.yes:
                _print({"error": "rollback requires --yes; it restores files from the initial snapshot"}, args.json)
                return EXIT_ERROR
            actions = orchestrator.rollback(args.task_id)
            _print({"task_id": args.task_id, "actions": actions}, args.json)
            return EXIT_OK
        if args.command == "report":
            from .report import build_task_report
            data = build_task_report(args.task_id, root)
            path = data.get("links", {}).get("result")
            if path and not Path(path).exists():
                _print({"error": "no result file", "path": path}, args.json)
                return EXIT_ERROR
            _print(data, args.json)
            return EXIT_OK
        if args.command == "preflight":
            checks = []
            for name, path in [
                ("hermes_cli", r"C:\Users\22649\AppData\Local\hermes\bin\hermes.exe"),
                ("opencode_cli", policy["opencode"]["cli"]),
                ("dsh_cli", policy["expert"]["dsh_cli"]),
            ]:
                checks.append({"name": name, "path": path, "exists": Path(path).exists()})
            checks.append({"name": "env_file", "path": policy["secrets"]["env_file"],
                           "exists": Path(policy["secrets"]["env_file"]).exists()})
            checks.append({"name": "runtime_root", "path": str(root), "exists": root.exists(),
                           "on_onedrive": is_onedrive_path(root)})
            _print({"checks": checks}, args.json)
            return EXIT_OK
        if args.command == "telegram-check":
            _print(orchestrator.telegram.check_targets(), args.json)
            return EXIT_OK
        if args.command == "telegram-test":
            from .models import Task, now_iso
            sample = Task(
                task_id="T-DEMO-TELEGRAM", summary="Report delivery smoke test (no repository writes)",
                status="succeeded", created_at=now_iso(), updated_at=now_iso(),
                working_directory=str(root), allowed_paths=[], acceptance_commands=[["cmd", "/c", "echo ok"]],
                timeout_seconds=60, attempt_count=1, risk_level="low", executor="none",
                result_path=None, last_error_category=None,
            )
            if args.send:
                if not policy["telegram"].get("authorized"):
                    _print({"error": "telegram target is not authorized in policy; refusing to send"}, args.json)
                    return EXIT_ERROR
                policy["telegram"]["dry_run"] = False
            result = orchestrator.telegram.notify("task_succeeded", sample, {"phase": "succeeded", "next": "none"})
            _print(result, args.json)
            return EXIT_OK
        _print({"error": "unknown command"}, args.json)
        return EXIT_ERROR
    except (TaskError, json.JSONDecodeError, FileNotFoundError) as exc:
        _print({"error": str(exc)}, args.json)
        return EXIT_ERROR
    except Exception as exc:
        _print({"error": type(exc).__name__ + ": " + str(exc)}, args.json)
        return EXIT_ERROR
    finally:
        orchestrator.close()


if __name__ == "__main__":
    raise SystemExit(main())
