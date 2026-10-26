from __future__ import annotations

import ctypes
import json
import msvcrt
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional

from .models import now_iso


class LockBusy(RuntimeError):
    def __init__(self, owner: Dict[str, Any]):
        self.owner = owner
        super().__init__("worktree lock is held by " + json.dumps(owner, ensure_ascii=False))


def pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class WorktreeLock:
    """Exclusive cross-process lock for one writing worktree.

    The lock is a one-byte msvcrt lock on a file under orchestration/locks.
    The OS releases it if the process dies, so a stale owner file is only
    advisory and is validated against the recorded PID.
    """

    def __init__(self, path: Path, task_id: str, purpose: str = "write"):
        self.path = Path(path)
        self.task_id = task_id
        self.purpose = purpose
        self._handle = None
        self._held = False

    def owner(self) -> Dict[str, Any]:
        try:
            data = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            value = json.loads(data)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                owner = self.owner()
                handle.close()
                raise LockBusy(owner)
            payload = {
                "task_id": self.task_id,
                "pid": os.getpid(),
                "purpose": self.purpose,
                "acquired_at": now_iso(),
            }
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
            self._handle = handle
            self._held = True
        except Exception:
            if not self._held:
                try:
                    handle.close()
                except Exception:
                    pass
            raise

    def release(self) -> None:
        if not self._held or self._handle is None:
            return
        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            try:
                self._handle.seek(0)
                self._handle.truncate()
                self._handle.flush()
            except OSError:
                pass
            self._handle.close()
            self._handle = None
            self._held = False

    def __enter__(self) -> "WorktreeLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    @property
    def held(self) -> bool:
        return self._held


def stale_lock_info(path: Path) -> Optional[Dict[str, Any]]:
    try:
        owner = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(owner, dict) and not pid_alive(int(owner.get("pid") or 0)):
        return owner
    return None
