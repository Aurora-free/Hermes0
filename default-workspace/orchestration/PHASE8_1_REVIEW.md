# Phase 8.1 Secret Isolation Review

Date: 2026-10-24 (Asia/Singapore)
Reviewed copy: C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-copy\orchestration
No files were modified during this review. No model calls or network requests.

## 1. Spawn-site inventory

All child-process creation in the copy:

| Location | Purpose | Env passed |
|---|---|---|
| proc.py:64 Popen | generic subprocess capture | env parameter; default None |
| proc.py:34 subprocess.run | taskkill timeout cleanup | none (inherits parent) |
| runner.py:368 run_capture | acceptance commands | self.public_env |
| executors/opencode.py:51 run_capture | OpenCode | executor._env() from public_env |
| executors/expert.py:116 run_capture | DeepSeek | executor._env() from expert_env |
| hermes_bridge.py:39 run_capture | Hermes CLI | public_env() |
| telegram.py:72 run_capture | Hermes send | public_env() |
| telegram.py:135 run_capture | Hermes target list | public_env() |

No direct os.environ.copy() remains in the executor paths.

Status: NEEDS REVIEW.

- proc.py:34 taskkill inherits the full parent environment because no env is passed.
  taskkill is a trusted system binary, but this is still a child process outside the
  new policy.
- proc.py:51 run_capture has env=None as its default. All current call sites pass an
  env, but a future caller could silently inherit the full environment.
- Minimal fix: use an absolute taskkill path with a minimal env, and make env
  required or default to public_env in run_capture.

## 2. Per-consumer environment policy

### OpenCode

runner.py:326 passes self.public_env; opencode.py:20 defaults to public_env.
opencode.py:55 passes _env() to run_capture.

Status: PASS for known DeepSeek/Telegram secrets.

### DeepSeek

runner.py:91-93 builds expert_env with only DEEPSEEK_API_KEY and
DEEPSEEK_BASE_URL. runner.py:328 and runner.py:533 pass it to
DshHeadlessExpert. expert.py:88 defaults to public_env if no base_env is supplied;
expert.py:120 passes _env() to run_capture.

Status: PASS. DeepSeek gets its key but not the Telegram token.

### Acceptance

runner.py:352 uses dict(self.public_env).

Status: PASS for known secrets.

### Hermes CLI

hermes_bridge.py:43 uses public_env().

Status: PASS for secret separation.
Compatibility caveat: the CLI now depends on its own .env/config for platform
credentials. A real hermes send has not been exercised.

### Telegram helper

telegram.py:76 and telegram.py:135 use public_env().
telegram.py:90-95 reads only TELEGRAM_BOT_TOKEN for the Bot API fallback.

Status: PASS for child-process separation. The reporter still needs its own token.

## 3. public_env / secret_env / executor_env

Lines: config.py:12 SECRET_ENV_NAMES; config.py:32 _is_secret_name;
config.py:170 public_env; config.py:180 secret_env; config.py:208 executor_env.

### What works

- public_env removes exact names and names with the four secret suffixes.
- secret_env loads only the requested names, from process env or the env file.
- executor_env combines public env with only the requested secrets.
- No unrequested secret is inserted by these functions.
- No value is logged or returned in error messages.

### Findings

Status: NEEDS REVIEW.

1. public_env is a denylist, not an allowlist. Any unknown secret name not matching
   the explicit set or the four suffixes is still inherited. Examples that would pass:
   MY_CREDENTIAL, GITHUB_PAT, DATABASE_URL (which may embed a password),
   SOME_AUTH_VALUE.
2. _is_secret_name is case-insensitive, which is good. extra_secret_names in
   public_env is matched exactly. A custom token_env without a known suffix and with
   different casing could escape removal; the default TELEGRAM_BOT_TOKEN is caught.
3. secret_env uses exact names. Windows env lookups are case-insensitive, but the
   .env parser and requested-name set are case-sensitive. Current config uses
   uppercase names, so this is a compatibility edge case, not a current failure.
4. executor_env applies secrets after public_env; a requested secret wins, which is
   intended.

Minimal fix: use an explicit public allowlist for executor environments (PATH,
SystemRoot, TEMP, HOME, APPDATA, LOCALAPPDATA, etc.) or add a configurable strict
secret-name policy plus tests for unknown names such as GITHUB_PAT and
UNKNOWN_CREDENTIAL.

## 4. Suffix filtering

Lines: config.py:32-34.

Status: NEEDS REVIEW.

- It catches MY_SECRET, MY_TOKEN, MY_API_KEY, MY_PASSWORD.
- It does not catch MY_KEY, GITHUB_PAT, CREDENTIAL_VALUE, or a URL containing a
  password.
- It can remove non-secret configuration whose name happens to end in one of the four
  suffixes. Current project config does not use such names, but external tools might.
- This is a deliberate compatibility tradeoff; document it or use an allowlist.

## 5. Secret exposure in logs, argv, tests, errors

Status: PASS with one NEEDS REVIEW.

PASS:

- Executor command lines contain prompts/tasks, not env values.
- run_capture writes stdout/stderr, not env.
- No code prints env dictionaries.
- Tests print only test names and assertion messages with variable names, not values.
- Fake values are used everywhere in the copy.

NEEDS REVIEW:

- redact.py:7 has a narrower SECRET_ENV_NAMES than config.py:12. The config set adds
  GOOGLE_API_KEY, OPENROUTER_API_KEY, AGNES_API_KEY, XAI_API_KEY, MISTRAL_API_KEY,
  GROQ_API_KEY, AZURE_OPENAI_API_KEY, and AWS keys. If those values appear in logs,
  Redactor will not replace them by exact-value matching (some may match regexes).
- Minimal fix: import the shared set into redact.py, or expand it to match config.py.

## 6. Do the tests verify an actual child environment?

Status: NEEDS REVIEW.

The tests at tests/test_env_isolation.py lines 85, 101, 116, 127, and 136 patch
run_capture and inspect its env kwarg. This verifies the env passed to the
subprocess-launch boundary, not a child process that actually reads os.environ.

This is stronger than testing an internal helper return value, but it would not catch
a future regression where run_capture or Popen ignores the env argument.

Minimal fix: spawn a tiny Python child through run_capture with public_env and a
script that prints only booleans such as
DEEPSEEK_API_KEY in os.environ, TELEGRAM_BOT_TOKEN in os.environ,
PUBLIC_TEST_VAR in os.environ. Assert no fake secret value is printed. This uses no
real credentials.

## 7. Test fixture and policy paths

Status: FAIL for merge portability, PASS for the copy itself.

Copy behavior is correct:

- config/policy.json:3 runtime_root points to the copy runtime.
- config/policy.json:53 telegram.env_file points to copy tests/fake.env.
- config/policy.json secrets.env_file points to copy tests/fake.env.
- tests/fake.env contains only fake values.
- tests/test_env_isolation.py resolves ROOT to the copy and imports copy/src.

Merge blocker:

- tests/test_env_isolation.py:33 calls load_policy() without overriding
  secrets.env_file or telegram.env_file. When copied into production, it will load
  the production policy and therefore the production .env, not tests/fake.env.
- The test would then either read real values or fail looking for
  FAKE_FILE_ONLY_SECRET.
- Minimal fix: after load_policy(), set
  policy["secrets"]["env_file"] = str(ROOT / "tests" / "fake.env"),
  policy["telegram"]["env_file"] = the same path, and use a temporary runtime root.
  Do not merge the copy config/policy.json.

## 8. PYTHONDONTWRITEBYTECODE=1

Status: NEEDS REVIEW, not a credential fix.

- The first verify.cmd run was 12/13, with
  test_expert_analysis_then_takeover failing because the acceptance imported a stale
  same-size calc.py bytecode file after the expert rewrote it.
- With PYTHONDONTWRITEBYTECODE=1, 13/13 passed.
- This is a pre-existing test flake, not an env-isolation change. It should not be
  counted as part of the credential fix.
- Minimal fix: add -B to the test's acceptance commands or clear __pycache__ between
  expert rewrites; rerun without relying on an external env variable.

## 9. Diff scope and env_with_secrets compatibility

Status: PASS for diff scope; NEEDS REVIEW for API compatibility.

- phase8_changes.diff contains only six env-related source files; no policy, runtime,
  or unrelated code changes.
- env_with_secrets has no remaining internal callers (grep is empty).
- External scripts/plugins importing env_with_secrets would break. It is not a
  documented public API, but should be called out in release notes.
- Unused imports (os in opencode/expert/hermes_bridge) were left in place; not
  functional.

## 10. Summary table

| Check | Status | Main reference |
|---|---|---|
| All spawn sites inventoried | NEEDS REVIEW | proc.py:34,51; all run_capture |
| OpenCode policy | PASS | runner.py:326; opencode.py:20 |
| DeepSeek policy | PASS | runner.py:91,328,533; expert.py:88 |
| Acceptance policy | PASS | runner.py:352 |
| Hermes CLI policy | PASS | hermes_bridge.py:43 |
| Telegram helper policy | PASS | telegram.py:76,135 |
| public/secret split | NEEDS REVIEW | config.py:170-211 |
| Unknown-secret leakage | NEEDS REVIEW | denylist, suffix filter |
| Case/custom env names | NEEDS REVIEW | config.py:170-206 |
| Suffix false positives/negatives | NEEDS REVIEW | config.py:32-34 |
| Logs/argv/errors | PASS / NEEDS REVIEW | redact.py:7 narrower |
| Actual child env test | NEEDS REVIEW | tests lines 85-136 |
| Test fixture portability | FAIL if merged as-is | test line 33; copy policy |
| PYTHONDONTWRITEBYTECODE | NEEDS REVIEW | pre-existing pyc flake |
| Diff unrelated changes | PASS | 249-line diff, six files |
| env_with_secrets removal | NEEDS REVIEW | external API compatibility |
| Real keys touched | NO | fake env only |
| Model calls | NO | none |

## 11. Recommendation

Do not merge this exact patch as-is.

The known DeepSeek/Telegram exposure is fixed in the copy, but three issues block a
safe merge:

1. The new test is not self-contained and would read the production .env when merged.
2. public_env remains a denylist; unknown credential names can still be inherited.
3. Two subprocess paths still inherit by default, and no test spawns an actual child
   to verify env.

Minimal merge set after fixes:

- src/orchestrator/config.py
- src/orchestrator/runner.py
- src/orchestrator/telegram.py
- src/orchestrator/hermes_bridge.py
- src/orchestrator/executors/opencode.py
- src/orchestrator/executors/expert.py
- tests/test_env_isolation.py (after making the fake env override explicit)
- tests/fake.env

Do not merge:

- phase8-copy/orchestration/config/policy.json (copy-specific runtime/fake paths)
- phase8_changes.diff
- original/
- runtime/ or test-runs/

Post-merge verification:

    python -m unittest tests.test_env_isolation -v
    $env:PYTHONDONTWRITEBYTECODE='1'
    cmd /c verify.cmd
    python -m compileall -q src run.py

Recommended minimal corrections before merge:

1. Override secrets.env_file and telegram.env_file to tests/fake.env inside the test;
   use a temporary runtime root.
2. Replace the unknown-secret denylist with a strict public allowlist, or add explicit
   tests for GITHUB_PAT / UNKNOWN_CREDENTIAL and document the residual risk.
3. Pass a minimal env to taskkill and make run_capture env required/default-public.
4. Add a real child-process probe that prints only booleans, not values.
5. Use the shared secret-name set in redact.py, or expand it.
6. Add -B/clear bytecode in the flaky expert test; do not count it as a credential fix.
