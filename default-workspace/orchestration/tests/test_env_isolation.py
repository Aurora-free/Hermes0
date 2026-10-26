import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import executor_env, public_env, secret_env
from orchestrator.executors.expert import DshHeadlessExpert
from orchestrator.executors.opencode import OpenCodeExecutor
from orchestrator.proc import run_capture
from orchestrator.redact import Redactor
from orchestrator.runner import Orchestrator

try:
    from tests.test_support import FAKE_ENV, test_policy
except ImportError:
    from test_support import FAKE_ENV, test_policy

FAKE_DEEPSEEK = "FAKE_DEEPSEEK_KEY_FOR_TESTS"
FAKE_TELEGRAM = "FAKE_TELEGRAM_TOKEN_FOR_TESTS"
FAKE_FILE_ONLY = "FAKE_FILE_ONLY_VALUE"
FAKE_PUBLIC = "public-value-must-survive"
FAKE_AWS = "FAKE_AWS_SECRET_VALUE"
FAKE_GOOGLE = "FAKE_GOOGLE_KEY_VALUE"
FAKE_GITHUB = "FAKE_GITHUB_PAT_VALUE"
FAKE_DATABASE = "postgres://fakeuser:FAKE_DATABASE_PASSWORD@localhost/fake"

SECRET_NAMES = (
    "DEEPSEEK_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_PAT",
    "UNKNOWN_CREDENTIAL",
    "MY_CREDENTIAL",
    "DATABASE_URL",
)
PROBE = (
    "import os\n"
    "names = ('DEEPSEEK_API_KEY','TELEGRAM_BOT_TOKEN','GITHUB_PAT',"
    "'UNKNOWN_CREDENTIAL','MY_CREDENTIAL','DATABASE_URL','PATH','TEMP')\n"
    "for name in names:\n"
    "    print('%s=%s' % (name, name in os.environ))\n"
)


def probe_env(env):
    result = run_capture(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        timeout_seconds=20,
        env=env,
    )
    if result.exit_code != 0:
        raise AssertionError("child probe failed with exit code %r" % result.exit_code)
    values = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value.strip() == "True"
    return values


class EnvIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["DEEPSEEK_API_KEY"] = FAKE_DEEPSEEK
        os.environ["TELEGRAM_BOT_TOKEN"] = FAKE_TELEGRAM
        os.environ["PUBLIC_TEST_VAR"] = FAKE_PUBLIC
        os.environ["GITHUB_PAT"] = FAKE_GITHUB
        os.environ["UNKNOWN_CREDENTIAL"] = "FAKE_UNKNOWN_VALUE"
        os.environ["MY_CREDENTIAL"] = "FAKE_MY_CREDENTIAL_VALUE"
        os.environ["DATABASE_URL"] = FAKE_DATABASE
        os.environ["Phase8_Case_Key"] = "FAKE_CASE_VALUE"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phase8-env-")
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / "runtime"
        self.policy = test_policy(self.runtime)
        self.orch = Orchestrator(self.policy, self.runtime)
        self.addCleanup(self.orch.close)

    def test_policy_paths_are_test_local(self):
        fake = Path(self.policy["secrets"]["env_file"]).resolve()
        telegram = Path(self.policy["telegram"]["env_file"]).resolve()
        runtime = Path(self.policy["runtime_root"]).resolve()
        self.assertEqual(fake, FAKE_ENV.resolve())
        self.assertEqual(telegram, fake)
        self.assertEqual(fake.parent, (ROOT / "tests").resolve())
        self.assertEqual(runtime, self.runtime.resolve())
        self.assertIn(str(self.temp.name), str(runtime))

    def test_public_env_is_strict_allowlist(self):
        env = public_env()
        for name in SECRET_NAMES:
            self.assertNotIn(name, env)
        self.assertNotIn("PUBLIC_TEST_VAR", env)
        self.assertIn("PATH", env)
        self.assertIn("TEMP", env)
        extra = public_env(extra_public_names={"PUBLIC_TEST_VAR"})
        self.assertEqual(extra.get("PUBLIC_TEST_VAR"), FAKE_PUBLIC)

    def test_unknown_credentials_and_database_url_do_not_leak(self):
        env = executor_env(self.policy, {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"})
        for name in ("GITHUB_PAT", "UNKNOWN_CREDENTIAL", "MY_CREDENTIAL", "DATABASE_URL"):
            self.assertNotIn(name, env)
        self.assertEqual(env.get("DEEPSEEK_API_KEY"), FAKE_DEEPSEEK)

    def test_secret_env_requests_only_allowlisted_names(self):
        env = secret_env(self.policy, {"FAKE_FILE_ONLY_SECRET"})
        self.assertEqual(env.get("FAKE_FILE_ONLY_SECRET"), FAKE_FILE_ONLY)
        self.assertNotIn("DEEPSEEK_API_KEY", env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertNotIn("PUBLIC_TEST_VAR", env)

    def test_secret_name_case_is_handled(self):
        env = secret_env(self.policy, {"phase8_case_key"})
        self.assertEqual(env.get("phase8_case_key"), "FAKE_CASE_VALUE")
        self.assertEqual(secret_env(self.policy, {"DOES_NOT_EXIST"}), {})

    def test_opencode_env_has_no_secrets(self):
        executor = OpenCodeExecutor(self.policy["opencode"], self.orch.public_env)
        values = probe_env(executor._env())
        for name in SECRET_NAMES:
            self.assertFalse(values[name], name)
        self.assertTrue(values["PATH"])

    def test_deepseek_env_has_only_deepseek_secret(self):
        executor = DshHeadlessExpert(self.policy["expert"], self.orch.expert_env)
        values = probe_env(executor._env())
        self.assertTrue(values["DEEPSEEK_API_KEY"])
        for name in ("TELEGRAM_BOT_TOKEN", "GITHUB_PAT", "UNKNOWN_CREDENTIAL",
                     "MY_CREDENTIAL", "DATABASE_URL"):
            self.assertFalse(values[name], name)
        self.assertTrue(values["PATH"])

    def test_acceptance_child_has_no_secrets(self):
        task = SimpleNamespace(
            task_id="T-ENV-ACCEPT",
            acceptance_commands=[[sys.executable, "-c", PROBE]],
            timeout_seconds=20,
            working_directory=str(ROOT),
        )
        results = self.orch._run_acceptance(task, 1)
        self.assertEqual(results[0]["exit_code"], 0)
        output = results[0]["stdout_tail"]
        for name in SECRET_NAMES:
            self.assertIn(name + "=False", output)
        self.assertIn("PATH=True", output)

    def test_hermes_and_telegram_helpers_use_public_env(self):
        from orchestrator.hermes_bridge import HermesKanban
        fake = type("R", (), {})()
        fake.exit_code = 0
        fake.stdout = "[]"
        fake.stderr = ""
        kanban = HermesKanban(self.policy["hermes"]["cli"], timeout_seconds=1,
                              redactor=self.orch.redactor)
        with patch("orchestrator.hermes_bridge.run_capture", return_value=fake) as mocked:
            kanban._run(["kanban", "list", "--json"])
            env = mocked.call_args.kwargs["env"]
        for name in SECRET_NAMES:
            self.assertNotIn(name, env)

    def test_taskkill_terminates_child_with_minimal_env(self):
        result = run_capture(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=ROOT,
            timeout_seconds=1,
            env=public_env(),
        )
        self.assertTrue(result.timed_out)

    def test_log_redaction_covers_added_secret_types(self):
        redactor = Redactor(str(FAKE_ENV))
        text = "aws=%s google=%s github=%s db=%s" % (
            FAKE_AWS, FAKE_GOOGLE, FAKE_GITHUB, FAKE_DATABASE,
        )
        output = redactor.text(text)
        self.assertNotIn(FAKE_AWS, output)
        self.assertNotIn(FAKE_GOOGLE, output)
        self.assertNotIn(FAKE_GITHUB, output)
        self.assertNotIn(FAKE_DATABASE, output)


if __name__ == "__main__":
    unittest.main()
