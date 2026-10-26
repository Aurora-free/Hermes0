# Phase 8.2 凭据隔离 — 生产合并记录

合并时间：2026-10-25 11:47 (UTC+8)
来源副本：`C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-2-copy\orchestration`

## 合并前完整性核验

- 8 个源码文件：在生产源码上 dry-run 应用 `phase8_2_changes.diff`，结果与副本文件逐一 `diff` 全部 MATCH（copy == production + diff）。
- 其中 6 个文件（config/runner/telegram/hermes_bridge/opencode/expert）与 `phase8-copy\orchestration\original` 备份逐字节一致；redact.py、proc.py 不在原备份中，通过 diff 应用结果核验。

## 已合并（12 个文件）

```text
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
```

## 未合并（按要求排除）

`config/policy.json`（mtime 2026-10-24 20:40 未变）、`phase8_2_changes.diff`、副本 `runtime/`、副本 `test-runs/`、`make_diff.py`。

## 备份

- 合并前生产 `src/` + `tests/` 完整备份：
  `C:\Users\22649\AppData\Local\HermesOrchestrator\phase8-2-copy\orchestration\pre-merge-prod-backup-20261025-114740`
- 更早的原始备份保留在 `phase8-copy\orchestration\original`。

## 生产验证结果（合并后）

```text
python -m unittest tests.test_env_isolation -v   11/11 PASS
cmd /c verify.cmd                                  13/13 PASS (failures=0, run-20261025-114813)
cmd /c verify.cmd (PYTHONDONTWRITEBYTECODE=1)      13/13 PASS (failures=0, run-20261025-114835)
python -m compileall -q src run.py                 exit 0
grep 残留 env_with_secrets / os.environ.copy()     src 内 0 处
```

## 仍未验证 / 开放项（本次合并不涵盖）

1. 真实环境兼容：`PUBLIC_ENV_ALLOWLIST` 不含 `HTTP_PROXY` / `HTTPS_PROXY` / `SSL_CERT_FILE`；若目标环境需要代理或自定义证书，需显式加入并审查。
2. 真实 `hermes send` 在仅公共环境下能否读取自身 `.env`：未实测。
3. OS 级隔离：未实现（仍为应用层路径检查 + 快照/回滚 + 预算）。
4. 真实失败后专家成功修复：仍无成功证据（BLOCKED 路径已验证）。
5. Telegram：未配置、未发送。
6. 常驻调度：未安装。

## 说法更正

本记录之后，"生产未修复凭据隔离"不再成立——生产源码已含 Phase 8.2 修复并通过上述本地验证。但上表 1–6 的开放项仍不得宣称完成。
