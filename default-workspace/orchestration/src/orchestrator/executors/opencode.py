from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from ..classify import classify
from ..config import public_env
from ..proc import run_capture
from .base import ExecOutcome, ExecRequest, Executor, ExecutorUnavailable


class OpenCodeExecutor(Executor):
    name = "opencode"
    counts_as_model_call = True

    def __init__(self, config: Dict[str, Any], base_env: Dict[str, str] | None = None):
        self.config = config
        self.base_env = dict(base_env if base_env is not None else public_env())
        self.cli = Path(str(config.get("cli", "")))
        self.model = str(config.get("model", "deepseek/deepseek-chat"))

    def preflight(self) -> Dict[str, Any]:
        if not self.cli.exists():
            return {"ok": False, "detail": "OpenCode CLI not found: " + str(self.cli)}
        return {"ok": True, "detail": "CLI exists (execution itself not exercised by preflight)"}

    def _env(self) -> Dict[str, str]:
        env = dict(self.base_env)
        home = str(self.config.get("home", "inherit"))
        if home.lower() != "inherit":
            home_path = Path(home)
            home_path.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(home_path)
            env["USERPROFILE"] = str(home_path)
            env["XDG_DATA_HOME"] = str(home_path / "data")
            env["XDG_CONFIG_HOME"] = str(home_path / "config")
            env["XDG_CACHE_HOME"] = str(home_path / "cache")
        env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
        return env

    def build_argv(self, prompt: str) -> List[str]:
        extra = [str(item) for item in self.config.get("extra_args", [])]
        return [str(self.cli), "run", *extra, "--model", self.model, prompt]

    def run(self, request: ExecRequest) -> ExecOutcome:
        if not self.cli.exists():
            raise ExecutorUnavailable("OpenCode CLI not found: " + str(self.cli))
        argv = self.build_argv(request.prompt)
        result = run_capture(
            argv,
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
            env=self._env(),
            stdout_path=request.log_dir / ("attempt-%d-opencode.stdout.log" % request.attempt_no),
            stderr_path=request.log_dir / ("attempt-%d-opencode.stderr.log" % request.attempt_no),
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
            provider="opencode",
            model=self.model,
            error_category=category,
            transport="subprocess",
            model_call_started=True,
            notes={"argv_head": argv[:-1], "cwd": str(request.cwd)},
        )
