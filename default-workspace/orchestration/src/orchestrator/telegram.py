from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .config import public_env, secret_env
from .models import Task, now_iso
from .proc import run_capture
from .redact import Redactor


class TelegramReporter:
    """Telegram reporter that fails closed.

    Real delivery requires enabled=true, dry_run=false, authorized=true and an
    explicit authorized chat id (config or environment). The preferred transport
    is hermes send, which reuses Hermes' existing platform credentials; no token
    is copied into this project. A direct Bot API transport remains available
    for explicit configuration.
    """

    def __init__(self, policy: Dict[str, Any], redactor: Redactor, root: Path):
        self.policy = policy
        self.config = policy.get("telegram", {})
        self.redactor = redactor
        self.root = Path(root)
        self.log_path = self.root / "logs" / "telegram-dryrun.log"

    def _chat_id(self) -> Optional[str]:
        value = self.config.get("allowed_chat_id")
        if value:
            return str(value)
        env_name = self.config.get("allowed_chat_id_env")
        return os.environ.get(str(env_name)) if env_name else None

    def build_message(self, kind: str, task: Task, extra: Dict[str, Any]) -> str:
        header = {
            "task_created": "TASK CREATED",
            "task_succeeded": "TASK SUCCEEDED",
            "task_blocked": "TASK BLOCKED",
            "task_failed": "TASK FAILED",
            "expert_analysis": "EXPERT ANALYSIS",
            "expert_takeover": "EXPERT TAKEOVER",
            "approval_required": "APPROVAL REQUIRED",
            "budget_exceeded": "BUDGET LIMIT",
        }.get(kind, kind.upper())
        summary = (task.summary or "").replace("\n", " ")[:300]
        lines = [
            header,
            "task: " + task.task_id,
            "status: " + task.status,
            "phase: " + str(extra.get("phase") or task.current_phase or "-"),
            "summary: " + summary,
        ]
        if task.hermes_task_id:
            lines.append("hermes_card: " + task.hermes_task_id)
        if extra.get("reason"):
            lines.append("reason: " + str(extra["reason"])[:500])
        if extra.get("next"):
            lines.append("next: " + str(extra["next"])[:300])
        lines.append("budget: calls=%d est=$%.2f" % (task.model_calls, task.estimated_cost_usd))
        return self.redactor.text("\n".join(lines))[:4000]

    def _send_hermes(self, chat_id: str, message: str) -> Dict[str, Any]:
        cli = self.policy.get("hermes", {}).get("cli")
        if not cli or not Path(cli).exists():
            return {"sent": False, "reason": "hermes_cli_missing"}
        result = run_capture(
            [str(cli), "send", "--to", "telegram:" + chat_id, "--json", "--file", "-"],
            cwd=Path(cli).parent,
            timeout_seconds=60,
            env=public_env(),
            stdin_text=message,
        )
        if result.exit_code != 0:
            return {"sent": False, "reason": "hermes_send_failed",
                    "detail": self.redactor.text(result.stderr[-500:])}
        try:
            data = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            data = {"ok": True}
        return {"sent": bool(data.get("ok", True)),
                "reason": "hermes_send" if data.get("ok", True) else "hermes_send_rejected"}

    def _send_bot_api(self, chat_id: str, message: str) -> Dict[str, Any]:
        token = os.environ.get(str(self.config.get("token_env", "TELEGRAM_BOT_TOKEN")))
        if not token:
            token = secret_env(
                {"secrets": {"env_file": self.config.get("env_file")}},
                {"TELEGRAM_BOT_TOKEN"},
            ).get("TELEGRAM_BOT_TOKEN")
        if not token:
            return {"sent": False, "reason": "missing_bot_token"}
        payload = urllib.parse.urlencode({
            "chat_id": str(chat_id), "text": message, "disable_web_page_preview": "true",
        }).encode()
        url = "https://api.telegram.org/bot" + token + "/sendMessage"
        try:
            with urllib.request.urlopen(url, data=payload, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8", errors="replace"))
            return {"sent": bool(body.get("ok")), "reason": "bot_api" if body.get("ok") else str(body.get("description"))}
        except Exception as exc:
            return {"sent": False, "reason": type(exc).__name__}

    def notify(self, kind: str, task: Task, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        extra = extra or {}
        message = self.build_message(kind, task, extra)
        record = {"at": now_iso(), "kind": kind, "task_id": task.task_id, "message": message,
                  "enabled": bool(self.config.get("enabled")), "dry_run": bool(self.config.get("dry_run", True))}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if not self.config.get("enabled") or self.config.get("dry_run", True):
            return {"sent": False, "reason": "dry_run", "message": message}
        if not self.config.get("authorized"):
            return {"sent": False, "reason": "target_not_authorized", "message": message}
        chat_id = self._chat_id()
        if not chat_id:
            return {"sent": False, "reason": "no_authorized_chat_id", "message": message}
        if str(self.config.get("transport", "hermes_send")) == "hermes_send":
            result = self._send_hermes(chat_id, message)
        else:
            result = self._send_bot_api(chat_id, message)
        result["message"] = message
        return result

    def check_targets(self) -> Dict[str, Any]:
        cli = self.policy.get("hermes", {}).get("cli")
        if not cli or not Path(cli).exists():
            return {"ok": False, "reason": "hermes_cli_missing"}
        result = run_capture([str(cli), "send", "--list", "telegram"], cwd=Path(cli).parent,
                             timeout_seconds=30, env=public_env())
        return {
            "ok": result.exit_code == 0,
            "exit_code": result.exit_code,
            "output": self.redactor.text((result.stdout + result.stderr)[-1500:]),
        }
