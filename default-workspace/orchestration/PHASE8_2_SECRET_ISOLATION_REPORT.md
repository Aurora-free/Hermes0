# Phase 8.2 Secret Isolation Report

Date: 2026-10-24 (Asia/Singapore)
Work copy: C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-2-copy\orchestration
Original Phase 8 copy preserved: C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-copy\orchestration
Production source: NOT MODIFIED.

No real .env value was read, printed, copied, or uploaded. All tests used fake values.
No model was called. No production config, SQLite/WAL, ACL, firewall, service, or
Telegram setting was changed.

## 1. Three blockers fixed

### A. Test no longer reads production config

New tests/test_support.py:

- test_policy() starts from orchestrator.config.DEFAULTS.
- Overrides runtime_root with a caller-provided temporary runtime.
- Overrides secrets.env_file and telegram.env_file to tests/fake.env.
- Sets fake DEEPSEEK_API_KEY and TELEGRAM_BOT_TOKEN in the test process.
- Sets PYTHONDONTWRITEBYTECODE=1 for the test process.

tests/test_orchestration.py now gets policy from test_policy() instead of load_policy().
tests/test_env_isolation.py uses a TemporaryDirectory runtime and test_policy().
It asserts that the fake env path is under tests/ and the runtime path is under the
temporary directory.

No test uses production config/policy.json, production .env, or production runtime.

### B. Strict public allowlist

config.py now has PUBLIC_ENV_ALLOWLIST (line 34) containing only explicitly reviewed
public Windows/Python/runtime variables:

- PATH, PATHEXT, SYSTEMROOT, SYSTEMDRIVE, WINDIR, COMSPEC, TEMP, TMP
- user/home/appdata/program directories
- processor/OS/computer/user environment
- language/timezone
- PYTHONPATH, PYTHONHOME, PYTHONIOENCODING, PYTHONUTF8, PYTHONDONTWRITEBYTECODE
- VIRTUAL_ENV, NODE_OPTIONS, NPM_CONFIG_PREFIX, PSModulePath
- OPENCODE_DISABLE_AUTOUPDATE

public_env() (config.py:224) now returns only allowlisted names. It is no longer a
copy-all-minus-known-secrets denylist.

secret_env() (config.py:240) only reads requested names and matches case-insensitively.

executor_env() (config.py:269) combines the strict public env with only requested
secrets.

Per-consumer policy:

| Consumer | Env |
|---|---|
| OpenCode | public only |
| DeepSeek | public + DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL |
| Acceptance | public only |
| Hermes CLI | public only |
| Telegram helper | public only |
| Telegram reporter itself | may read TELEGRAM_BOT_TOKEN for its own send, not passed to children |

### C. No implicit subprocess inheritance

proc.py:

- _kill_tree (line 32) now uses an absolute System32\taskkill.exe and a minimal env
  containing only SystemRoot, WINDIR, and System32 PATH.
- run_capture (line 58) now requires env (line 62); there is no default None.
- run_capture rejects None at runtime.
- Popen receives that explicit env.

All run_capture call sites pass an explicit env:
hermes_bridge.py:43, runner.py:371, opencode.py:55, expert.py:120, telegram.py:76,
telegram.py:136, and test probes.

Remaining os.environ uses are non-spawn or trusted-system lookups:

- config.py:235/249: allowlist/secret reads.
- proc.py:33: SystemRoot/WINDIR for taskkill.
- telegram.py:38/90: chat id and Bot API token for the reporter itself.

No os.environ.copy() remains in src. No env_with_secrets call remains.

## 2. Unknown and case handling

Tests cover:

- GITHUB_PAT
- UNKNOWN_CREDENTIAL
- MY_CREDENTIAL
- DATABASE_URL
- lowercase / mixed-case requested secret name
- missing variable
- PUBLIC_TEST_VAR only via explicit extra_public_names allowlist

The strict allowlist excludes all unknown names from public env and from executor
env unless explicitly requested.

## 3. Actual child-process verification

The new tests no longer only inspect internal return values.

tests/test_env_isolation.py:50 probe_env() starts a real Python child through
run_capture and the child prints only variable-name=boolean pairs.

Actual child probes verify:

- OpenCode env: DeepSeek false, Telegram false, GITHUB_PAT false,
  UNKNOWN_CREDENTIAL false, MY_CREDENTIAL false, DATABASE_URL false, PATH true.
- DeepSeek env: DeepSeek true, Telegram false, other unknown false, PATH true.
- Acceptance env: all listed secrets false, PATH true.
- taskkill: run_capture times out a sleeping child and kills it via the new taskkill
  path; timed_out is true.

Hermes/Telegram helper env is checked at the run_capture boundary with public_env;
the actual hermes send was not executed.

## 4. Redaction extension

redact.py now imports SECRET_ENV_NAMES from config.py instead of maintaining a
narrower duplicate set. The shared set includes AWS, Google, GitHub, DATABASE_URL,
and provider keys.

Test test_log_redaction_covers_added_secret_types uses fake values and asserts none
appear in the redacted output.

## 5. Test and compile results

All commands ran in the Phase 8.2 copy.

### Env isolation tests

    python -m unittest tests.test_env_isolation -v

    Ran 11 tests
    OK
    exit 0

### Normal verify

    cmd /c verify.cmd

    PASS test_read_only_success
    ...
    PASS test_cancel_queued
    RESULT PASS failures= 0
    exit 0

Note: an earlier normal run failed 12/13 because the expert acceptance imported a
stale same-size calc.py .pyc after the takeover edited calc.py. This is the
pre-existing bytecode-cache flake. tests/test_support.py now sets
PYTHONDONTWRITEBYTECODE=1 for test-owned child processes, so normal verify passes
without an external env var.

### Verify with explicit bytecode suppression

    $env:PYTHONDONTWRITEBYTECODE='1'
    cmd /c verify.cmd

    13/13 PASS
    exit 0

### Compile

    python -m compileall -q src run.py

    exit 0

## 6. Modified files

Source in the Phase 8.2 copy:

- src/orchestrator/config.py
- src/orchestrator/runner.py
- src/orchestrator/telegram.py
- src/orchestrator/hermes_bridge.py
- src/orchestrator/executors/opencode.py
- src/orchestrator/executors/expert.py
- src/orchestrator/redact.py
- src/orchestrator/proc.py

Test support in the copy:

- tests/test_support.py (new)
- tests/test_env_isolation.py (rewritten)
- tests/test_orchestration.py (test policy import and usage)
- tests/fake.env (fake values only)

Copy-only:

- config/policy.json (points at copy runtime and tests/fake.env; must NOT be merged)
- phase8_2_changes.diff
- runtime/ and test-runs/

Production source was not modified.

## 7. Full diff

    C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-2-copy\orchestration\phase8_2_changes.diff

Diff scope: eight source files, 383 diff lines, no policy/runtime changes.

Key changes:

    config.py
    + PUBLIC_ENV_ALLOWLIST
    + public_env() allowlist
    + secret_env() case-insensitive, requested-only
    + executor_env()
    - env_with_secrets()

    runner.py
    + self.public_env / self.expert_env
    OpenCode -> public_env
    DeepSeek -> expert_env
    acceptance -> public_env

    opencode.py / expert.py
    - base_env or os.environ
    + explicit base_env or public_env

    hermes_bridge.py
    + public_env()

    telegram.py
    + public_env() for helpers
    + secret_env for Bot API token only

    redact.py
    + shared SECRET_ENV_NAMES

    proc.py
    + run_capture env required
    + absolute taskkill + minimal env

## 8. Residual risks and merge assessment

Residual risks:

1. PUBLIC_ENV_ALLOWLIST is strict. If a tool needs a custom non-secret variable
   (HTTP_PROXY, SSL_CERT_FILE, a custom SDK setting), it must be added explicitly and
   reviewed. Current scripts do not use proxy variables.
2. PYTHONPATH, PYTHONHOME, PSModulePath, and NODE_OPTIONS are allowlisted because
   Python/Node need them in some setups; they can point to user code. This is a
   compatibility choice, not a secret leak.
3. OpenCode currently uses a free model and needs no provider key. If the model
   changes to an API-key provider, add an explicit OpenCode secret allowlist.
4. Hermes CLI and Telegram helper receive public env only. They are expected to read
   their own .env/config; an actual hermes send was not tested (no Telegram target).
5. The test harness sets PYTHONDONTWRITEBYTECODE=1 to avoid a pre-existing same-size
   .pyc flake; this is test hygiene, not part of the credential fix.
6. This is still application-layer env isolation, not OS isolation. It does not
   confine filesystem writes; Phase 7 remains the isolation assessment.

Merge assessment:

- The three Phase 8.1 blockers are fixed in this copy.
- Env isolation tests and both verify modes pass; compile passes.
- No production source was changed.
- The copy is ready for final merge review.
- Do not merge config/policy.json, runtime, test-runs, or the diff file.

## 9. Merge set and verification

If approved, merge only:

    src/orchestrator/config.py
    src/orchestrator/runner.py
    src/orchestrator/telegram.py
    src/orchestrator/hermes_bridge.py
    src/orchestrator/executors/opencode.py
    src/orchestrator/executors/expert.py
    src/orchestrator/redact.py
    src/orchestrator/proc.py
    tests/test_support.py
    tests/test_env_isolation.py
    tests/test_orchestration.py
    tests/fake.env

Do not merge:

    config/policy.json
    phase8_2_changes.diff
    runtime/
    test-runs/

Post-merge verification:

    python -m unittest tests.test_env_isolation -v
    cmd /c verify.cmd
    python -m compileall -q src run.py

Expected: 11/11 env tests, 13/13 regression, compile exit 0.

## 10. Rollback

Production was not modified, so no production rollback is needed.

If merged later:

- Original source backup is in the Phase 8 copy:
  phase8-copy\orchestration\original\src\orchestrator\...
- Restore those files to roll back the source change.
- The Phase 8.2 diff can be used to review or reverse the change.
- Delete the Phase 8.2 copy to remove test artifacts; this does not affect
  production, Hermes, or runtime.
- No real .env, Agnes/DeepSeek config, SQLite/WAL, ACL, firewall, user, service, or
  Telegram setting was changed.

## 11. Final status

| Item | Status |
|---|---|
| A. Test uses fake env + temp runtime | PASS |
| B. Strict public allowlist | PASS |
| B. Unknown credentials and DATABASE_URL excluded | PASS |
| B. Case-insensitive secret lookup | PASS |
| C. run_capture requires env | PASS |
| C. taskkill minimal env and actual kill | PASS |
| Actual child-process env probes | PASS |
| Redaction expanded | PASS |
| Env tests | 11/11 PASS |
| Normal verify | 13/13 PASS |
| Verify with PYTHONDONTWRITEBYTECODE=1 | 13/13 PASS |
| Compile | exit 0 |
| Production modified | NO |
| Real keys accessed | NO |
| Model calls | 0 |
| Ready for final merge review | YES |
