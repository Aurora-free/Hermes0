# Phase 6 Expert Takeover Report

Date: 2026-10-24 (Asia/Singapore)
Project: C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration
Runtime: C:\Users\22649\AppData\Local\HermesOrchestrator\runtime

## Verdict

Real OpenCode failure followed by successful DeepSeek takeover: BLOCKED.

The designated task had a genuine initial failure and a satisfiable, independent
acceptance suite. OpenCode fixed it on its first attempt. DeepSeek was never invoked.
No simulated failure, contradictory acceptance, test modification, or permission
bypass was used.

## 1. Existing escalation logic

The current engine is in src/orchestrator/runner.py:

- _run_locked starts with OpenCode attempt 1.
- On non-infrastructure failure, it runs OpenCode attempt 2 with a different prompt
  and the previous failure evidence.
- If attempt 2 still fails, it sets needs_expert.
- _run_expert_analysis calls the DeepSeek Harness expert once.
- If analysis succeeds, OpenCode implements the plan once.
- If that still fails, the expert takes over once under the same worktree lock.
- If takeover fails, the task becomes blocked.
- Infrastructure categories (auth, quota, network, permission, tool_missing) stop
  before expert escalation.

Routing is policy-driven:

    expert.mode = dsh_headless
    expert.dsh_cli = ...\dsh.cmd
    expert.base_url = https://api.deepseek.com/anthropic
    opencode.model = opencode/deepseek-v4.1-flash-free
    limits.max_opencode_attempts = 2
    limits.max_model_calls_per_task = 6

Every OpenCode or expert launch goes through _check_budget before the process starts.

## 2. Why previous tasks did not trigger DeepSeek

Phase 5 calibration tasks were run with independent, read-only tests:

### DST-aware cron

    card t_d3533106
    task T-20261024-222708-1fef93
    initial implementation: 3/9 failures
    OpenCode: 1 attempt
    final: 9/9 pass
    card: done

OpenCode rewrote the local-time loop to UTC instant iteration, fixed DOM/DOW OR
semantics, and handled DST. It passed on the first attempt.

### JSON Schema subset

    card t_52011dfe
    task T-20261024-223045-ea33f9
    initial implementation: all 11 tests errored
    OpenCode: 1 attempt
    final: all tests pass
    card: done

OpenCode implemented the requested subset on the first attempt.

Both test files were read-only and hashed before/after; hashes were unchanged.
The reason no expert was invoked is simply that the free model solved both tasks on
attempt 1. This is a capability observation, not a routing bug.

## 3. Designated task: from-scratch regex engine

Project:

    C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase6-regex

The task requires a from-scratch fullmatch engine supporting literals, dot,
*, +, ?, bounded repeats, classes, anchors, alternation, groups, backreferences,
lazy quantifiers, predefined classes, and escaped metacharacters. It also forbids
importing the stdlib re module.

Tests are inside the project and read-only:

    tests/test_regex.py

The test file was hashed before and after execution.

Initial local run:

    python -m unittest tests.test_regex -v
    13 errors, 1 pass
    exit 1

The only passing initial test is the source-level check that stdlib re is not
imported; all functional tests fail because the stub raises NotImplementedError.

The task is satisfiable: a correct from-scratch engine can implement the tested
subset. No contradictory or impossible assertion was used.

## 4. Actual Hermes/OpenCode run

Card:       t_8195fe79
Task:       T-20261024-223712-4cc709
Executor:   opencode
Attempts:   1
Exit code:  0
Tests:      14/14 pass
Card:       done
Result:     C:\Users\22649\AppData\Local\HermesOrchestrator\runtime\results\T-20261024-223712-4cc709.json

Test hash:

    before 4A71DF7BD4616BBCF48F0198075A4634EA47A1DD8A0F970951D78727184F3316
    after  4A71DF7BD4616BBCF48F0198075A4634EA47A1DD8A0F970951D78727184F3316
    unchanged: true

Direct independent acceptance re-run:

    python -m unittest tests.test_regex
    Ran 14 tests
    OK
    exit 0

OpenCode implemented a backtracking continuation matcher and passed all tests on
attempt 1. No second OpenCode attempt was needed, so the expert branch was never
entered. DeepSeek calls: 0.

## 5. Real failure evidence that did reach DeepSeek

The prior phase4 fixture remains the only real failure that reached the expert:

    card t_38a183fb
    task T-20261024-221454-f9ad8b
    5 model calls
    final status: blocked

Its acceptance was intentionally unsatisfiable, so it cannot prove successful repair.
It does prove the safe-stop branch: real OpenCode acceptance failures, real DeepSeek
analysis, real takeover attempt, and a truthful blocked result.

The controlled successful takeover remains:

    card t_46e390a7
    task T-20261024-205414-f2f517
    controlled OpenCode failures + real DeepSeek analysis/takeover + pass

That is not a genuine OpenCode code failure.

## 6. Why this is BLOCKED, not PASS

PASS requires all of:

1. Real OpenCode execution and real failure.
2. DeepSeek analysis invoked by the existing routing.
3. DeepSeek takeover modifies the test copy.
4. Independent acceptance passes.
5. Task/card state is correct.
6. Modification scope is compliant.

Items 1 happened at the initial-code level, but OpenCode repaired it on attempt 1.
Items 2-5 did not happen. Reporting PASS would be false.

## 7. Regression and state

Command:

    cmd /c verify.cmd

Result:

    exit 0
    13/13 tests PASS

Final runtime state:

    19 succeeded
    4 blocked
    0 running
    budget ledger: 25 calls, estimated $0.50

The budget ledger is an estimate at $0.02 per call, not a provider invoice. OpenCode
reports cost 0 for its free model; DeepSeek usage is not reconciled. No provider-side
hard budget is configured.

This round's regex task:

    1 model call
    estimated $0.02
    0 DeepSeek calls

Calibration tasks this round:

    DST cron: 1 OpenCode call
    JSON Schema: 1 OpenCode call

## 8. OS isolation, Telegram, scheduling

- OS isolation: BLOCKED. Windows Sandbox is unavailable/requires elevation; no
  sandbox user; no low-integrity/AppContainer launch privilege. allowed_paths plus
  post-hoc diff is not an OS sandbox.
- Telegram: BLOCKED. hermes send --list telegram reports no targets; only dry-run.
  No token, chat id, or authorization was added.
- Persistent scheduling: NOT TESTED. Only manual hermes-reconcile was used; no
  service, cron, startup item, or long-running daemon.
- External-write detection: not implemented; snapshot only covers working_directory.

## 9. Files changed and rollback

Production source: not modified this round.

New files:

- PHASE6_EXPERT_TAKEOVER_REPORT.md
- examples/phase6-regex.json
- examples/phase5-dst-cron.json
- examples/phase5-jsonschema.json
- local test projects:
  - test-projects/phase6-regex
  - test-projects/phase5-dst-cron
  - test-projects/phase5-jsonschema
  - test-projects/phase5-reference

No Hermes .env, Agnes/DeepSeek config, SQLite/WAL, or system setting was changed.

Rollback:

1. Delete the new examples and this report.
2. Delete the local phase5/phase6 test projects if no longer needed.
3. Block/archive disposable cards if desired.
4. Per-task rollback remains: run.cmd rollback <task_id> --yes.
5. Do not move SQLite/WAL while a runner is active.

## 10. Next executable steps

1. Either accept the BLOCKED verdict for real repair, or explicitly approve a harder
   task/challenge with independent tests. Do not use impossible acceptance.
2. Implement OS-level isolation before further automation; this is the larger safety
   gap.
3. Telegram and scheduling remain separate approvals; no changes were made.
