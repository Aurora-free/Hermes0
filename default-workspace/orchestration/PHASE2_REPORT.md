# Phase 2 Report — Hermes Integration and Telegram

Date: 2026-10-24 (Asia/Singapore)
Host: Windows 11, desktop-4krb498\22649
No credential values are reproduced in this report.

## 1. What changed

### Runtime moved out of OneDrive

Runtime data is now at:

    C:\Users\22649\AppData\Local\HermesOrchestrator\runtime

The source remains in the OneDrive orchestration directory. The migration tool:

1. refused to run while a runner PID or worktree lock was alive
2. checkpointed the SQLite WAL
3. copied the DB with the SQLite backup API
4. copied tasks, results, logs, snapshots
5. verified row counts exactly: 5 tasks, 18 attempts, 59 events, 10 budget rows
6. preserved the old OneDrive copy as a backup
7. wrote MIGRATION.json and RUNTIME_MOVED.json

One issue found and fixed: shutil.copytree preserved the source read-only directory
attribute. The runtime directories were made writable, and a write probe succeeded.
Preflight now reports runtime_root on_onedrive=false.

### Hermes Kanban adapter

New file: src/orchestrator/hermes_bridge.py

Design:

- Hermes Kanban is the canonical task queue and terminal-state owner.
- Cards are created with --tenant orchestrator and --idempotency-key.
- A ready card is claimed, its JSON body is parsed as the task spec, and the existing
  engine is invoked. OpenCode and DeepSeek calls are not reimplemented.
- The engine task stores the card id as hermes_task_id; the card stores
  orchestrator_task=<id> as a comment.
- On success the card is completed with metadata: orchestrator id, local result path,
  attempt count, model calls, estimated cost, card id.
- On blocked/needs_approval/cancelled the card is blocked with a typed reason.
- Rerunning a done card is a no-op. A linked blocked card is not auto-rerun.
- No direct Hermes SQLite write; only hermes CLI calls.

New commands:

    python run.py --json hermes-create --file <spec.json>
    python run.py --json hermes-reconcile --limit 1
    python run.py --json hermes-dispatch --card <card_id>
    python run.py --json hermes-query --card <card_id>
    python run.py --json hermes-cancel --card <card_id>

### Engine hardening

- runtime_root separation in config and CLI.
- hermes_task_id and idempotency_key columns, unique indexes, and lookup methods.
- create_task returns the existing task for duplicate hermes id or idempotency key.
- resume now refuses tasks with prior attempts unless --force is used; attempt and
  budget counters are preserved.
- cancel added for queued/blocked/needs_approval tasks; active runners are not killed.
- Budget is recorded only when a model call actually started; pre-launch exceptions no
  longer consume calls. BudgetExceeded now emits a budget_exceeded event/report.
- max_total_attempts check uses refreshed state.

## 2. Verified results

### 2.1 Hermes read-only task — PASS

Card t_4b657fab, task T-20261024-204511-c6dcd2.

    hermes-create  -> card ready
    hermes-reconcile -> claimed, OpenCode exit 0, acceptance exit 0
    card status: done
    card result: orchestrator succeeded: T-20261024-204511-c6dcd2
    metadata result_path:
      C:\Users\22649\AppData\Local\HermesOrchestrator\runtime\results\T-20261024-204511-c6dcd2.json
    query consistent: true

Second dispatch returned status=noop (card is done). Second reconcile returned [].

### 2.2 Hermes reversible modification — PASS

Card t_291e3977, task T-20261024-204801-95d848.

    diff: {"added":[],"removed":[],"modified":["calc.py"]}
    --- a/calc.py
    +++ b/calc.py
    -    return a - b
    +    return a + b
    acceptance exit 0
    card status: done

Rollback:

    python run.py rollback T-20261024-204801-95d848 --yes
    { "actions": ["restored calc.py"] }
    calc.py -> def add(a, b): return a - b

### 2.3 Hermes escalation — PASS (controlled OpenCode failures, real expert)

Card t_46e390a7, task T-20261024-205414-f2f517.

    attempt 1  simulated  exit 1  acceptance
    attempt 2  simulated  exit 1  acceptance
    attempt 5  deepseek_expert  expert_analysis  exit 0
    attempt 3  simulated  exit 1  acceptance
    attempt 4  deepseek_expert  expert_takeover  exit 0
    final status: succeeded
    card status: done

analysis.json contains a concrete root cause and a plan. The takeover modified
calc.py to return a + b and acceptance passed. Only one writer was active: the engine
held worktree.lock and OpenCode had already exited before takeover.

This proves the Hermes -> engine -> expert -> acceptance -> card path. It does not
prove a genuine OpenCode code failure; those failures were controlled.

### 2.4 Genuine OpenCode failure attempt — NOT REPRODUCED

Three real OpenCode tasks succeeded:

- T-20261024-204511-c6dcd2 read-only verification
- T-20261024-204801-95d848 calc.py fix
- T-20261024-205751-f56aa6 RFC 6901 JSON Pointer resolver from scratch

The failure probe through card t_45928216 (task T-20261024-205751-f56aa6) also
succeeded: OpenCode implemented the resolver correctly and acceptance passed. No
genuine OpenCode code failure occurred, so no real failure -> expert ladder was
triggered. This remains an open verification item; no failure was fabricated.

### 2.5 Approval gate through Hermes — PASS

Card t_0eab986b, task T-20261024-205052-9ca913.

    risk_level=high -> task needs_approval -> card blocked
    python run.py approve T-... --by local-tester -> queued
    python run.py hermes-dispatch --card t_0eab986b
      -> card unblocked, executed, completed
    final card: done, task: succeeded

### 2.6 Cancel through Hermes — PASS

Card t_2f317a06, created but not executed:

    python run.py hermes-cancel --card t_2f317a06
    card status: blocked
    reason: cancelled by user before execution
    no orchestrator task created

### 2.7 Idempotency and recovery — PASS

- Unit test test_idempotent_task_reuse: same idempotency key returns same task id,
  only one task row.
- Unit test test_resume_requires_force: a blocked task with 2 attempts refuses plain
  resume; --force returns queued with attempt_count still 2.
- Unit test test_cancel_queued: cancel -> cancelled, run is a no-op.
- Card re-dispatch: noop; reconcile: empty.
- recover --apply still marks dead in-flight tasks blocked; it never auto-runs.

Regression suite: 13/13 PASS.

### 2.8 Budget — PASS

- Budget is checked before each paid call.
- Budget is recorded only when a model call actually started.
- test_budget_guard passes.
- A previous real task hit max_model_calls_per_task=6 and stopped; that evidence is
  retained in the migrated DB.
- BudgetExceeded now produces a distinct budget_exceeded event/report.

Estimated cost is not a bill. OpenCode reports cost 0 for the free model. DeepSeek
calls are estimated at $0.02 each by policy. The successful escalation card used two
DeepSeek calls, estimated $0.04; the provider invoice is not available.

## 3. Telegram

Status: IMPLEMENTED, REAL DELIVERY BLOCKED.

What works:

- Reporter builds messages for task_created, task_succeeded, task_blocked,
  expert_analysis, expert_takeover, approval_required, budget_exceeded.
- Messages include task id, Hermes card id, status, phase, summary, reason/next,
  and call estimate. They are redacted.
- Transport is Hermes send, so the bot token stays in Hermes' credential store.
- The reporter fails closed if not enabled, not authorized, no chat id, or no target.
- Dry-run sample was generated.

Current blocker:

    hermes send --list telegram
    -> no targets found for platform 'telegram'. Configured: (none)

No TELEGRAM_BOT_TOKEN or chat id is configured in Hermes .env or config.yaml. No
message was sent.

Required manual steps (not performed):

1. Configure a Telegram bot token in Hermes' secure environment (not in this repo,
   not in task JSON, not in policy.json).
2. Set TELEGRAM_CHAT_ID to an explicitly authorized chat id, or set
   telegram.allowed_chat_id in local policy.
3. Set telegram.authorized=true and telegram.enabled=true.
4. Run telegram-check, then one approved dry-run/real send test.

The project has not created a bot account or installed any Telegram package.

## 4. Files changed or added

Source:

- README.md
- PHASE2_REPORT.md
- config/policy.json
- src/orchestrator/config.py
- src/orchestrator/models.py
- src/orchestrator/db.py
- src/orchestrator/runner.py
- src/orchestrator/cli.py
- src/orchestrator/telegram.py
- src/orchestrator/hermes_bridge.py (new)
- src/orchestrator/executors/base.py
- src/orchestrator/executors/opencode.py
- src/orchestrator/executors/expert.py
- tests/test_orchestration.py
- tools/migrate_runtime.py (new)
- run.cmd, verify.cmd (stable launchers, no execution-policy change)
- examples/hermes-*.json (new)

Runtime (not in the repo):

- C:\Users\22649\AppData\Local\HermesOrchestrator\runtime\state\orchestrator.db
- tasks/, results/, logs/, locks/, bridge/, MIGRATION.json

Preserved backup:

- The original OneDrive runtime directories remain in orchestration/ (state, tasks,
  results, logs) with RUNTIME_MOVED.json.

## 5. Test commands and observed exit codes

    python -m compileall -q src run.py tools            exit 0
    python tests\test_orchestration.py                  13 PASS, exit 0
    python run.py --json preflight                      exit 0
    python run.py --json hermes-reconcile --limit 1     exit 0 (completed)
    python run.py --json hermes-query --card t_4b657fab exit 0, consistent=true
    python run.py --json hermes-dispatch --card t_4b657fab  exit 0, noop
    python run.py --json telegram-check                 exit 0, ok=false (no targets)
    python run.py --json telegram-test                  exit 0, sent=false dry_run
    python run.py --json telegram-test --send           exit 2, unauthorized

## 6. Status classification

Actually verified:

- runtime migration and local DB access
- Hermes card create/claim/complete/block/comment/query
- Hermes read-only execution
- Hermes reversible modification and rollback
- Hermes -> real expert analysis + takeover (controlled OpenCode failures)
- approval gate and cancel
- idempotent card/task handling
- budget guard, retry cap, resume-force, recovery

Implemented but not end-to-end verified:

- real Telegram delivery
- budget_exceeded Telegram delivery
- automatic Hermes scheduling (reconcile is a manual one-pass command)

Blocked / open:

- genuine OpenCode code failure and the full real failure ladder
- Telegram token and authorized chat id

Manual input or approval needed:

- Telegram bot token and chat id (provide through secure env, not chat)
- approval of one real Telegram send
- decision on whether to install a recurring reconcile schedule

## 7. Rollback

- Code: revert the listed source files; the old OneDrive runtime copy remains.
- Runtime: old OneDrive state/tasks/results/logs were preserved. To revert the runtime
  location, stop all runners, remove the local runtime directory, and point
  runtime_root back to the orchestration directory. Do not move SQLite while a runner
  is active.
- Per-task: python run.py rollback <task_id> --yes.
- Hermes cards: block/archive via hermes kanban, or keep the completed cards as audit
  evidence.

## 8. Next steps

1. Configure an authorized Telegram chat and approve one send.
2. Decide whether to run hermes-reconcile from Hermes cron or a service.
3. Design a harder real code task to induce a genuine OpenCode failure.
4. Replace the flat per-call estimate with actual provider usage where available.
