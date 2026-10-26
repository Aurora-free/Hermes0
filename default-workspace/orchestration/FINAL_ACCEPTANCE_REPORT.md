# Final Acceptance Report

Date: 2026-10-24 (Asia/Singapore)
Project: C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration
Runtime: C:\Users\22649\AppData\Local\HermesOrchestrator\runtime

## Conclusion: CONDITIONAL PASS

The core Hermes -> orchestrator -> OpenCode / DeepSeek -> acceptance -> card-state
closed loop is real and tested. A genuine OpenCode acceptance failure was exercised
and ended in a truthful blocked state. Concurrency, crash recovery, duplicate
dispatch, rollback, and budget gates have concrete evidence.

Conditions:

- OS-level write confinement is NOT implemented. The current boundary is declared
  allowed_paths plus post-hoc diff/rollback, not a Windows sandbox.
- Telegram has no configured target and no real delivery.
- A genuine OpenCode code failure that DeepSeek successfully repairs was not achieved.
  The real failure test proves safe stop; the successful-takeover proof remains the
  controlled-failure card.
- The reconcile scheduler is still a manual one-pass command; no service or cron was
  installed.
- Costs are local estimates, not provider invoices.

## 1. Current state before changes

- Git: no repository, so no git status/diff is available.
- Runtime DB: WAL, integrity_check=ok, local non-OneDrive path.
- Current tasks after this round: 16 succeeded, 4 blocked, 0 running.
- Ready orchestrator cards: none; hermes-reconcile returned results [].

Completed before this round:

- Hermes Kanban adapter, stable card/task linking, idempotency.
- Local runtime migration and old-copy preservation.
- Read-only, modification, approval, cancel, rollback, budget, retry, concurrency,
  crash recovery, and expert takeover tests.

Incomplete before this round:

- OS-level execution isolation.
- Real OpenCode code failure with successful DeepSeek repair.
- Telegram real delivery.
- Persistent scheduling.

Risks:

- The executor can write outside working_directory at the OS level; only writes inside
  working_directory are diffed/rolled back.
- Cost is estimated at $0.02 per call; there is no token-level reconciliation.
- A crash after a provider call but before budget recording can undercount one call.
- The card/task link is crash-safe for the tested paths, but the short window between
  task creation and the link comment remains theoretically exposed; DB lookup prevents
  duplicate task creation.

## 2. Files changed in this round

Production source: none.

New test fixtures and examples:

- examples/phase4-real-failure.json
- examples/phase3-jsonpatch.json
- examples/phase3-cron.json
- examples/concurrency-probe.json
- examples/crash-probe.json, crash-probe2.json, crash-probe3.json,
  crash-probe4.json, crash-probe5.json
- examples/budget-precheck.json
- FINAL_ACCEPTANCE_REPORT.md

Local test assets outside the repo:

- C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase4-real-failure\
- C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase4-harness\
- C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase3-jsonpatch\
- C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase3-cron\
- C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\concurrency\
- C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\crash-test*\
- C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase3-harness\

No Hermes config, .env, credential, SQLite/WAL, or system setting was modified.
No source file was modified this round, so no source backup was required. The old
OneDrive runtime copy remains preserved from the earlier migration.

## 3. Actual tests and exit codes

### 3.1 Runtime and database

    run.cmd --json preflight                         exit 0
    SQLite integrity_check                           ok
    SQLite journal_mode                              wal
    database_list                                    local runtime DB

### 3.2 Regression suite

Last full run of verify.cmd (source unchanged since that run):

    cmd /c verify.cmd                                exit 0
    13/13 tests PASS

The 13 tests are:

    test_read_only_success
    test_retry_then_success
    test_acceptance_blocks_false_success
    test_expert_analysis_then_takeover
    test_retry_cap_stops
    test_write_lock_blocks_second_writer
    test_rollback_restores_snapshot
    test_recover_orphan
    test_budget_guard
    test_scope_violation
    test_idempotent_task_reuse
    test_resume_requires_force
    test_cancel_queued

The suite was not repeatedly rerun this round, per instruction; the source did not
change after the recorded run.

### 3.3 Duplicate dispatch and concurrency

Two reconcile processes started against one ready card t_03184509:

    process 1: completed card, task T-20261024-211558-145309
    process 2: claim failed: status=running lock=...
    linked task count: 1
    final card: done, consistent=true

Result: no duplicate task, no duplicate execution.

### 3.4 Crash after claim, before run

Card t_e2dbf021, task T-20261024-212554-de263d:

    kill after link, task queued
    recover -> no orphan (queued is not in-flight)
    hermes-dispatch -> same task executed
    linked task count: 1
    final card: done

Result: link-before-run prevented an orphan card and a duplicate task.

### 3.5 Crash while running

Card t_5f36890b, task T-20261024-212912-1e3e42:

    kill while running
    recover --apply -> task blocked, runner_pid dead
    hermes-dispatch -> card blocked, status linked_pending
    final: card blocked, task blocked, consistent=true
    linked task count: 1

Result: no re-execution, no false success.

### 3.6 Real failure test

Card t_38a183fb, task T-20261024-221454-f9ad8b.

Disposable project:

    C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase4-real-failure

Acceptance harness outside allowed_paths:

    C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase4-harness\acceptance.py

The harness is intentionally unsatisfiable:

    result = calc.add(2, 3)
    assert type(result) is int
    assert result == 5
    assert result == 6

Observed attempts:

    attempt 1  opencode  exit 0  acceptance failed
    attempt 2  opencode  exit 0  acceptance failed
    attempt 5  deepseek_expert  expert_analysis  exit 0
    attempt 3  opencode  exit 0  acceptance failed
    attempt 4  deepseek_expert  expert_takeover  exit 0  acceptance failed
    final: blocked

DeepSeek analysis correctly identified the contradiction and refused an illegitimate
workaround; it recommended blocking rather than retrying. The expert changed calc.py
to return a + b, but the second assertion still failed. The task/card ended blocked,
not succeeded. Rollback restored calc.py to return a - b.

Budget used: 5 calls, estimated $0.10. Actual provider cost is not available.

This is a real OpenCode acceptance failure and a real DeepSeek analysis/takeover
attempt. It proves the safe-stop branch, not successful repair.

### 3.7 Controlled successful takeover (separate)

Card t_46e390a7, task T-20261024-205414-f2f517:

    2 controlled OpenCode failures
    real DeepSeek analysis
    1 controlled plan attempt
    real DeepSeek takeover
    acceptance passed
    final succeeded, card done

This remains the successful-takeover evidence, but the OpenCode failures were
controlled, not a genuine code failure.

### 3.8 Budget precheck

Task T-20261024-210756-74a833 was precharged to model_calls=6 and run:

    status: blocked
    reason: max_model_calls_per_task reached (6)
    attempts: 0
    result file written
    budget_exceeded dry-run report generated

No model call was started.

### 3.9 Telegram

    hermes send --list telegram
    -> no targets found for platform 'telegram'. Configured: (none)

    run.cmd --json telegram-test
    -> sent=false, reason=dry_run

    run.cmd --json telegram-test --send
    -> exit 2, target not authorized

No message was sent. No token or chat id was added.

### 3.10 Credential scan

    source scan for sk-* and bot tokens: 0 hits
    runtime scan for sk-* and bot tokens: 0 hits

### 3.11 Final state

    hermes-reconcile --limit 1: results []
    ready cards: []
    running tasks: []
    task counts: 16 succeeded, 4 blocked

## 4. Security isolation boundary

### What is actually enforced

- Every allowed_path is resolved and must be inside working_directory.
- A read-only task fails on any change under working_directory.
- Each attempt snapshots working_directory and compares before/after.
- Changes outside allowed_paths but inside working_directory are scope_violation and
  trigger rollback.
- A single worktree lock allows one writing task at a time.
- Hermes cards and the engine are linked and idempotent.
- Budget blocks before model calls.
- No direct Hermes SQLite writes.

### What is NOT enforced

OS-level confinement is not implemented:

- Windows Sandbox is not available in this environment; the feature query requires
  elevation.
- No separate sandbox user exists.
- The current token does not have the privileges needed to launch a low-integrity or
  AppContainer token; no such launcher was installed.
- icacls can label a directory Low integrity, but without a low-integrity launcher this
  does not confine the executor.
- OpenCode runs with --auto and has the OS permissions of the current user.
- The expert is a real local agent with file/terminal tools and can read outside the
  declared paths, even though writes are checked afterward.
- Writes outside working_directory are not in the snapshot and cannot be detected or
  rolled back.

Therefore the current isolation is a policy and post-hoc audit boundary, not an OS
sandbox. Do not claim otherwise.

### Safe alternatives

1. Windows Sandbox or a dedicated VM for executor processes (requires admin/feature
   setup and user approval).
2. A dedicated low-privilege Windows user with ACLs only on the task directory
   (requires account/admin work).
3. AppContainer per task (requires a Windows API launcher, capability configuration,
   and separate credentials handling).
4. Copy-on-write project execution: run on a disposable copy, apply only the verified
   diff to the real project. This reduces risk but is not OS isolation.

## 5. Reliability assessment

- Duplicate dispatch: protected by Hermes claim and engine idempotency; concurrent
  test produced one task.
- Crash while queued: same task resumed after the link-before-run fix.
- Crash while running: task recovered blocked, card blocked, no second task.
- Cancel: card blocked without creating a task.
- Rollback: real modification restored; real failure test restored calc.py.
- Budget: all model call sites are gated; budget block writes a result and does not
  call the model.
- Model-call accounting: call counts are recorded after a launched call; a crash
  between provider response and DB write can undercount. No provider reconciliation.
- Unrecoverable failure: blocked with reason and result path; no false success.

## 6. Telegram and scheduling

Telegram: blocked, not delivered. No target and no credentials. Dry-run content and
fail-closed behavior were tested.

Scheduling: not installed. The only trigger is a manual one-pass:

    run.cmd --json hermes-reconcile --limit 1

No Windows service, cron job, startup item, or long-running daemon was created.

## 7. Start, stop, status, recovery

Start one dispatch pass:

    cd C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration
    run.cmd --json hermes-reconcile --limit 1

Create a Hermes card from a spec:

    run.cmd --json hermes-create --file examples\hermes-readonly.json

Query card and engine state:

    run.cmd --json hermes-query --card <card_id>
    run.cmd --json status <task_id>
    run.cmd --json report <task_id>

Cancel/block:

    run.cmd --json hermes-cancel --card <card_id>
    run.cmd cancel <task_id>

Recover after a crash:

    run.cmd --json recover --apply
    run.cmd --json hermes-dispatch --card <card_id>
    run.cmd resume <task_id> --force

Stop: there is no daemon to stop. Ensure no running task:

    run.cmd --json status <task_id>
    run.cmd --json hermes-query --card <card_id>

For a still-running process, identify the runner PID from task status and terminate
only that process tree; do not kill unrelated Hermes/DSH processes.

## 8. Rollback

Per task:

    run.cmd rollback <task_id> --yes

This restores the initial snapshot and removes files added after it. Do not use it on a
running task; recover/stop first.

This round's artifacts:

- No production source was changed.
- Delete the phase 3/4 examples and FINAL_ACCEPTANCE_REPORT.md to remove this round's
  repo additions.
- Delete the local test-projects directories and harnesses if they are no longer
  needed.
- Block/archive the disposable Hermes cards via hermes kanban if desired.

Project runtime:

- Old OneDrive runtime data is preserved as a backup.
- To revert the runtime location, stop all runners, preserve the local runtime
  directory, and point runtime_root back to the old copy. Do not move SQLite/WAL while
  a runner is active.
- Do not delete the OneDrive project tree.

No source backup was needed this round because no production source was modified.

## 9. Remaining work

1. Implement a real OS-level confinement mechanism (Windows Sandbox/VM, dedicated
   sandbox user, or AppContainer) and test outside-directory writes.
2. Obtain a genuine OpenCode code failure that DeepSeek successfully repairs; current
   real failure proves safe stop only.
3. Configure an authorized Telegram target and approve one real send.
4. Add actual provider usage/cost reconciliation.
5. Design and approve a single-instance scheduled reconcile before any service.

## Final verdict

CONDITIONAL PASS.

Evidence supports the core execution and safety-stop loop, but not OS-level isolation,
Telegram delivery, persistent automation, or successful repair of a genuine
OpenCode code failure. Those must remain explicit conditions, not hidden assumptions.
