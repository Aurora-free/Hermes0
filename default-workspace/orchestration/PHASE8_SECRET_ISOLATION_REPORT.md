# Phase 8 Secret Isolation Report

Date: 2026-10-24 (Asia/Singapore)
Production project: C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration
Isolated copy: C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-copy\orchestration

## Verdict

Isolated-copy fix: PASS.
Production source: NOT MODIFIED; the original leak remains in production.
Overall production status: BLOCKED pending review and merge.

No real .env value was read, printed, copied, or uploaded. All tests used fake values
only. No model was called.

## 1. Root cause and code locations

The old env_with_secrets loaded three names into a copy of the entire process
environment and returned that copy:

    config.py: env_with_secrets
    allowed = DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, TELEGRAM_BOT_TOKEN
    env = dict(os.environ)
    ... add allowed values ...

Every consumer then received the whole dict, not a per-executor allowlist:

    runner.py: self.env = env_with_secrets(policy)
    runner.py: OpenCodeExecutor(..., self.env)
    runner.py: DshHeadlessExpert(..., self.env)
    runner.py: expert analysis uses the same self.env
    runner.py: acceptance env was os.environ minus only two names
    opencode.py: base_env or os.environ
    expert.py: base_env or os.environ
    telegram.py: env_with_secrets for token; os.environ.copy() for Hermes CLI
    hermes_bridge.py: os.environ.copy() for Hermes CLI

Consequences:

- OpenCode inherited DEEPSEEK_API_KEY and TELEGRAM_BOT_TOKEN.
- DeepSeek inherited TELEGRAM_BOT_TOKEN.
- Acceptance commands inherited any secret not in the two-name filter.
- Hermes/Telegram helper subprocesses inherited the entire process environment.
- A child that can read environment variables could read unrelated credentials.

## 2. Before and after flow

Before:

    os.environ + .env allowlist
      -> one env dict containing DeepSeek + Telegram + public
      -> OpenCode child: all of it
      -> DeepSeek child: all of it
      -> acceptance child: os.environ minus two names
      -> Hermes CLI: os.environ
      -> Telegram helper: os.environ

After (copy):

    public_env()
      -> all inherited variables except secret names
      -> OpenCode: public only
      -> Hermes CLI: public only
      -> Telegram send helper: public only
      -> acceptance: public only

    executor_env(policy, required_names)
      -> public_env() + only requested secrets
      -> DeepSeek: DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL
      -> OpenCode: no provider or Telegram secrets

The reporter still reads TELEGRAM_BOT_TOKEN for its own Bot API fallback, but that
token is not passed to executor or acceptance children.

## 3. Fix implemented in the copy

### Public/secret split

New in config.py:

- SECRET_ENV_NAMES: known provider/Telegram/AWS secret names.
- _is_secret_name(): also filters names ending in _API_KEY, _TOKEN, _SECRET,
  _PASSWORD.
- public_env(extra_secret_names=()): inherited env with secrets removed.
- secret_env(policy, names): only explicitly requested names, from process env or
  the configured env file.
- executor_env(policy, names): public env plus only requested secrets.

env_with_secrets was removed to prevent future whole-env use.

### Runner

- self.public_env for OpenCode and acceptance.
- self.expert_env for DeepSeek, containing only DEEPSEEK_API_KEY and
  DEEPSEEK_BASE_URL.
- Acceptance uses dict(self.public_env) instead of os.environ minus two names.
- Both expert construction sites (normal analysis and takeover) use self.expert_env.

### Executors

- OpenCode default env is public_env().
- DeepSeek default env is public_env(); the runner passes expert_env explicitly.
- Explicit base_env still allows a caller to provide a controlled environment.

### Hermes CLI

- HermesKanban._run uses public_env().
- Hermes CLI can still read its own .env from disk; it no longer inherits unrelated
  secrets from the orchestrator process.

### Telegram

- _send_hermes and check_targets use public_env().
- Bot API fallback reads only TELEGRAM_BOT_TOKEN via secret_env, not the whole env.

## 4. Minimal source diff

Full diff:

    C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-copy\orchestration\phase8_changes.diff

Key changes:

    config.py
    + SECRET_ENV_NAMES
    + public_env()
    + secret_env()
    + executor_env()
    - env_with_secrets()

    runner.py
    - self.env = env_with_secrets(policy)
    + self.public_env = public_env(...)
    + self.expert_env = executor_env(policy, {DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL})
    OpenCode -> public_env
    DeepSeek -> expert_env
    acceptance -> self.public_env

    opencode.py / expert.py
    - base_env or os.environ
    + base_env if base_env is not None else public_env()

    hermes_bridge.py
    - env=os.environ.copy()
    + env=public_env()

    telegram.py
    - env=os.environ.copy()
    + env=public_env()
    - env_with_secrets(...)
    + secret_env(..., {TELEGRAM_BOT_TOKEN})

No production source file was changed.

## 5. Tests

All tests ran in the isolated copy with this fake env:

    DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY_FOR_TESTS
    TELEGRAM_BOT_TOKEN=FAKE_TELEGRAM_TOKEN_FOR_TESTS
    PUBLIC_TEST_VAR=public-value-must-survive
    FAKE_FILE_ONLY_SECRET=FAKE_FILE_ONLY_VALUE

The production .env was not opened; the copy policy points at
phase8-copy\orchestration\tests\fake.env.

### Env isolation suite

Command:

    python -m unittest tests.test_env_isolation -v

Result:

    Ran 10 tests
    OK
    exit 0

Tests:

1. public_env excludes DeepSeek/Telegram and file-only fake secret, keeps
   PUBLIC_TEST_VAR.
2. secret_env only returns explicitly requested names.
3. executor_env adds only DeepSeek key/base, never Telegram.
4. OpenCode env has neither provider nor Telegram secrets.
5. DeepSeek env has the key but no Telegram token.
6. Acceptance env has no secrets and keeps public variables.
7. OpenCode run() passes the public env to its subprocess (mocked run_capture).
8. DeepSeek analyze() passes expert env to its subprocess (mocked run_capture).
9. Hermes CLI subprocess env has no secrets.
10. Telegram helper subprocess env has no secrets.

The test output prints only test names; no fake or real secret value is printed.

### Existing regression

First run, without bytecode suppression:

    12/13 PASS, exit 1
    failure: test_expert_analysis_then_takeover

The diff showed calc.py was modified, but acceptance imported a stale same-size
calc.py bytecode file. This is a pre-existing same-size .pyc test flake, not an
environment-variable regression.

Second run with bytecode writes disabled:

    $env:PYTHONDONTWRITEBYTECODE='1'
    cmd /c verify.cmd

Result:

    13/13 PASS
    exit 0

The env-isolation suite was rerun after this:

    Ran 10 tests
    OK
    exit 0

No model calls were made.

## 6. Compatibility risks

1. OpenCode currently uses the free model opencode/deepseek-v4.1-flash-free; it does
   not need the DeepSeek key. If the model/provider changes to an API-key provider,
   add an explicit OpenCode secret allowlist; do not restore whole-env inheritance.
2. DeepSeek still receives DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL; expert behavior
   should be unchanged.
3. Acceptance commands no longer receive provider/Telegram keys. A test that
   incorrectly relied on ambient credentials will fail by design.
4. Hermes CLI and Telegram send now receive public env only; they are expected to
   read .env/config from disk. Real Telegram sending was not tested (no target).
5. env_with_secrets was removed. No internal call site remains; any external script
   importing it will break.
6. The broad suffix filter removes any custom *_TOKEN, *_API_KEY, *_SECRET, or
   *_PASSWORD variable from children. This is intentional, but app-specific
   variables with those suffixes need an explicit allowlist.
7. DEEPSEEK_BASE_URL is not secret and remains in public config; it is not a
   credential.
8. PYTHONDONTWRITEBYTECODE=1 is needed to avoid a pre-existing same-size .pyc test
   flake; this is a test-run recommendation, not part of the env fix.

## 7. Production status

The production source is unchanged. Therefore the original credential exposure still
exists in production. The fix is only proven in the isolated copy. Do not describe
production as fixed.

Production status: BLOCKED pending review and merge.

## 8. Rollback

Because production was not modified, no production rollback is required.

If the copy is reviewed and merged later:

- Original files are backed up at:
  phase8-copy\orchestration\original\src\orchestrator\...
- Restore those six files to roll back the source change.
- The full diff is at:
  phase8-copy\orchestration\phase8_changes.diff
- Delete the copy directory to remove all test artifacts; this does not affect
  production, Hermes, or runtime.

No real .env, Agnes/DeepSeek config, SQLite/WAL, ACL, firewall, user, service, or
Telegram setting was changed.

## 9. Final status

| Item | Status |
|---|---|
| Root cause identified | PASS |
| Public/secret env split in copy | PASS |
| OpenCode gets no unrelated secrets | PASS |
| DeepSeek gets key but no Telegram token | PASS |
| Acceptance gets no secrets | PASS |
| Hermes/Telegram helper subprocess env | PASS |
| Env isolation tests | 10/10 PASS |
| Existing regression | 13/13 PASS with PYTHONDONTWRITEBYTECODE=1 |
| Production fix applied | NOT APPLIED / BLOCKED |
| Real credentials accessed | NO |
| Model calls | 0 |

## 10. Next steps

1. Review the diff and approve merging the six source files from the copy.
2. Merge, then rerun:
   - tests.test_env_isolation
   - verify.cmd with PYTHONDONTWRITEBYTECODE=1
3. Add the env-isolation test to the normal regression command.
4. Before any real Telegram test, confirm hermes send still reads its own .env
   when given the public env.
5. Keep the isolated copy and original backup until review is complete.
