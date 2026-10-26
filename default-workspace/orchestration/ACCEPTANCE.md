# Acceptance Report

Date: 2026-10-24 (Asia/Singapore)
Host: Windows 11, user desktop-4krb498\22649
Runtime: DeepSeek Harness with file policy danger-full-access for the real runs.
No credentials are reproduced below.

## Summary

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | Hermes CLI starts | PASS | hermes --version exit 0, Python 3.14.7 |
| 2 | OpenCode controlled non-interactive task | PASS | T-20261024-201032-30cdbc |
| 3 | Hermes dispatches a read-only task | PARTIAL | orchestrator dispatch PASS; Hermes adapter NOT WIRED |
| 4 | Recognise simulated failure, stop/escalate at cap | PASS | test_retry_cap_stops, test_acceptance_blocks_false_success |
| 5 | DeepSeek expert callable, boundary evidenced | PASS | T-20261024-202006-920329 analysis + takeover |
| 6 | Expert takeover excludes OpenCode writes | PASS | lock test + takeover event, no concurrent writer |
| 7 | Telegram report delivered | BLOCKED | no token/chat id; dry-run only |
| 8 | Budget/retry caps block extra calls | PASS | unit test + real cap hit at 6 calls |
| 9 | Diff and recorded restore | PASS | T-20261024-201146-15d1d6 + rollback |
| 10 | Success supported by acceptance | PASS | false-success test; all real successes ran acceptance |

## Details

### 1. Hermes CLI starts — PASS

    hermes --version
    Hermes Agent vgit.4d04303 (2026.9.24) · stable · local 4d04303818b6
    Install directory: C:\Users\22649\AppData\Local\hermes\hermes-agent
    Install method: git
    Python: 3.14.7
    OpenAI SDK: 2.24.0
    exit 0

hermes pm doctor exit 0; all required components are present (python 3.14.7,
node 26.7.0, git, ripgrep, uv, ffmpeg, chromium, ...). Optional packages (gh, bws,
llama.cpp builds) are absent but not required.

No hermes pm repair was run. The initial silent failure under the DSH workspace-write
sandbox was an access-denied boundary on the bundled Python directory, not a
dependency fault.

### 2. OpenCode controlled non-interactive task — PASS

    opencode run --standalone --auto --format json \
      -m opencode/deepseek-v4.1-flash-free \
      "Read hello.txt and reply with exactly its contents. Do not modify any file."
    exit 0 · 7.7 s · hello.txt unchanged

The JSONL session shows the read tool returning OPENCODE_READ_OK and the final
assistant text OPENCODE_READ_OK. Then the orchestrator repeated this through its own
adapter:

    T-20261024-201032-30cdbc  status=succeeded  attempt_count=1  model_calls=1
    diff: added=[], removed=[], modified=[]
    acceptance: passed

Note: deepseek/deepseek-chat returns provider.no-route; the only configured model is
opencode/deepseek-v4.1-flash-free.

### 3. Hermes dispatches a read-only task — PARTIAL

The orchestrator can dispatch a read-only task and produce a structured result
(T-20261024-201032-30cdbc). Hermes itself was not wired as the dispatcher: no Hermes
plugin, Kanban adapter, or gateway bridge was built. Hermes CLI is healthy, so this is
an integration gap, not a capability gap. Do not claim Hermes dispatched the task.

### 4. Simulated failure handling — PASS

Deterministic tests in tests/test_orchestration.py:

- test_retry_cap_stops: always-failing executor -> exactly 2 attempts, then blocked;
  no third call.
- test_acceptance_blocks_false_success: executor exits 0 but acceptance fails ->
  blocked, category acceptance, never succeeded.
- test_scope_violation: out-of-scope write -> immediate stop, rollback, blocked.

All 10 tests pass:

    PASS test_read_only_success
    PASS test_retry_then_success
    PASS test_acceptance_blocks_false_success
    PASS test_expert_analysis_then_takeover
    PASS test_retry_cap_stops
    PASS test_write_lock_blocks_second_writer
    PASS test_rollback_restores_snapshot
    PASS test_recover_orphan
    PASS test_budget_guard
    PASS test_scope_violation
    RESULT PASS failures= 0

### 5. DeepSeek expert capability — PASS

Minimum probe:

    $env:DEEPSEEK_BASE_URL='https://api.deepseek.com/anthropic'
    dsh --profile headless "Reply with exactly OK."
    OK
    exit 0

Real analysis and takeover (T-20261024-202006-920329):

- attempts 1 and 2: simulated OpenCode failures
- attempt 5: real expert analysis, exit 0, 37 s; analysis.json contains a concrete
  root cause and a 6-step plan, including the __pycache__ risk
- attempt 3: simulated plan implementation, failed
- attempt 4: real expert direct takeover, exit 0, 51 s; it read calc.py, edited
  "return a - b" -> "return a + b", removed stale bytecode, ran the acceptance command
- final acceptance: exit 0; status succeeded

Boundary: this is a real DSH headless agent with file/terminal tools, not a plain chat
API. It runs with DSH_HOME=C:\Users\22649\.dsh and reads the DeepSeek key from the
existing Hermes .env; the key is not written to any task/log/result.

### 6. No concurrent OpenCode/expert writes — PASS

test_write_lock_blocks_second_writer: with the worktree lock held by another task, the
second writing task is blocked with lock_busy.

Real takeover event:

    2026-10-24T20:20:46  expert_takeover  opencode stopped; lock retained by 16112
    2026-10-24T20:20:46  status            executing
    2026-10-24T20:21:37  attempt           4 deepseek_expert exit 0 acceptance true
    2026-10-24T20:21:37  succeeded

OpenCode is a synchronous subprocess and had already exited; the expert ran while the
same lock was held. The lock is released only after the task reaches a terminal state.

### 7. Telegram delivery — BLOCKED

No TELEGRAM_* variables exist in Hermes .env; config.yaml has no Telegram bot section;
channel_directory.json is empty; the gateway state shows no active platforms. Sending a
test would require a bot token and an explicitly authorized chat id.

A dry-run sample was generated and logged:

    TASK SUCCEEDED
    task: T-DEMO-TELEGRAM
    status: succeeded
    phase: succeeded
    summary: Report delivery smoke test (no repository writes)
    next: none
    budget: calls=0 est=$0.00
    sent: false (dry_run)

No message was sent.

### 8. Budget / retry caps — PASS

- test_budget_guard: when model_calls reaches the cap, _check_budget raises
  BudgetExceeded and no executor runs.
- Real task T-20261024-201713-986725 hit max_model_calls_per_task=6 and was blocked
  with "runner exception: max_model_calls_per_task reached (6)"; no further request.
- test_retry_cap_stops: exactly 2 OpenCode attempts, then blocked.

Local guard only. No provider-side hard budget was configured or verified.

### 9. Diff and rollback — PASS

Real task T-20261024-201146-15d1d6:

    diff: {"added":[],"removed":[],"modified":["calc.py"]}
    --- a/calc.py
    +++ b/calc.py
    @@ -1,2 +1,2 @@
     def add(a, b):
    -    return a - b
    +    return a + b

Rollback:

    $ python run.py rollback T-20261024-201146-15d1d6 --yes
    { "actions": ["restored calc.py"] }
    $ cat calc.py
    def add(a, b):
        return a - b

### 10. Success requires acceptance — PASS

Every attempt runs all acceptance commands and records exit codes. succeeded requires
executor exit 0, no timeout, all acceptance exit 0, and no scope violation. The
false-success test proves an executor claiming success with a failing acceptance is
blocked. Real OpenCode and expert successes both show acceptance: true.

### Additional: high-risk approval gate — PASS

T-20261024-202452-7f0089, risk_level=high:

- first run -> needs_approval, exit 3, no executor attempt
- approve --by local-tester -> queued
- second run -> succeeded, attempt 1, acceptance passed

This confirms that high-risk tasks cannot execute before an explicit approval.

## Budget accounting

- OpenCode model: free (cost 0 reported by the session event). Four real OpenCode runs
  were made (two direct probes, two orchestrator tasks).
- DeepSeek expert: two calls in the successful takeover task, estimated at $0.02 each
  by policy, total estimated $0.04. Earlier failed probes add roughly $0.06 estimated.
  Actual billed amount is not available from the API response; these are estimates,
  not invoices.
- No hard provider budget exists.

## Backups and rollback of the project itself

- Hermes config, .env, SQLite/WAL, and gateway files were not modified, so no Hermes
  backup was needed. Existing backups remain untouched.
- All new code and state live under orchestration/.
- To remove the prototype: stop any running orchestrator process, then delete the
  orchestration/ directory. This does not affect Hermes, OpenCode, or DSH.
- Per-task rollback: python run.py rollback <task_id> --yes.

# Phase 2 Acceptance — Hermes and Telegram

Date: 2026-10-24 (Asia/Singapore)

## Phase 2 summary

| # | Test | Result | Evidence |
|---|---|---|---|
| 11 | Runtime data moved out of OneDrive | PASS | local runtime, row counts 5/18/59/10 match |
| 12 | Hermes read-only task | PASS | card t_4b657fab, task T-20261024-204511-c6dcd2 |
| 13 | Hermes reversible modification | PASS | card t_291e3977, task T-20261024-204801-95d848 |
| 14 | Hermes escalation with real expert | PASS | card t_46e390a7, task T-20261024-205414-f2f517 |
| 15 | Hermes approval gate | PASS | card t_0eab986b, task T-20261024-205052-9ca913 |
| 16 | Hermes cancel | PASS | card t_2f317a06, no engine task created |
| 17 | Idempotency / resume-force / cancel | PASS | 13/13 tests, duplicate card dispatch noop |
| 18 | Real OpenCode code failure | NOT REPRODUCED | OpenCode passed three real tasks, including RFC 6901 |
| 19 | Telegram real delivery | BLOCKED | hermes send has no Telegram targets; dry-run only |

## 11. Runtime migration — PASS

Destination:

    C:\Users\22649\AppData\Local\HermesOrchestrator\runtime

Migration checks and results:

- no live runner PID and no live worktree lock
- SQLite WAL checkpointed
- DB copied with SQLite backup API
- row counts before and after: tasks 5, attempts 18, events 59, budget 10
- files copied: tasks 45, results 5, logs 48
- old OneDrive copy preserved
- preflight: runtime_root exists, on_onedrive=false
- read-only attribute inherited by copytree was cleared; write probe succeeded

## 12. Hermes read-only card — PASS

    card t_4b657fab
    task T-20261024-204511-c6dcd2
    card claim -> OpenCode exit 0 -> acceptance exit 0
    card status done
    metadata result_path local runtime
    query consistent true
    second dispatch noop; second reconcile []

Comments contain orchestrator_task=T-20261024-204511-c6dcd2 and the local result path.

## 13. Hermes modification card — PASS

    card t_291e3977
    task T-20261024-204801-95d848
    diff modified calc.py
    -    return a - b
    +    return a + b
    acceptance exit 0
    card status done

Rollback restored calc.py to return a - b. The patch is under the local runtime
tasks/T-20261024-204801-95d848/attempts/01/diff.patch.

## 14. Hermes escalation card — PASS (controlled OpenCode failures, real expert)

    card t_46e390a7
    task T-20261024-205414-f2f517
    attempt 1 simulated exit 1 acceptance
    attempt 2 simulated exit 1 acceptance
    attempt 5 deepseek_expert expert_analysis exit 0
    attempt 3 simulated exit 1 acceptance
    attempt 4 deepseek_expert expert_takeover exit 0
    final succeeded; card done

analysis.json has a root cause and plan. The expert modified calc.py and acceptance
passed. The engine held the worktree lock; OpenCode had already exited.

This is not a genuine OpenCode code failure. It is a controlled-failure escalation
test with a real DeepSeek analysis and real takeover.

## 15. Approval gate — PASS

    card t_0eab986b
    task T-20261024-205052-9ca913
    risk high -> needs_approval -> card blocked
    approve --by local-tester -> queued
    hermes-dispatch -> unblock, execute, complete
    final card done, task succeeded

## 16. Cancel — PASS

    card t_2f317a06
    hermes-cancel -> card blocked, reason cancelled by user before execution
    no orchestrator task created

## 17. Idempotency and recovery — PASS

    test_idempotent_task_reuse
    test_resume_requires_force
    test_cancel_queued
    test_retry_cap_stops
    test_budget_guard
    ... 13/13 PASS

Duplicate card dispatch returned noop. Duplicate idempotency key returned the same
task id. Plain resume of a task with 2 attempts was refused; --force requeued it with
attempt_count still 2. Card cancel produced blocked without execution.

## 18. Genuine OpenCode failure — NOT REPRODUCED

Real OpenCode tasks:

- T-20261024-204511-c6dcd2 read-only verification: succeeded
- T-20261024-204801-95d848 calc.py fix: succeeded
- T-20261024-205751-f56aa6 RFC 6901 JSON Pointer from scratch: succeeded

The failure probe through card t_45928216 (task T-20261024-205751-f56aa6) also
succeeded. Therefore the real failure -> expert ladder was not observed. The
controlled-failure ladder in item 14 is the available escalation evidence. No failure
was fabricated.

## 19. Telegram — BLOCKED

    hermes send --list telegram
    -> no targets found for platform 'telegram'. Configured: (none)

Dry-run message was generated. Real send was refused because the target is not
authorized. No token or chat id is configured.

Required manual steps:

1. Put a bot token in Hermes' secure environment, not in this repo.
2. Set TELEGRAM_CHAT_ID to an authorized chat.
3. Set telegram.authorized=true and telegram.enabled=true.
4. Approve one send test.

## Phase 2 budget note

OpenCode reports cost 0 for the free model. The successful real-expert escalation
used two DeepSeek calls, estimated at $0.04 by policy. Earlier expert probes add
about $0.06 estimated. These are estimates, not provider invoices. No hard provider
budget is configured.
