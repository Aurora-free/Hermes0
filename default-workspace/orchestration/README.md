# Local Multi-Agent Orchestration

A runnable, recoverable workflow for:

- **Hermes** — Hermes Kanban is the task-state owner and queue
- **Existing engine** — the Python orchestrator in this directory executes cards
- **OpenCode** — default local executor
- **DeepSeek Harness** — diagnostic expert and direct-takeover executor
- **Telegram** — report sink, delivered through the Hermes messaging layer

The source code lives in this OneDrive directory. Runtime state (SQLite, WAL, locks,
logs, snapshots, results) lives in a local non-synced directory:

    C:\Users\22649\AppData\Local\HermesOrchestrator\runtime

Hermes SQLite is never opened or edited directly by this project; the adapter uses
the Hermes CLI only.

## Current status (2026-10-24)

| Component | Status | Evidence |
|---|---|---|
| Hermes CLI | PASS | hermes --version exit 0, Python 3.14.7 |
| Hermes dependency health | PASS | hermes pm doctor exit 0; pm repair not needed |
| Hermes Kanban adapter | PASS | card t_4b657fab -> task T-20261024-204511-c6dcd2 -> card done |
| Hermes read-only task | PASS | card t_4b657fab, OpenCode exit 0, acceptance passed |
| Hermes file modification | PASS | card t_291e3977, diff -a - b / +a + b, rollback restored |
| Hermes escalation card | PASS | card t_46e390a7, 2 controlled OpenCode failures + real expert takeover |
| Hermes approval gate | PASS | card t_0eab986b, needs_approval -> approve -> done |
| Hermes cancel | PASS | card t_2f317a06, blocked/cancelled without execution |
| Idempotent rerun | PASS | same card dispatch -> noop; duplicate task key -> same task id |
| Runtime moved out of OneDrive | PASS | C:\Users\22649\AppData\Local\HermesOrchestrator\runtime |
| Real OpenCode code failure | NOT REPRODUCED | OpenCode succeeded on three real tasks; escalation used controlled failures |
| Telegram real delivery | BLOCKED | hermes send reports no Telegram targets; dry-run only |
| Budget guard | PASS | unit test + real task stopped at max_model_calls_per_task=6 |
| Retry cap | PASS | exactly 2 OpenCode attempts, then escalate or block |
| Diff + rollback | PASS | real modification rolled back to original file |

## Architecture

    Hermes Kanban card (tenant=orchestrator, status=ready)
              |
              |  run.py hermes-reconcile / hermes-dispatch
              v
    HermesBridge: claim card, parse JSON body, link IDs
              |
              v
    Existing Orchestrator: state machine, locks, budget, snapshots
              |
              +--> OpenCode executor
              +--> DeepSeek expert (analysis, then takeover)
              |
              v
    Acceptance commands decide success
              |
              v
    Hermes card: complete + metadata, or block + reason
              |
              v
    Telegram report (Hermes send; dry-run until configured)

Hermes owns the task card and its terminal state. The engine owns execution details
(attempts, budget, diff, logs) and mirrors the final result back to the card. The
card and engine are linked by the card id, stored as hermes_task_id, and by the
orchestrator_task=... comment.

## Runtime migration and OneDrive

SQLite WAL, locks, logs, and snapshots must not live under OneDrive sync:

- OneDrive can copy WAL/SHM files mid-transaction.
- A synced lock file can appear held on another device.
- Sync can delay or revert a file while the executor has it open.

Migration performed with tools/migrate_runtime.py:

- checked no live runner PID and no live worktree lock
- checkpointed the SQLite WAL
- copied the DB with the SQLite backup API
- copied tasks, results, logs, snapshots
- verified row counts: 5 tasks, 18 attempts, 59 events, 10 budget rows
- preserved the old OneDrive copy as a backup
- wrote runtime/MIGRATION.json and RUNTIME_MOVED.json

New tasks use the local runtime automatically. The old OneDrive copy is not deleted.

## Quick start (engine)

    cd C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration

    # stable launchers use the bundled Python and need no execution-policy change
    run.cmd --json preflight
    verify.cmd

    # equivalent direct form (use the bundled Python if python is not on PATH)
    # runtime root defaults to the local non-synced directory
    python run.py init
    python run.py preflight

    # direct engine usage
    python run.py --json create --file examples\readonly-task.json
    python run.py --json run <task_id>
    python run.py --json status <task_id>
    python run.py --json report <task_id>
    python run.py rollback <task_id> --yes
    python run.py --json recover --apply
    python run.py resume <task_id> --force

## Quick start (Hermes Kanban)

Create a card from a JSON spec:

    python run.py --json hermes-create --file examples\hermes-readonly.json

Execute all ready cards with tenant=orchestrator (one pass):

    python run.py --json hermes-reconcile --limit 1

Execute one specific card (also continues an approved card):

    python run.py --json hermes-dispatch --card t_4b657fab

Query card and linked engine state:

    python run.py --json hermes-query --card t_4b657fab

Cancel a card before or during execution:

    python run.py --json hermes-cancel --card t_2f317a06

The reconcile pass is intentionally a one-shot command. It is not installed as a
Windows service, scheduled task, or background daemon in this phase.

## Layout

Source (OneDrive):

    orchestration/
      run.py
      config/policy.json
      src/orchestrator/
        models.py, db.py, lock.py, redact.py, snapshot.py, proc.py
        classify.py, runner.py, telegram.py, cli.py
        hermes_bridge.py       Hermes Kanban adapter
        executors/opencode.py, expert.py, simulated.py
      examples/                task JSON samples
      tests/                   deterministic tests
      tools/migrate_runtime.py

Runtime (local, non-synced):

    C:\Users\22649\AppData\Local\HermesOrchestrator\runtime\
      state/orchestrator.db
      tasks/<task_id>/
      results/<task_id>.json
      logs/<task_id>/
      locks/worktree.lock
      bridge/                  temporary card body files
      MIGRATION.json

## Task schema

Required fields:

| Field | Meaning |
|---|---|
| summary | human-readable task description |
| working_directory | absolute directory the executor may enter |
| allowed_paths | absolute paths the executor may modify (empty for read-only) |
| acceptance_commands | list of argv arrays (no shell strings) |
| timeout_seconds | per-executor timeout |
| risk_level | low, medium, high |
| executor | opencode, simulated, or deepseek_expert |

Optional: read_only, requires_approval, task_id, simulation, idempotency_key,
hermes_task_id. The Hermes bridge always sets hermes_task_id and an
idempotency_key of hermes:<card_id>.

A command is an argv array, never a concatenated shell string.

## State machine

queued -> running -> succeeded | failed | blocked | needs_approval | needs_expert

- needs_expert -> executing (expert takeover) -> verifying/succeeded/blocked
- blocked -> queued only through an explicit resume
- needs_approval -> queued only through approve
- Terminal: succeeded, failed, cancelled

Every transition is checked in db.transition; illegal transitions raise and are
logged. All state changes use SQLite WAL with synchronous=FULL.

## Escalation ladder

1. OpenCode attempt 1.
2. OpenCode attempt 2 with a materially different strategy.
3. DeepSeek expert analysis (once), plan saved to analysis.json.
4. OpenCode implements the plan (once).
5. DeepSeek expert direct takeover (once), holding the same worktree lock.
6. Further failure -> blocked; automation stops.

Infrastructure failures (auth, quota, network, permission, tool missing) are not
retried as code failures.

## Locks, idempotency, recovery

- One writing task at a time via locks/worktree.lock.
- OpenCode runs synchronously; it is finished before expert takeover starts.
- Card creation uses Hermes --idempotency-key.
- The engine has a unique hermes_task_id and idempotency_key; a duplicate request
  returns the existing task instead of creating another.
- A done card is a no-op; a linked blocked card is not automatically re-run.
- recover --apply marks dead in-flight tasks blocked; it does not auto-run them.
- resume refuses to requeue a task with prior attempts unless --force is given.
- Budget counters are preserved across resume, so a resume cannot bypass the cap.

## Budget and retries

config/policy.json controls:

- max_opencode_attempts (2)
- max_expert_analyses (1)
- max_expert_takeovers (1)
- max_total_attempts (4)
- max_model_calls_per_task (6)
- max_cost_usd_per_task (0.50)
- assumed_cost_per_call_usd (0.02)

The guard is checked before each paid call. Budget is recorded only after a model
call actually starts; a pre-launch exception no longer consumes a call. When the cap
is hit, the task is blocked and a budget_exceeded event/report is emitted.

This is a local guard only. No provider-side hard budget was configured or verified.
OpenCode's model reports cost 0; the $0.02 value is a bookkeeping assumption.

## Executors

OpenCode:

    opencode-cli.exe run --standalone --auto --format json \
      --model opencode/deepseek-v4.1-flash-free "<prompt>"

Only that model is configured; deepseek/deepseek-chat returns provider.no-route.

DeepSeek expert:

    dsh --profile headless --json -

The prompt is sent through stdin because dsh.cmd is a batch launcher and multi-line
argv is truncated by cmd.exe. The adapter sets DSH_HOME, the Anthropic-compatible
DeepSeek base URL, and the existing key from Hermes .env without logging it.

## Telegram

The reporter fails closed. Delivery requires all of:

- telegram.enabled = true
- telegram.dry_run = false
- telegram.authorized = true
- an authorized chat id via TELEGRAM_CHAT_ID (preferred) or policy allowed_chat_id
- a working hermes send Telegram target

Transport is hermes send --to telegram:<chat_id> --json --file -, so the bot token
stays in Hermes' existing credential store. No token is copied into this project.

Current state: hermes send --list telegram reports no targets. The dry-run sample is
in the local runtime logs/telegram-dryrun.log. No message was sent.

## Rollback

- Before each attempt: tasks/<task_id>/attempts/NN/before/.
- run.py rollback <task_id> --yes restores captured files and removes added files.
- Real test: card t_291e3977 task T-20261024-204801-95d848 changed calc.py, rollback
  restored return a - b.
- Rollback does not modify Hermes config or its database.

## What is intentionally not done

- No Hermes config, .env, SQLite/WAL, or credential was modified.
- No hermes pm repair was run; pm doctor is healthy.
- No software was installed, removed, or upgraded.
- No Telegram message was sent (no targets).
- No provider-side hard budget was configured.
- The reconcile pass is not installed as a service, scheduled task, or startup item.
- A genuine OpenCode code failure was not reproduced; all three real OpenCode tasks
  succeeded. The escalation card used controlled OpenCode failures with real expert
  analysis and real takeover.

## Next steps

1. Provide an authorized Telegram chat id and bot token, then approve one send test.
2. Decide whether the one-pass reconcile should run from Hermes cron or a service.
3. Try a harder real code task to induce a genuine OpenCode failure, or accept the
   controlled-failure escalation evidence.
4. Add actual token/cost accounting instead of the flat per-call estimate.

See PHASE2_REPORT.md, PHASE3_REPORT.md, ACCEPTANCE.md, and
FINAL_ACCEPTANCE_REPORT.md for evidence.

Phase 3 audit: the card link is now written before execution, orphan tasks block
their card instead of being re-run, and budget blocks write a structured result. A
genuine OpenCode code failure was still not reproduced; see PHASE3_REPORT.md.
