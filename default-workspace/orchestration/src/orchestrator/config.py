from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable

ORCH_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ORCH_ROOT / "src"

# Names that must never be inherited by an executor or acceptance command.
SECRET_ENV_NAMES = {
    "DEEPSEEK_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "AGNES_API_KEY",
    "HERMES_API_KEY",
    "XAI_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_PAT",
    "DATABASE_URL",
}


PUBLIC_ENV_ALLOWLIST = {
    # Windows process/runtime basics
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432",
    "ALLUSERSPROFILE",
    "PUBLIC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "OS",
    "COMPUTERNAME",
    "USERNAME",
    "USERDOMAIN",
    "LOGONSERVER",
    "SESSIONNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    # Python and runtime configuration
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONNOUSERSITE",
    "PYTHONSAFEPATH",
    "PYTHONDONTWRITEBYTECODE",
    "VIRTUAL_ENV",
    "NODE_OPTIONS",
    "NPM_CONFIG_PREFIX",
    "PSModulePath",
    "OPENCODE_DISABLE_AUTOUPDATE",
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "runtime_root": r"C:\Users\22649\AppData\Local\HermesOrchestrator\runtime",
    "hermes": {
        "cli": r"C:\Users\22649\AppData\Local\hermes\bin\hermes.exe",
        "kanban_tenant": "orchestrator",
        "kanban_board": "default",
        "timeout_seconds": 120,
    },
    "limits": {
        "max_opencode_attempts": 2,
        "max_expert_analyses": 1,
        "max_expert_takeovers": 1,
        "max_total_attempts": 4,
        "max_model_calls_per_task": 6,
        "max_cost_usd_per_task": 0.50,
        "assumed_cost_per_call_usd": 0.02,
        "default_timeout_seconds": 300,
        "max_output_bytes": 2_000_000,
        "max_snapshot_files": 3000,
        "max_snapshot_bytes": 80_000_000,
    },
    "concurrency": {"max_writing_tasks": 1},
    "expert": {
        "enabled": True,
        "mode": "dsh_headless",
        "dsh_cli": r"C:\Users\22649\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd",
        "dsh_home": r"C:\Users\22649\.dsh",
        "timeout_seconds": 600,
    },
    "opencode": {
        "cli": r"C:\Users\22649\AppData\Roaming\ai.opencode.desktop\cli\2.1.3-x64\opencode-cli.exe",
        "model": "deepseek/deepseek-chat",
        "home": "inherit",
        "extra_args": ["--standalone", "--auto", "--format", "json"],
    },
    "telegram": {
        "enabled": False,
        "dry_run": True,
        "authorized": False,
        "transport": "hermes_send",
        "allowed_chat_id": None,
        "allowed_chat_id_env": "TELEGRAM_CHAT_ID",
        "token_env": "TELEGRAM_BOT_TOKEN",
        "env_file": r"C:\Users\22649\AppData\Local\hermes\.env",
    },
    "paths": {
        "exclude_dirs": [".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", "dist", "build"],
        "text_patch_suffixes": [".py", ".js", ".ts", ".json", ".md", ".txt", ".yaml", ".yml", ".ps1", ".cmd", ".html", ".css"],
    },
    "secrets": {
        "env_file": r"C:\Users\22649\AppData\Local\hermes\.env",
    },
}


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("policy file must contain a JSON object: " + str(path))
    return data


def load_policy(path: Path | None = None) -> Dict[str, Any]:
    policy_path = path or (ORCH_ROOT / "config" / "policy.json")
    merged = _deep_merge(DEFAULTS, _read_json(policy_path))
    env_path = merged.get("secrets", {}).get("env_file")
    if env_path and not Path(env_path).exists():
        raise FileNotFoundError("configured env_file not found: " + str(env_path))
    return merged


def runtime_root(policy: Dict[str, Any] | None = None) -> Path:
    value = (policy or {}).get("runtime_root") or DEFAULTS["runtime_root"]
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def ensure_layout(root: Path = ORCH_ROOT) -> None:
    for name in ("tasks", "results", "logs", "locks", "state", "config", "snapshots"):
        (root / name).mkdir(parents=True, exist_ok=True)


def db_path(root: Path = ORCH_ROOT) -> Path:
    return root / "state" / "orchestrator.db"


def task_dir(task_id: str, root: Path = ORCH_ROOT) -> Path:
    return root / "tasks" / task_id


def result_path(task_id: str, root: Path = ORCH_ROOT) -> Path:
    return root / "results" / (task_id + ".json")


def log_path(task_id: str, name: str, root: Path = ORCH_ROOT) -> Path:
    directory = root / "logs" / task_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def resolve_under(base: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def is_onedrive_path(path: Path) -> bool:
    text = str(Path(path).resolve()).lower()
    return "onedrive" in text or "\\users\\22649\\onedrive" in text


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def public_env(extra_public_names: Iterable[str] = ()) -> dict[str, str]:
    """Return only explicitly allowlisted public variables.

    This is an allowlist, not a denylist: unknown credential names and
    variables ending in _KEY, _TOKEN, _SECRET, or _PASSWORD are never
    inherited unless a caller explicitly adds the exact name.
    """
    allowed = {name.upper() for name in PUBLIC_ENV_ALLOWLIST}
    allowed.update(str(name).upper() for name in extra_public_names)
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper() in allowed
    }


def secret_env(policy: Dict[str, Any], names: Iterable[str]) -> dict[str, str]:
    """Load only the explicitly requested secrets.

    Requested names may come from the process environment (for injected
    credentials) or from the configured env file. Matching is case-insensitive
    and only requested names can enter the result.
    """
    wanted = {str(name).upper(): str(name) for name in names}
    out: dict[str, str] = {}
    for name, value in os.environ.items():
        key = name.upper()
        canonical = wanted.get(key)
        if canonical and canonical not in out and value:
            out[canonical] = value
    path = policy.get("secrets", {}).get("env_file")
    if not path or not Path(path).exists():
        return out
    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            canonical = wanted.get(name.strip().upper())
            if canonical and canonical not in out:
                out[canonical] = value.strip().strip('"').strip("'")
    return out


def executor_env(
    policy: Dict[str, Any],
    secret_names: Iterable[str],
    extra_public_names: Iterable[str] = (),
) -> dict[str, str]:
    """Strict public allowlist plus only the requested secrets."""
    env = public_env(extra_public_names)
    env.update(secret_env(policy, secret_names))
    return env
