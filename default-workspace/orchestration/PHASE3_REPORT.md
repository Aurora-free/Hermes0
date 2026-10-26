# Phase 3 Audit and Isolated Failure Test

Date: 2026-10-24 (Asia/Singapore)
Scope: read-only audit of the phase-2 implementation, plus an isolated real-failure
test. No Hermes config, credentials, Telegram send, system settings, startup item, or
service was changed.

## 1. Executive result

The core chain is real and testable:

    Hermes Kanban -> bridge -> existing engine -> OpenCode / DeepSeek expert
                  -> acceptance -> diff -> card state

The runtime is confirmed local, the database is healthy, and concurrent/crash tests
found two real defects that were fixed and retested:

1. The card-to-task link was written after the run started, so a crash could leave a
   running card with no link.
2. A crash/orphan task could be ignored by query, allowing a later dispatch to create
   a second task.

A genuine OpenCode code failure was still not produced. Four isolated hard tasks all
passed on the first attempt. That remains an open verification item; no failure was
fabricated.

## 2. Audit findings

### 2.1 Runtime database identity — PASS

Preflight and SQLite audit:

    runtime_root = C:\Users\22649\AppData\Local\HermesOrchestrator\runtime
    on_onedrive = false
    database_list = main -> ...\runtime\state\orchestrator.db
    journal_mode = wal
    integrity_check = ok
    tasks=11 attempts=27 events=100 budget=15

The old OneDrive runtime copy is preserved as a backup. New work uses the local DB.

### 2.2 Budget coverage — MOSTLY PASS, COST IS ESTIMATED

Static call trace found exactly three model-call paths:

- OpenCode executor, called only from runner._attempt
- DeepSeek expert analysis, called only from runner._run_expert_analysis
- DeepSeek expert takeover, called only from runner._attempt

Both runner paths call _check_budget before launching the process:

    runner.py:396  _check_budget(task, executor)      # OpenCode and takeover
    runner.py:535  _check_budget(task, expert)        # expert analysis

Budget is recorded after a call only when outcome.model_call_started is true. Retries,
timeouts, and expert takeover are counted because they all go through those paths.
Simulated tasks do not count.

Limits:

- Cost is a fixed $0.02-per-call estimate, not parsed provider usage. It is not a bill.
- If the process dies after the provider call but before _record_budget writes, the call
  can be undercounted. There is no provider-usage reconciliation.
- The budget probe used a pre-charged call count and est=$0.00; that proves the call
  gate, not zero real cost.
- No provider-side hard budget is configured.

### 2.3 Concurrency and crash behavior — PASS AFTER FIXES

Two reconcilers on one ready card:

    process 1: claimed and completed t_03184509
    process 2: claim failed with status=running lock=...
    linked task count: 1
    final card: done, consistent=true

Crash after the link but before the task ran:

    card t_e2dbf021, task T-20261024-212554-de263d
    kill -> task queued, card running, link already present
    recover -> no orphan (queued is not in-flight)
    dispatch -> ran the same task, no second task; final card done

Crash while the task was running:

    card t_5f36890b, task T-20261024-212912-1e3e42
    kill -> recover marked task blocked
    dispatch -> card blocked, linked_pending, no new task
    final: card blocked, task blocked, consistent=true, task count=1

Fixes made during this audit:

- card link comment is now written before run
- query/cancel/dispatch fall back to hermes_task_id DB lookup if the comment is absent
- an orphaned running task is recovered and the card is blocked, not re-run
- lock release clears the owner record, avoiding stale-owner false positives

### 2.4 File write boundary — PARTIAL / NEEDS HARDENING

What is enforced:

- create_task resolves every allowed_path and rejects paths outside working_directory.
- A read-only task is failed on any changed file.
- Every attempt snapshots working_directory before and after; changed files outside
  allowed_paths are scope_violation and trigger rollback.

What is not enforced:

- There is no pre-execution OS sandbox. OpenCode runs with --auto and can write
  anywhere the process has permission; allowed_paths is prompt plus post-hoc diff.
- snapshot.scan only covers working_directory. A write outside working_directory is
  neither detected nor rollback-able.
- The expert is a real local agent with file/terminal tools; it can read outside the
  allowed path even though writes are checked afterward.

The earlier expert __pycache__ cleanup was inside the task's allowed project directory,
so it was within the declared scope. It still shows why a narrow allowed_paths and a
dedicated project directory are required.

Recommendation: put executors under an OS-level sandbox or OpenCode permission policy,
and use a dedicated per-task project directory. Do not rely on diff/rollback as the
primary permission boundary.

### 2.5 Telegram — BLOCKED, UNCHANGED

    hermes send --list telegram
    -> no targets found for platform 'telegram'. Configured: (none)

No token, chat id, authorization, or send was added. The dry-run reporter and
budget_exceeded message remain fail-closed.

## 3. Isolated real-failure test

Isolation:

- projects under C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\
- acceptance harness under phase3-harness, outside each project's allowed_paths
- one card per task, tenant=orchestrator
- no existing project files were touched

Tasks attempted:

| Card | Task | Result |
|---|---|---|
| t_89d1d7fb | T-20261024-213133-167477 RFC 6902 JSON Patch | succeeded, first attempt |
| t_a1b52fdc | T-20261024-213443-4d81ba cron next-run with DOM/DOW OR | succeeded, first attempt |
| t_45928216 | T-20261024-205751-f56aa6 RFC 6901 JSON Pointer | succeeded, first attempt |
| t_46e390a7 | controlled OpenCode failures + real expert | succeeded via expert |

The real OpenCode tasks all passed on attempt 1. Therefore no real OpenCode failure
occurred and the real failure -> expert ladder remains unverified. The only escalation
evidence is the controlled-failure card t_46e390a7: two simulated OpenCode failures,
real DeepSeek analysis, simulated plan step, real DeepSeek takeover, acceptance passed.

No test deleted important files, changed system settings, or manufactured credential
errors.

## 4. What was actually verified

- local runtime DB identity and integrity
- all model call sites reach a budget gate
- two concurrent reconcilers do not double-create tasks
- crash after link and crash while running do not duplicate tasks after the fixes
- orphan task blocks the card
- real Hermes read-only, modification, approval, cancel, and escalation cards
- 13/13 regression tests, verify.cmd exit 0
- secret precheck rejects a fake key before card creation
- Telegram remains unconfigured and unsent

## 5. Remaining open items

1. Genuine OpenCode code failure and real failure -> expert ladder.
2. Telegram token, authorized chat id, and one approved send.
3. OS-level per-task write confinement.
4. Actual provider token/cost reconciliation.
5. Automatic reconcile scheduling (not installed; manual one-pass only).
6. Crash during the short window between task creation and the link comment remains a
   theoretical gap, although DB lookup now prevents a duplicate task.

## 6. Recommended next phase

1. Harden execution isolation before automation: dedicated project root, OS sandbox or
   OpenCode permission rules, and a strict allowed_paths policy.
2. Add a deliberately flawed but recoverable code task in a new temporary project; if
   OpenCode still passes, accept that the free model is capable and test the expert
   path with a controlled failure rather than forcing a fake bug.
3. Configure Telegram only after the isolation and real-failure evidence is accepted.
4. Only then consider a single-instance reconcile schedule, with stop/restart tests.
