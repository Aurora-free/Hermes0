# Overnight Progress Report

Date: 2026-10-24 (Asia/Singapore)
Project: C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration
Runtime: C:\Users\22649\AppData\Local\HermesOrchestrator\runtime
Git: no repository present.

## Overall verdict

Core execution and safety-stop loops: CONDITIONAL PASS.
Real OpenCode failure followed by successful DeepSeek repair: BLOCKED.

The blocking reason is not a system defect: OpenCode solved both new hard tasks on
its first attempt. No fake failure, contradictory acceptance, test modification, or
permission bypass was used.

## 1. Baseline verification

Paths exist:

- orchestration project
- local runtime
- local test-projects directory

Launcher commands verified with run.cmd --help:

- init, create, run, status, list, recover, resume, approve, rollback, report,
  preflight, cancel
- hermes-create, hermes-dispatch, hermes-reconcile, hermes-query, hermes-cancel
- telegram-check, telegram-test

Prior state:

- no Git repository, so no git diff/status
- runtime DB: WAL, integrity_check=ok, local non-OneDrive path
- prior phase4 rollback confirmed: calc.py is return a - b
- no running tasks before this round
- final state after this round: 18 succeeded, 4 blocked, 0 running
- hermes-reconcile returned results []

## 2. Task A findings

### Confirmed

- Production source was not modified in this round.
- The prior final report and phase reports are present.
- run.cmd works and exposes the expected commands.
- The local runtime DB is the one actually in use.
- The previous phase4 test project is rolled back.
- Existing cards and tasks are consistent; no ready cards remain.

### Completed / incomplete / risky

Completed before this round:

- Hermes Kanban adapter and card/task linking
- local runtime migration
- read-only, modification, approval, cancel, rollback
- concurrency and crash recovery tests
- budget precheck and blocked-state handling
- controlled expert takeover

Incomplete:

- OS-level executor isolation
- real OpenCode failure followed by successful DeepSeek repair
- Telegram real delivery
- persistent scheduling
- actual token/cost reconciliation
- out-of-working-directory write detection

Risks:

- allowed_paths plus post-hoc diff is not an OS sandbox
- writes outside working_directory are not detected or rollback-able
- cost is a fixed estimate
- a crash between provider response and budget write can undercount one call

## 3. Task B: real failure and repair attempts

### 3.1 Attempt 1: timezone-aware cron

Project:

    C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase5-dst-cron

Initial implementation was deliberately incomplete for a realistic reason: it used
naive local-time iteration, AND semantics for DOM/DOW, and no DST fold handling.

Acceptance suite inside the project:

    tests/test_cron.py

The test file was made read-only and hashed before execution.

Initial local result:

    Ran 9 tests
    FAILED (failures=3)
    exit 1

A separate local reference implementation passed all 9 tests:

    Ran 9 tests
    OK
    exit 0

This proves the task was satisfiable.

Hermes/OpenCode run:

    card t_d3533106
    task T-20261024-222708-1fef93
    1 OpenCode attempt
    exit 0
    9/9 tests passed
    card status done

Test hash:

    before C7D702F13D1AA508891BEA1ED408F802041F5757481DCFA00A5AF730EC228FC9
    after  C7D702F13D1AA508891BEA1ED408F802041F5757481DCFA00A5AF730EC228FC9
    unchanged: true

Outcome: OpenCode fixed it on the first attempt. No DeepSeek escalation.

### 3.2 Attempt 2: JSON Schema subset

Project:

    C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\phase5-jsonschema

No jsonschema package is installed, so this required a real implementation.

Acceptance suite inside the project:

    tests/test_validator.py

The tests cover type checks, required/properties, additionalProperties false and
schema, array items, enum, bounds, allOf, anyOf, oneOf, local $ref, not, type lists,
and bool-vs-integer.

Initial local result:

    all 11 tests errored at NotImplementedError
    exit 1

The test file was read-only and hashed.

Hermes/OpenCode run:

    card t_52011dfe
    task T-20261024-223045-ea33f9
    1 OpenCode attempt
    exit 0
    all tests passed
    card status done

Test hash:

    before 549A615CE53D889062EC26FB395F48F2854D02B889809EC85873E45A23B7B99D
    after  549A615CE53D889062EC26FB395F48F2854D02B889809EC85873E45A23B7B99D
    unchanged: true

Outcome: OpenCode fixed it on the first attempt. No DeepSeek escalation.

### 3.3 Real failure that did reach DeepSeek (safe stop, not repair)

This is the phase4 test retained from the prior round:

    card t_38a183fb
    task T-20261024-221454-f9ad8b

Acceptance was intentionally unsatisfiable (int result must equal 5 and 6). This was
a disposable failure fixture, not a production task.

Attempts:

    1  opencode        exit 0  acceptance failed
    2  opencode        exit 0  acceptance failed
    5  deepseek_expert expert_analysis exit 0
    3  opencode        exit 0  acceptance failed
    4  deepseek_expert expert_takeover exit 0  acceptance failed
    final: blocked

DeepSeek correctly identified the contradiction and recommended blocking rather than
a workaround. It did not fix an unsatisfiable requirement, and the task did not
falsely succeed.

This proves real failure -> analysis -> takeover attempt -> safe blocked. It does not
prove successful repair.

### 3.4 Controlled successful takeover

    card t_46e390a7
    task T-20261024-205414-f2f517

Controlled OpenCode failures, real DeepSeek analysis, real DeepSeek takeover,
acceptance passed, card done. This is useful evidence but not a genuine OpenCode
code failure.

### 3.5 Task B conclusion

Real failure -> successful DeepSeek repair: BLOCKED.

Reason: the two fair, satisfiable, read-only-test tasks were both solved by OpenCode
on the first attempt. The only real failure that reached DeepSeek was the unsatisfiable
phase4 fixture, which correctly ended blocked.

No fabrication was used. Do not change the verdict to PASS without a genuine failure
and a genuine independent acceptance pass.

## 4. Task C: regression and safety

### Regression

Command:

    cmd /c verify.cmd

Result:

    exit 0
    13/13 tests PASS

Test list:

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

### State and duplicates

    hermes-reconcile --limit 1 -> results []
    ready cards -> []
    running tasks -> []
    task counts -> 18 succeeded, 4 blocked

Prior concurrency test: two reconcile processes, one completed, one claim conflict,
exactly one linked task.

Prior crash tests:

- crash while queued: same task resumed, no duplicate
- crash while running: task blocked, card blocked, no duplicate

### Budget

- Every model call path is guarded before launch.
- Budget precheck at 6 calls blocked before any model call and wrote a result.
- This round's phase5 tasks each used 1 call.
- Runtime ledger: 24 calls, estimated $0.48.
- The ledger is an estimate at $0.02/call; it is not a provider bill.
- No provider hard budget or token-level reconciliation is configured.

### Test integrity

Both phase5 test files were hashed before and after:

- cron test unchanged
- JSON Schema test unchanged

They were also set read-only. No test modification was accepted.

### File boundaries

- allowed_paths are validated under working_directory.
- Changes inside working_directory but outside allowed_paths trigger
  scope_violation and rollback.
- Writes outside working_directory are not in the snapshot, cannot be detected, and
  cannot be rolled back.
- No OS sandbox was used. See section 6.

## 5. Task D: next-phase plan (not executed)

### OS isolation

Options:

1. Windows Sandbox or dedicated VM.
   - Requires Windows feature/admin setup.
   - Then rerun the external-write probe and the real failure test.
   - Rollback: disable/remove the sandbox feature after approval.

2. Dedicated low-privilege Windows user with ACLs only on task directories.
   - Requires account creation/admin approval.
   - Test read/write, network, and credential access before use.
   - Rollback: remove the account after stopping tasks.

3. AppContainer per task.
   - Requires a Windows API launcher, capability SIDs, and separate credential handling.
   - Do not attempt without a prototype and rollback plan.

4. Copy-on-write project execution.
   - Run on a disposable copy; apply only the verified diff to the real project.
   - This reduces risk but is not OS isolation.

### Telegram

- No target exists. Required: secure bot token in Hermes, an authorized chat id, and
  telegram.enabled/authorized set.
- Validate with a dry-run, then ask for explicit approval for one send.
- Do not put the token in task JSON, policy.json, logs, or this report.

### Scheduled execution

- Current trigger is manual: run.cmd --json hermes-reconcile --limit 1.
- Before any schedule: single-instance lock, poll interval, claim TTL, stop procedure,
  crash recovery, and log rotation.
- Do not install a Windows service or startup entry without explicit approval.

### Cost reconciliation

- Current cost is estimated, not billed.
- Need token usage from the provider or response payload and a reconciliation record.
- Keep the local call-count gate regardless.

### External write detection

- Current snapshot covers only working_directory.
- Need a pre-execution OS sandbox or a broader monitored root.
- A post-hoc scan of the whole user profile is not a safe substitute.

## 6. Status table

| Item | Status | Evidence |
|---|---|---|
| Baseline paths/runtime | PASS | preflight, DB integrity ok, local WAL |
| run.cmd commands | PASS | --help and executed commands |
| Prior rollback intact | PASS | phase4 calc.py is a - b |
| DST cron initial failure | PASS | 9 tests, 3 failures, exit 1 |
| DST cron independent repair | PASS | OpenCode 1 attempt, 9/9, card done |
| JSON Schema initial failure | PASS | 11 errors, exit 1 |
| JSON Schema independent repair | PASS | OpenCode 1 attempt, all pass, card done |
| Real OpenCode failure -> successful DeepSeek repair | BLOCKED | OpenCode solved both fair tasks first try |
| Real failure -> analysis -> safe blocked | PASS | card t_38a183fb, 5 calls, blocked |
| Controlled successful takeover | PASS | card t_46e390a7 |
| Regression | PASS | 13/13, verify.cmd exit 0 |
| Duplicate dispatch / crash recovery | PASS | prior concurrency and crash evidence |
| Budget precheck | PASS | task T-20261024-210756-74a833 |
| Test integrity | PASS | hashes unchanged |
| OS-level isolation | BLOCKED | no Sandbox/sandbox user/token launch |
| External write detection | NOT TESTED | no OS sandbox; boundary known |
| Telegram delivery | BLOCKED | no targets, no send |
| Persistent scheduling | NOT TESTED | not installed |
| Actual cost reconciliation | NOT TESTED | estimates only |

## 7. Model calls and cost

This round's phase5 tests:

- DST cron: 1 OpenCode call, estimated $0.02
- JSON Schema: 1 OpenCode call, estimated $0.02

Retained real-failure test (prior round):

- 5 calls, estimated $0.10

Runtime ledger total:

    24 calls, estimated $0.48

This is not a bill. OpenCode's model reports cost 0; DeepSeek usage is not reconciled.
No provider-side hard budget exists.

## 8. Modified files and rollback

Production source: none this round.

New files:

- OVERNIGHT_PROGRESS_REPORT.md
- examples/phase5-dst-cron.json
- examples/phase5-jsonschema.json
- examples/phase4-real-failure.json (test fixture)
- local test projects and harnesses under
  C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\

No Hermes .env, Agnes/DeepSeek config, Hermes SQLite/WAL, or system setting was
modified.

Rollback:

1. Delete the new examples and this report to remove repository additions.
2. Delete the local phase5 test projects if no longer needed.
3. Block/archive the disposable Hermes cards via hermes kanban if desired.
4. Per-task rollback remains: run.cmd rollback <task_id> --yes.
5. Do not move SQLite/WAL while any runner is active.

## 9. Start, stop, status, recovery

    cd C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration

    run.cmd --json hermes-reconcile --limit 1
    run.cmd --json hermes-create --file examples\phase5-dst-cron.json
    run.cmd --json hermes-query --card <card_id>
    run.cmd --json status <task_id>
    run.cmd --json recover --apply
    run.cmd --json hermes-dispatch --card <card_id>
    run.cmd resume <task_id> --force
    run.cmd --json telegram-check
    run.cmd --json telegram-test

There is no installed daemon. Stop means: verify no running task, block/cancel cards,
and terminate only the specific runner PID if one is active.

## 10. Next executable tasks

1. Implement one OS-level isolation option (Windows Sandbox/VM or dedicated user) and
   run an external-write probe before any more automation.
2. Build one genuinely harder task with independent tests and try to induce a real
   OpenCode failure; if it still passes, record the negative result and stop.
3. Configure Telegram only after isolation is accepted, then dry-run and request one
   send approval.
4. Add actual token/cost reconciliation.
5. Design a single-instance scheduled reconcile with stop/recovery tests; do not install
   it until approved.
