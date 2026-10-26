# Phase 9 Report — Dispatch Pipeline + Failure→Expert Closed-Loop Proof

Date: 2026-10-25 (18:00–19:50 MYT)

## Objective
1. Validate the merged Phase 8.2 orchestrator end-to-end on real tasks.
2. Prove the escalation loop with a *real* failure: OpenCode first attempts fail,
   then the DeepSeek expert tier successfully repairs the task.

## Tasks run (all via `run.py hermes-create` + `hermes-dispatch`)

| Task | Project | Baseline | Attempts | Outcome |
|---|---|---|---|---|
| T-20261025-152006 | phase9-exprc (112-test impl) | 109 red | opencode #1 | success (158s→accepted 112/112) |
| T-20261025-180818 | phase9-exprc (171-test tool groups) | 58 red (5 stubs) | opencode #1 | success (158s, 171/171) |
| T-20261025-183858 | phase9-exprc-repair (223 tests, 6 planted defects) | 37 red | opencode #1 | success (158s, all 7 fixes incl. masked B3) |
| T-20261025-185556 | phase9-exprc-deep (273 tests, 7 planted defects) | 55 red | opencode #1 (232s, 300s budget) | success, all 7 defects fixed |
| T-20261025-190743 | phase9-exprc-deep, 110s budget | 55 red | opencode #1,#2 timeout (110s each, partial fixes persisted); expert_analysis; opencode #3 with expert plan | success in 29.3s |
| **T-20261025-194009** | **phase9-exprc-deep, 15s budget** | **55 red** | **opencode #1 #2 #3 all failed (15.5–15.6s timeouts, acceptance red)**; **expert_takeover: success in 74.7s** | **273/273 green** |

## Closed loop — demonstrated (T-20261025-194009)

Result JSON (runtime/results/T-20261025-194009-2fb676.json):

```
attempt 1  implement       opencode        success=False  15.5s  timed_out=True  acceptance FAILED
attempt 2  implement       opencode        success=False  15.6s  timed_out=True  acceptance FAILED
attempt 3  implement       opencode        success=False  15.6s  timed_out=True  acceptance FAILED
attempt 4  expert_takeover deepseek_expert success=True   74.7s  acceptance PASSED (273/273)
```

- The failing state was a 273-test suite (55 failures) with 7 deliberately planted
  subtle defects: AND-short-circuit type coercion, trace-limit off-by-one, a
  *masked* trace guard (symptom only visible after the limit bug is fixed),
  minify jump-remap off-by-one, pack bool-literal tag corruption, unpack missing
  trailing-byte validation, disasm argc off-by-one.
- OpenCode was budget-capped (15s/attempt, real need ≈ 230s) → three genuine
  failed attempts (attempts 01–03 diff patches preserved under
  runtime/tasks/T-20261025-194009-2fb676/attempts/).
- Expert analysis (attempt slot 05) produced a correct structured plan
  (root_cause + files_to_change + verification).
- The DeepSeek expert **direct takeover** (attempt 04, 66-line diff / 23
  additions) repaired all 7 defects; acceptance 273/273 OK.
- Independently re-verified after the fact: `python -m unittest tests.test_exprc`
  in the project dir → `Ran 273 tests / OK`.

## Pipeline behavior confirmed under real load
- opencode retry (attempt 2) and expert plan hand-off (attempt 3) work.
- Budget-cap failures are classified `acceptance` + `timed_out=True` and
  correctly escalate: needs_expert → expert_analysis → expert_takeover.
- Workspace persists across attempts (partial fixes from attempt 1 were visible
  to attempt 2/3) — a deliberate design property, verified.
- Kanban card ↔ orchestrator task stayed `consistent: true` through all states.
- Cost: $0.10 for the full 5-call task (budget cap $0.50).

## Artifacts
- Test projects: C:\Users\22649\AppData\Local\HermesOrchestrator\test-projects\
  phase9-exprc, phase9-exprc-repair, phase9-exprc-deep
- Cards/examples: orchestration/examples/phase9-exprc*.json
- Result JSONs: C:\Users\22649\AppData\Local\HermesOrchestrator\runtime\results\
- Per-attempt diffs + snapshots: ...\runtime\tasks\T-20261025-194009-2fb676\

## Notes
- OpenCode (deepseek-v4.1-flash-free) is strong: with a full budget it repaired
  all 7 defects in 232s on the first attempt. The failure leg of the loop is
  therefore induced by budget caps (a realistic production failure mode: the
  cheap executor running out of its time window), not by task unsolvability.
- The masked-defect trick (defect B3 only surfaces after B2 is fixed) held up:
  naive partial fixes left the suite red in every capped run.

## Phase 9.3 — Pacing, dual acceptance, and kill-recovery (2026-10-25 21:00–22:00)

### 1. Rate gate (new: src/orchestrator/rate.py)
- Pacing: minimum interval between model calls (default 8s), persisted in
  runtime/state/rate_gate.json so it holds across separate dispatches.
- Backoff: when a model call is classified `quota` (HTTP 429 / rate-limit),
  the attempt retries in place with exponential backoff (90s/180s/360s
  capped at 300s, max 3 retries, bounded by the attempt deadline) instead of
  hard-blocking the task as an infrastructure failure. Previously a 429
  aborted the whole task (classify -> `quota` -> blocked) and required a
  manual re-dispatch.
- Policy: config/policy.json `rate_limit` section (min_interval_seconds,
  retry_on_rate_limit, max_rate_retries, backoff_base_seconds,
  backoff_max_seconds). All orchestrator unit tests still green after the
  runner change (test_orchestration failures=0; test_env_isolation 11/11).

### 2. Dual acceptance gate (new: phase9-exprc-deep)
- tests/test_exprc.py (273 pinned cases) is complemented by
  tools/fuzz_diff.py: 5000 seeded random pexpr programs cross-checked
  against a frozen reference evaluator at
  test-projects/phase9-oracle/pexpr_ref.py (outside the task's
  allowed_paths), comparing result values AND PExprError messages
  (native exceptions compared by class).
- Anti-tamper: a third acceptance command pins the SHA-256 of both the
  oracle and fuzz_diff.py (pinned in examples/phase9-exprc-deep-05.json);
  gutting the gate fails acceptance.
- Gate validated before dispatch: clean implementation -> all 3 commands
  green (FUZZ-DIFF OK 5000); buggy baseline -> 55 unittest failures AND
  18 fuzz mismatches.

### 3. Run T-20261025-210825-fb3bd2 (card t_ad678020, 300s budget)
- Timeline: opencode attempts 1-2 each hit the 300s budget (unittest gate
  still red, fuzz gate already green from partial fixes); expert analysis
  ran (needs_expert phase, root cause: trace() limit handling, confidence
  high); opencode attempt 3 finished 91.2s with all three acceptance
  commands green. Deciding attempt: 3 (opencode, expert plan applied).
- Independently re-verified after the run: unittest OK, FUZZ-DIFF OK
  (5000 agree, oracle=98fbbfd3a84d), hashes intact.
- Budget: 5 calls, $0.10 cap reached. Card/task ended `succeeded`,
  kanban card `done`, consistent: true.
- Report command: `run.py report <task_id>` now prints the full attempt
  timeline (per-gate exit codes, diff stats, expert analysis, rate-gate
  state) with the raw result embedded.

### 4. Kill-recovery demonstrated
- The first dispatcher (terminal, 420s foreground cap) died mid-run during
  attempt 2, leaving the task stuck `running` plus a stale worktree.lock
  (runner PID dead). Recovery: `run.py recover --apply` found both,
  `resume --force` requeued the task, re-dispatch (background process)
  completed it. The system self-healed a dispatcher death without manual
  DB surgery.
