from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..classify import classify
from ..config import public_env
from ..proc import run_capture
from .base import ExecOutcome, ExecRequest, Executor, ExecutorUnavailable


ANALYSIS_INSTRUCTION = """You are the DeepSeek diagnostic expert in a local orchestration system.
Analyse the failed task below and return ONE JSON object and nothing else.
Schema:
{
  "root_cause": "short technical explanation",
  "confidence": "low|medium|high",
  "plan": ["concrete step", "..."],
  "files_to_change": ["relative/path"],
  "verification": ["argv array, e.g. [\\"python\\",\\"-m\\",\\"pytest\\",\\"-q\\"]"],
  "risks": ["..."]
}
Do not claim you changed files. You are analysing only; a local executor will apply the plan.
Never include credentials, tokens, or environment values in the output.
"""

TAKEOVER_INSTRUCTION = """You are the DeepSeek expert taking over a local worktree.
You have local file and terminal tools. Work ONLY inside the working directory and only
inside the allowed paths listed below. Do not touch credentials, do not install software,
do not use the network for package installs.
Fix the failing task, run the acceptance commands, and then print a final summary with:
1. files changed, 2. commands run and exit codes, 3. whether acceptance passed.
If you cannot fix it safely, stop and explain why instead of guessing.
"""


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return "...<truncated>...\n" + text[-limit:]


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    candidates = re.findall(r"\{.*?\}", text, flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict) and ("plan" in value or "root_cause" in value):
                return value
        except json.JSONDecodeError:
            continue
    return None


def _events_text(stdout: str) -> str:
    messages: List[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            for key in ("text", "message", "content"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    messages.append(value)
    return "\n".join(messages) if messages else stdout


class DshHeadlessExpert(Executor):
    """Expert tier backed by the DeepSeek Harness headless agent.

    This is a real local agent process (file + terminal tools) when the host
    allows it to launch. It is not a plain chat API call.
    """

    name = "deepseek_expert"
    counts_as_model_call = True

    def __init__(self, config: Dict[str, Any], base_env: Dict[str, str] | None = None):
        self.config = config
        self.base_env = dict(base_env if base_env is not None else public_env())
        self.cli = Path(str(config.get("dsh_cli", "")))
        self.dsh_home = str(config.get("dsh_home", ""))

    def preflight(self) -> Dict[str, Any]:
        if not self.cli.exists():
            return {"ok": False, "detail": "dsh CLI not found: " + str(self.cli)}
        return {
            "ok": True,
            "detail": "dsh headless CLI exists; model credentials live in DSH_HOME and are not read by the orchestrator",
            "dsh_home": self.dsh_home,
        }

    def _env(self) -> Dict[str, str]:
        env = dict(self.base_env)
        if self.dsh_home:
            env["DSH_HOME"] = self.dsh_home
        base_url = self.config.get("base_url")
        if base_url:
            env["DEEPSEEK_BASE_URL"] = str(base_url)
        return env

    def _invoke(self, request: ExecRequest, prompt: str, tag: str) -> ExecOutcome:
        if not self.cli.exists():
            raise ExecutorUnavailable("dsh CLI not found: " + str(self.cli))
        # Feed the prompt through stdin: dsh.cmd is a batch launcher and
        # multi-line argv would be truncated by cmd.exe.
        argv = [str(self.cli), "--profile", "headless", "--json", "-"]
        result = run_capture(
            argv,
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
            env=self._env(),
            stdout_path=request.log_dir / ("attempt-%d-%s.stdout.log" % (request.attempt_no, tag)),
            stderr_path=request.log_dir / ("attempt-%d-%s.stderr.log" % (request.attempt_no, tag)),
            stdin_text=prompt,
        )
        category = None
        if result.exit_code != 0 or result.timed_out:
            category = classify(result.exit_code, result.stdout, result.stderr, result.timed_out)
        return ExecOutcome(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
            provider="deepseek_expert",
            model="dsh-headless",
            error_category=category,
            transport="subprocess",
            model_call_started=True,
            notes={"cwd": str(request.cwd), "phase": request.phase},
        )

    def analyze(self, request: ExecRequest) -> ExecOutcome:
        outcome = self._invoke(request, ANALYSIS_INSTRUCTION + "\n\n" + request.prompt, "expert-analysis")
        outcome.notes["parsed"] = _extract_json(_events_text(outcome.stdout))
        return outcome

    def takeover(self, request: ExecRequest) -> ExecOutcome:
        return self._invoke(request, TAKEOVER_INSTRUCTION + "\n\n" + request.prompt, "expert-takeover")
