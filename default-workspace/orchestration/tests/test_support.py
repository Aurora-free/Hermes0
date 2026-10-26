import copy
import os
from pathlib import Path

from orchestrator.config import DEFAULTS

ROOT = Path(__file__).resolve().parents[1]
FAKE_ENV = ROOT / "tests" / "fake.env"


def test_policy(runtime_root: Path):
    # Keep the test process on fake values even if the host has real env vars.
    os.environ["DEEPSEEK_API_KEY"] = "FAKE_DEEPSEEK_KEY_FOR_TESTS"
    os.environ["TELEGRAM_BOT_TOKEN"] = "FAKE_TELEGRAM_TOKEN_FOR_TESTS"
    # Prevent the known same-size .pyc flake in acceptance subprocesses.
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    policy = copy.deepcopy(DEFAULTS)
    policy["runtime_root"] = str(Path(runtime_root))
    policy["secrets"]["env_file"] = str(FAKE_ENV)
    policy["telegram"]["env_file"] = str(FAKE_ENV)
    return policy
