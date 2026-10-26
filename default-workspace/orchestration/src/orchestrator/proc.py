from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence


@dataclass
class ProcResult:
    argv: List[str]
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("mbcs", errors="replace")


def _kill_tree(pid: int) -> None:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    taskkill = Path(system_root) / "System32" / "taskkill.exe"
    minimal_env = {
        "SystemRoot": system_root,
        "WINDIR": system_root,
        "PATH": str(Path(system_root) / "System32"),
    }
    try:
        if taskkill.exists():
            subprocess.run(
                [str(taskkill), "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=20,
                env=minimal_env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, 9)
    except Exception:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def run_capture(
    argv: Sequence[str],
    cwd: Path,
    timeout_seconds: int,
    env: Dict[str, str],
    stdout_path: Optional[Path] = None,
    stderr_path: Optional[Path] = None,
    max_output_bytes: int = 2_000_000,
    stdin_text: Optional[str] = None,
) -> ProcResult:
    argv_list = [str(item) for item in argv]
    if not argv_list:
        raise ValueError("argv must not be empty")
    if env is None:
        raise ValueError("env is required; implicit environment inheritance is disabled")
    started = time.monotonic()
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        argv_list,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    timed_out = False
    try:
        stdout_raw, stderr_raw = process.communicate(
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process.pid)
        try:
            stdout_raw, stderr_raw = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_raw, stderr_raw = process.communicate()
    duration = time.monotonic() - started
    stdout = _decode(stdout_raw or b"")
    stderr = _decode(stderr_raw or b"")
    if len(stdout.encode("utf-8", errors="replace")) > max_output_bytes:
        stdout = stdout[: max_output_bytes // 2] + "\n...<truncated>...\n"
    if len(stderr.encode("utf-8", errors="replace")) > max_output_bytes:
        stderr = stderr[: max_output_bytes // 2] + "\n...<truncated>...\n"
    if stdout_path is not None:
        Path(stdout_path).parent.mkdir(parents=True, exist_ok=True)
        Path(stdout_path).write_text(stdout, encoding="utf-8", errors="replace")
    if stderr_path is not None:
        Path(stderr_path).parent.mkdir(parents=True, exist_ok=True)
        Path(stderr_path).write_text(stderr, encoding="utf-8", errors="replace")
    return ProcResult(
        argv=argv_list,
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_seconds=round(duration, 3),
        stdout_path=str(stdout_path) if stdout_path else None,
        stderr_path=str(stderr_path) if stderr_path else None,
    )
