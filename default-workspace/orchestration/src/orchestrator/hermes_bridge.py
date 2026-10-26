from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ORCH_ROOT, public_env, runtime_root
from .lock import pid_alive
from .proc import run_capture
from .redact import Redactor

ORCH_TAG = "orchestrator_task="
LINK_RE = re.compile(r"orchestrator_task=([A-Za-z0-9._-]+)")


class HermesBridgeError(RuntimeError):
    pass


class HermesKanban:
    """Thin CLI client for the Hermes Kanban board.

    Hermes remains the owner of card state. This client never opens or edits
    the Hermes SQLite database directly.
    """

    def __init__(self, cli: str, timeout_seconds: int = 120, redactor: Optional[Redactor] = None):
        self.cli = Path(cli)
        self.timeout_seconds = int(timeout_seconds)
        self.redactor = redactor or Redactor()

    def _run(self, args: List[str], stdin_text: Optional[str] = None) -> Any:
        if not self.cli.exists():
            raise HermesBridgeError("Hermes CLI not found: " + str(self.cli))
        result = run_capture(
            [str(self.cli), *args],
            cwd=ORCH_ROOT,
            timeout_seconds=self.timeout_seconds,
            env=public_env(),
            stdin_text=stdin_text,
        )
        if result.exit_code != 0:
            raise HermesBridgeError(
                "hermes " + " ".join(args[:3]) + " failed: " + self.redactor.text(result.stderr[-1500:])
            )
        return result

    def _json(self, args: List[str]) -> Any:
        result = self._run(args)
        text = result.stdout.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise HermesBridgeError("Hermes returned non-JSON output for " + " ".join(args[:2]) + ": " + text[:500]) from exc

    def create(self, title: str, body: str, idempotency_key: str, tenant: str,
               body_dir: Path) -> Dict[str, Any]:
        body_dir.mkdir(parents=True, exist_ok=True)
        body_file = body_dir / ("card-" + uuid.uuid4().hex + ".json")
        body_file.write_text(body, encoding="utf-8")
        try:
            return self._json([
                "kanban", "create", "--json",
                "--tenant", tenant,
                "--idempotency-key", idempotency_key,
                "--body-file", str(body_file),
                title,
            ])
        finally:
            try:
                body_file.unlink()
            except OSError:
                pass

    def list_ready(self, tenant: str) -> List[Dict[str, Any]]:
        data = self._json(["kanban", "list", "--tenant", tenant, "--status", "ready", "--json"])
        return data if isinstance(data, list) else []

    def show(self, card_id: str) -> Dict[str, Any]:
        return self._json(["kanban", "show", card_id, "--json"])

    def claim(self, card_id: str) -> str:
        return self._run(["kanban", "claim", card_id]).stdout

    def comment(self, card_id: str, text: str) -> None:
        self._run(["kanban", "comment", card_id, text, "--author", "orchestrator"])

    def complete(self, card_id: str, result: str, metadata: Dict[str, Any]) -> None:
        self._run([
            "kanban", "complete", card_id,
            "--result", result,
            "--metadata", json.dumps(metadata, ensure_ascii=False),
        ])

    def block(self, card_id: str, reason: str, kind: str = "capability") -> None:
        self._run(["kanban", "block", card_id, reason, "--kind", kind])

    def unblock(self, card_id: str) -> None:
        self._run(["kanban", "unblock", card_id])

    def archive(self, card_id: str) -> None:
        self._run(["kanban", "archive", card_id])

    def send(self, target: str, message: str) -> Dict[str, Any]:
        result = self._run(["send", "--to", target, "--json", "--file", "-"], stdin_text=message)
        text = result.stdout.strip()
        try:
            return json.loads(text) if text else {"ok": True, "raw": ""}
        except json.JSONDecodeError:
            return {"ok": True, "raw": self.redactor.text(text[:500])}

    def list_targets(self, platform: str = "telegram") -> str:
        return self._run(["send", "--list", platform]).stdout


class HermesBridge:
    def __init__(self, policy: Dict[str, Any], orchestrator, root: Optional[Path] = None):
        self.policy = policy
        self.orchestrator = orchestrator
        self.root = Path(root or orchestrator.root)
        self.redactor = orchestrator.redactor
        cfg = policy.get("hermes", {})
        self.tenant = str(cfg.get("kanban_tenant", "orchestrator"))
        self.kanban = HermesKanban(
            str(cfg.get("cli", r"C:\Users\22649\AppData\Local\hermes\bin\hermes.exe")),
            int(cfg.get("timeout_seconds", 120)),
            self.redactor,
        )
        self.body_dir = self.root / "bridge"
        self.body_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ create
    def create_card(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        material = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        if self.redactor.contains_secret(material):
            raise HermesBridgeError("task spec appears to contain a credential; refusing to create the card")
        key = str(spec.get("idempotency_key") or ("orch-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]))
        title = str(spec.get("summary") or "Orchestrator task")[:120]
        body = json.dumps({k: v for k, v in spec.items() if k not in {"idempotency_key"}}, ensure_ascii=False, indent=2)
        return self.kanban.create(title, body, key, self.tenant, self.body_dir)

    # ------------------------------------------------------------------ query
    def _link(self, card: Dict[str, Any]) -> Optional[str]:
        text = json.dumps(card.get("comments") or [], ensure_ascii=False)
        match = LINK_RE.search(text)
        return match.group(1) if match else None

    def _sync(self, card_id: str, task) -> Dict[str, Any]:
        public = task.public()
        result_path = public.get("result_path")
        metadata = {
            "orchestrator_task_id": task.task_id,
            "status": task.status,
            "result_path": result_path,
            "attempt_count": task.attempt_count,
            "model_calls": task.model_calls,
            "estimated_cost_usd": task.estimated_cost_usd,
            "hermes_task_id": card_id,
        }
        if task.status == "succeeded":
            try:
                self.kanban.unblock(card_id)
            except Exception:
                pass
            self.kanban.complete(
                card_id,
                "orchestrator succeeded: " + task.task_id,
                metadata,
            )
            return {"card_id": card_id, "status": "completed", "task_id": task.task_id}
        kind = "needs_input" if task.status in {"needs_approval", "cancelled"} else "capability"
        reason = "orchestrator %s: %s" % (task.status, (task.needs_approval_reason or task.next_action or "")[:300])
        self.kanban.block(card_id, reason, kind)
        return {"card_id": card_id, "status": "blocked", "task_id": task.task_id, "reason": reason}

    def _task_for_card(self, card_id: str, card: Dict[str, Any]):
        linked = self._link(card)
        task = self.orchestrator.store.get_task(linked) if linked else None
        if task is None:
            task = self.orchestrator.store.get_task_by_hermes_id(card_id)
        return linked, task

    def _handle_linked(self, card_id: str, task, card_status: str) -> Dict[str, Any]:
        if task.status == "queued":
            try:
                self.kanban.unblock(card_id)
            except Exception:
                pass
            report = self.orchestrator.run(task.task_id)
            refreshed = self.orchestrator.store.get_task(task.task_id)
            synced = self._sync(card_id, refreshed)
            synced["run_message"] = report.get("message")
            return synced
        if task.status in {"running", "verifying", "executing", "needs_expert"}:
            if pid_alive(int(task.runner_pid or 0)):
                return {"card_id": card_id, "status": "linked_running", "task_id": task.task_id}
            self.orchestrator.recover(apply=True)
            task = self.orchestrator.store.get_task(task.task_id)
            if task and task.status == "blocked":
                self.kanban.block(card_id, "orchestrator runner died; task recovered as blocked", "transient")
                return {"card_id": card_id, "status": "blocked", "task_id": task.task_id,
                        "reason": "recovered_orphan"}
        if task.status in {"blocked", "needs_approval"}:
            if card_status in {"running", "ready", "todo"}:
                self.kanban.block(card_id, "orchestrator task is " + task.status, "needs_input")
            return {"card_id": card_id, "status": "linked_pending", "task_id": task.task_id,
                    "task_status": task.status}
        return self._sync(card_id, task)

    def dispatch_card(self, card_id: str) -> Dict[str, Any]:
        card = self.kanban.show(card_id)
        task_data = card.get("task") or {}
        status = task_data.get("status")
        if status in {"done", "archived"}:
            return {"card_id": card_id, "status": "noop", "reason": "card is " + str(status)}
        linked, existing = self._task_for_card(card_id, card)
        if existing is not None:
            return self._handle_linked(card_id, existing, status)

        if status not in {"ready", "running", "todo"}:
            return {"card_id": card_id, "status": "skipped", "reason": "card status " + str(status)}
        if status in {"ready", "todo"}:
            self.kanban.claim(card_id)
            self.kanban.comment(card_id, "claimed by orchestrator adapter; Hermes card is the state owner")

        body = str(task_data.get("body") or "").strip()
        try:
            spec = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HermesBridgeError("card body is not a JSON task spec") from exc
        if not isinstance(spec, dict):
            raise HermesBridgeError("card body must be a JSON object")
        spec = dict(spec)
        spec["hermes_task_id"] = card_id
        spec["idempotency_key"] = "hermes:" + card_id
        task = self.orchestrator.create_task(spec)
        # Persist the link before execution so a crash cannot orphan the card.
        self.kanban.comment(card_id, ORCH_TAG + task.task_id + " result=" + str(task.result_path))
        report = self.orchestrator.run(task.task_id)
        task = self.orchestrator.store.get_task(task.task_id)
        if task is None:
            raise HermesBridgeError("task disappeared after run: " + report.get("message", ""))
        synced = self._sync(card_id, task)
        synced["run_message"] = report.get("message")
        return synced

    # ----------------------------------------------------------------- reconcile
    def reconcile(self, limit: int = 1) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for card in self.kanban.list_ready(self.tenant)[:limit]:
            card_id = str(card.get("id"))
            try:
                results.append(self.dispatch_card(card_id))
            except Exception as exc:
                results.append({"card_id": card_id, "status": "error", "error": type(exc).__name__ + ": " + str(exc)[:500]})
        return results

    def query_card(self, card_id: str) -> Dict[str, Any]:
        card = self.kanban.show(card_id)
        task_data = card.get("task") or {}
        linked, task = self._task_for_card(card_id, card)
        return {
            "card_id": card_id,
            "card_status": task_data.get("status"),
            "orchestrator_task_id": task.task_id if task else linked,
            "orchestrator_status": task.status if task else None,
            "result_path": task.result_path if task else None,
            "consistent": bool(
                (task and (
                    (task.status == "succeeded" and task_data.get("status") == "done") or
                    (task.status != "succeeded" and task_data.get("status") in {"blocked", "running"})
                )) or (
                    task is None
                    and task_data.get("status") == "blocked"
                    and "cancelled" in json.dumps(card, ensure_ascii=False).lower()
                )
            ),
        }

    def cancel_card(self, card_id: str) -> Dict[str, Any]:
        card = self.kanban.show(card_id)
        task_data = card.get("task") or {}
        if task_data.get("status") in {"done", "archived"}:
            return {"card_id": card_id, "status": "noop", "reason": "card is " + str(task_data.get("status"))}
        linked, existing = self._task_for_card(card_id, card)
        if existing is not None:
            task = self.orchestrator.cancel(existing.task_id)
            self.kanban.block(card_id, "cancelled by user; orchestrator task " + task.task_id, "needs_input")
            return {"card_id": card_id, "status": "cancelled", "task_id": task.task_id}
        self.kanban.block(card_id, "cancelled by user before execution", "needs_input")
        return {"card_id": card_id, "status": "cancelled", "task_id": None}
