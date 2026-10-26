# Phase 7 Isolation Assessment

Date: 2026-10-24 (Asia/Singapore)
Project: C:\Users\22649\OneDrive\文档\deepseek-harness\default-workspace\orchestration
Runtime: C:\Users\22649\AppData\Local\HermesOrchestrator\runtime
Scope: read-only assessment. No model calls, no software installation, no Windows
feature enablement, no system-policy change, no source change, no service/task.

## 1. Executive conclusion

The current system has no OS-level execution isolation for OpenCode, DeepSeek, or
acceptance commands. It has application-layer path validation, snapshots, post-hoc
diff checks, rollbacks, locks, and budget gates. Those are useful safety controls but
are not a sandbox.

Recommended staged approach:

1. Near-term: a dedicated low-privilege Windows user + ACL-scoped task workspace +
   Job Object + credential broker. This is OS-enforced, compatible with the current
   host, and does not require a reboot or a guest OS.
2. High-assurance: a Hyper-V VM for executor processes when the threat model includes
   malicious model/tool behavior. Requires admin, feature enablement, reboot, guest
   provisioning, and more RAM/ops.
3. Windows Sandbox: useful for manual validation, not for unattended per-task
   automation.
4. AppContainer: technically promising for lightweight per-task isolation, but needs
   a custom launcher/prototype and compatibility testing before use.
5. Restricted token / low integrity alone: not sufficient and not currently
   launchable without elevation.

## 2. Current application-layer boundary

### Enforced in application code

- create_task resolves each allowed_path and rejects paths outside working_directory
  (runner.py lines 116-128).
- A read-only task is failed on any changed file.
- Each attempt snapshots working_directory, then diffs and hashes files
  (snapshot.py scan/save; runner.py lines 431-443).
- Changes inside working_directory but outside allowed_paths become scope_violation
  and are rolled back.
- One writing task at a time via a one-byte msvcrt lock (lock.py).
- Budget is checked before each model launch (runner.py lines 495-505).
- Task/card idempotency, blocked states, and result files are implemented.

### Not enforced by Windows

- There is no restricted token, AppContainer, VM, or file ACL around the executor.
- OpenCode runs with --auto as the current user and can write anywhere that user can.
- The DeepSeek expert is a real local agent with file/terminal tools.
- Acceptance commands run as the current user; they are not sandboxed either.
- The snapshot covers only working_directory.
- Writes outside working_directory are neither detected nor rollback-able.
- Snapshot limits are 3000 files / 80 MB; excluded dirs are not diffed. A truncated
  snapshot is not treated as a hard failure.
- proc.run_capture creates a new process group and kills the tree on timeout, but no
  Job Object is assigned. If the orchestrator process dies, a child can survive.
- WorktreeLock is advisory to cooperating processes; it does not stop a malicious
  child from ignoring it.

### Test-file read-only attribute

Some phase 5/6 tests were marked read-only and hashed before/after. That is OS-enforced
for the file attribute only; the same user can clear it. It is not an executor sandbox.

### Credential propagation risk

env_with_secrets loads an allowlist including DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
and TELEGRAM_BOT_TOKEN. OpenCodeExecutor and DshHeadlessExpert copy that whole
environment into their child processes. Therefore OpenCode and DSH currently inherit
the Telegram token and the DeepSeek key even when they do not need them. Acceptance
commands filter two names from os.environ, but the executor children do not.
This must be fixed with a per-executor environment allowlist before any isolation work.

## 3. Host facts verified read-only

### OS and hardware

- systeminfo: Microsoft Windows 11 Pro, version 10.0.26300, x64.
- Get-ComputerInfo reports Windows 10 Pro product name; the build and systeminfo
  identify Windows 11 Pro. Use the build/systeminfo result.
- 8 logical processors, 8 GB RAM, 76.8 GB free on C:.
- HyperVisorPresent=True; HvHost service is running (VBS/hypervisor support).

### Virtualization tools/features

- WindowsSandbox.exe: not found.
- vmcompute.exe / vmms.exe: not found.
- Hyper-V PowerShell module: not listed.
- WSL: not installed.
- VirtualBox / Docker: not found.
- Get-WindowsOptionalFeature for Windows Sandbox and Hyper-V-All requires elevation;
  exact feature state could not be read without admin.
- Interpretation: a hypervisor is present, but Windows Sandbox and Hyper-V management
  are not currently available as installed features/tools. Enabling either requires
  elevation and likely reboot.

### Identity and privileges

- User: desktop-4krb498\22649.
- Member of Administrators, but the token shows Administrators as deny-only:
  this is a filtered/UAC medium-integrity token.
- Integrity: Medium.
- Privileges: SeShutdown, SeChangeNotify, SeUndock, SeIncreaseWorkingSet,
  SeTimeZone. No SeImpersonate, SeAssignPrimaryToken, SeDebug, SeBackup,
  SeTakeOwnership, or SeIncreaseQuota.
- New-LocalUser and Add-LocalGroupMember exist, but require elevation.
- Existing local users: CodexSandboxOffline and CodexSandboxOnline (enabled),
  WDAGUtilityAccount (disabled), Administrator (disabled).
- The CodexSandbox users are not ours; credentials/purpose are unknown. Do not reuse
  or modify them without owner approval.

### Filesystem and ACLs

- Runtime, OpenCode data, and .dsh are owned by the current user with Full Control.
- C:\Users\22649 grants Full Control only to SYSTEM, Administrators, 22649, and one
  AppContainer SID. A new local user would not automatically read the profile.
- OpenCode CLI is under AppData\Roaming; DeepSeek CLI and Python are under
  AppData\Local. A low-privilege user would need explicit Read+Execute grants on those
  install trees.
- Hermes .env and .dsh are not readable by a new local user by default; that is good,
  but the executor still needs a key injection path.
- A temporary il-probe directory under test-projects was created and removed solely
  to verify that icacls can set a Low integrity label. No persistent ACL or system
  change remains.

### Network

- api.deepseek.com:443 reachable.
- opencode.ai:443 reachable.
- No proxy environment variables.
- No egress firewall/allowlist is configured for executor processes.

## 4. Dependencies an isolated executor must retain

OpenCode:

- opencode-cli.exe and its read/execute dependencies.
- A writable HOME/XDG data directory for logs, DB, cache (currently
  C:\Users\22649\.local\share\opencode).
- Network access to its provider.
- No DeepSeek key or Telegram token is required for its current free model.

DeepSeek expert:

- dsh.cmd and the DeepSeek Harness resources.
- DSH_HOME with sessions/config; currently C:\Users\22649\.dsh.
- DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL; key currently read from Hermes .env by the
  orchestrator and passed via environment.
- Network to api.deepseek.com:443 (Anthropic-compatible endpoint).

Acceptance commands:

- The bundled Python interpreter and project dependencies.
- Read access to the task project and test files.
- Writable temp directory, but no write access outside the project/temp.

Orchestrator/trusted side:

- Runtime DB, logs, snapshots, locks, results.
- Hermes CLI for card state; this should stay outside the untrusted executor.
- Telegram reporter; no token in executor env.

## 5. Option comparison

| Option | OS enforcement | Compatibility | Setup | Runtime cost | Rollback | Automation | Verdict |
|---|---|---|---|---|---|---|---|
| Current app-layer | No | High | None | Low | Per-task | High | Not isolation |
| Dedicated low-priv user + ACL + Job | Yes (ACL/token) | High | Admin once | Low | Remove user/ACL | Good with broker | Recommended near-term |
| Hyper-V VM | Yes (VM boundary) | Medium | Admin/reboot/guest | High (RAM/CPU) | Delete VM/checkpoint | Good with agent | Recommended high-assurance |
| Windows Sandbox | Yes (VM-like) | Medium | Admin/reboot | High startup | Disable feature | Poor/medium | Manual validation |
| AppContainer | Yes (package SID) | Low/medium | Custom launcher | Low | Delete profile/ACL | Good if built | Prototype later |
| Restricted token/low IL | Partial | Medium | Privileges | Low | Remove ACL | Medium | Not sufficient |
| WSL2 | Partial/VM-like | Medium | Not installed | Medium | Delete distro | Medium | Not recommended |

### Current app-layer

Pros: already implemented, no setup, fast, compatible.
Cons: no OS boundary; untrusted code runs as the user; path checks and rollback are
post-hoc; outside-working-directory writes are invisible.

### Dedicated low-privilege Windows user + ACL + Job Object

Pros:

- OS-enforced file ACLs; separate user profile; no access to 22649's .env/.dsh by
  default.
- Same Windows, no guest OS, no reboot; compatible with OpenCode/DSH/Python if
  Read+Execute is granted to tool directories.
- Job Object can kill the process tree on orchestrator death or timeout.
- Lower RAM cost than a VM.

Cons:

- Requires admin to create the user, set ACLs, firewall rules.
- Requires a credential broker (CreateProcessWithLogonW or Credential Manager/DPAPI).
  Storing a local password is a security tradeoff.
- The user can read world-readable files; ACLs must be narrow.
- Tool updates may change ACL needs.
- Network egress should be constrained by firewall.

### Hyper-V VM

Pros:

- Strongest practical boundary; guest cannot see host files except explicitly shared
  folders.
- Snapshots/checkpoints make rollback and crash recovery clean.
- Good for a persistent executor service.

Cons:

- Requires Hyper-V feature/admin/reboot; management tools currently absent.
- Needs a Windows guest license/image, guest tools, provisioning, patching.
- 8 GB host RAM is tight; a 4 GB guest plus host is possible but not generous.
- More operational complexity and startup cost.

### Windows Sandbox

Pros:

- Strong disposable environment; no persistent changes.
- Easy manual testing of untrusted code.

Cons:

- Feature absent/needs elevation and reboot.
- Not designed for unattended per-task automation; no simple exit-code/result channel.
- Tools must be installed/copied per run; secrets mapping is awkward.
- 8 GB host RAM is a constraint.

### AppContainer

Pros:

- Per-package SID; OS file isolation; lightweight compared with a VM.
- No separate user account if the launcher is built correctly.

Cons:

- No AppContainer cmdlets found; requires a custom C#/ctypes launcher and profile
  management.
- Network capabilities must be granted explicitly (internetClient).
- OpenCode/DSH compatibility with AppContainer, child processes, temp, and GUI/console
  behavior is unverified.
- Debugging and rollback are more complex than a low-priv user.

### Restricted token / low integrity

- runas /trustlevel creates a restricted token but does not scope file access to a
  project.
- icacls can set a Low integrity label, but the current token lacks the privileges
  needed to launch a low-integrity/AppContainer process reliably.
- Not sufficient as a primary solution.

## 6. Recommended implementation

### 6.1 Near-term recommended: dedicated low-privilege user + ACL + Job Object

Architecture:

    Trusted orchestrator (current user)
      -> creates per-task workspace under a sandbox root
      -> sets ACLs for sandbox user (Modify only there)
      -> launches executor as sandbox user via broker
      -> Job Object with KILL_ON_JOB_CLOSE
      -> captures stdout/stderr/exit, kills tree on timeout
      -> acceptance runs in the same sandbox user/workspace
      -> orchestrator collects diff/rollback from host side

Steps:

1. Approval: create a local user, e.g. HermesExec, not in Administrators.
2. Store its password in Windows Credential Manager or DPAPI; do not put it in task
   JSON, logs, Git, or reports.
3. ACLs:
   - Task workspace: Modify for HermesExec.
   - Sandbox temp/home: Modify for HermesExec.
   - OpenCode/DSH/Python install dirs: Read+Execute for HermesExec.
   - C:\Users\22649, .dsh, hermes .env: no access for HermesExec.
   - C:\Windows\Temp: no write.
4. Broker:
   - Use CreateProcessWithLogonW or a dedicated service account with a stored
     credential.
   - Assign a Job Object with KILL_ON_JOB_CLOSE.
   - Pass only a per-executor environment allowlist.
   - Set HOME/USERPROFILE/TEMP to the sandbox user's per-task directories.
5. Environment split:
   - OpenCode: no DeepSeek key, no Telegram token.
   - DeepSeek: DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL only.
   - Acceptance: no secrets.
   - This requires changing the current executor env handling, which currently passes
     the whole orchestrator env.
6. Network:
   - Windows Firewall outbound rules for HermesExec to api.deepseek.com:443 and the
     OpenCode provider endpoints:443.
   - Block other outbound traffic from HermesExec unless explicitly allowed.
7. Verification:
   - See section 7.
8. Rollback:
   - Remove firewall rule, ACLs, stored credential, and local user.
   - Restore the previous executor launch path.

Why recommended first:

- It is a real OS boundary (ACL/token), not just path checks.
- It fits Windows 11 Pro without enabling Hyper-V/Sandbox or rebooting.
- It can run the existing binaries with read-only grants.
- It is lower cost than a VM and easier to roll back.

### 6.2 High-assurance alternative: Hyper-V VM

Use when the threat model includes a compromised model/tool that may attempt arbitrary
host access, or when regulatory isolation is required.

Steps:

1. Admin enables Hyper-V and management tools; reboot.
2. Create a Windows guest; install OpenCode/DSH/Python and dependencies.
3. Provide only a shared project folder; no host C: drive.
4. Use PowerShell Direct or a guest agent to launch commands and capture results.
5. Snapshot the clean VM; revert after each task or on failure.
6. Inject the DeepSeek key in guest memory/environment only; do not write it to a
   shared folder.
7. Firewall/NAT allowlist for the guest.
8. Verify with the same probes, from inside the guest.

Rollback: delete VM/checkpoints, remove the shared folder, optionally disable Hyper-V.

### 6.3 Windows Sandbox

Use only for manual forensic validation:

- Enable feature/admin/reboot.
- Create a .wsb mapping the project read-write and a tool bundle read-only.
- Use LogonCommand to run a probe.
- Do not use for unattended task execution until a result/exit-code channel is proven.

### 6.4 AppContainer

Prototype only after low-priv user or VM:

- Build a launcher using CreateAppContainerProfile and
  PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES.
- Grant the package SID access to the task workspace and internetClient capability.
- Test OpenCode, DSH, Python, child processes, temp, and console behavior.
- If compatibility fails, fall back to the low-priv user.

## 7. Isolation acceptance probes

Run these before trusting any executor isolation:

1. Identity:
   - whoami /user and whoami /groups inside the executor.
   - Confirm the SID is the sandbox user, not 22649.
2. Inside project:
   - Create and delete a file in the task project; expect success.
3. Outside project:
   - Attempt to create
     C:\Users\22649\AppData\Local\HermesOrchestrator\isolation-probe.txt.
   - Expect Access Denied; confirm no file exists afterward.
4. Secrets:
   - Attempt to read C:\Users\22649\AppData\Local\hermes\.env and
     C:\Users\22649\.dsh\.credentials.yaml.
   - Expect Access Denied.
5. System temp:
   - Attempt to write C:\Windows\Temp\isolation-probe.txt; expect Access Denied.
6. Child process:
   - Have OpenCode spawn a child; inspect its token/SID or run the same probe from it.
7. Network:
   - api.deepseek.com:443 and the OpenCode provider:443 succeed.
   - An unapproved endpoint fails if firewall rules are used.
8. Timeout/crash:
   - Kill the orchestrator during a long executor run; Job Object must kill the child.
9. Budget:
   - Precharge the task to the cap; confirm no process is launched.
10. Acceptance:
   - Read-only task and reversible modify task; diff and rollback still work.

Evidence to store:

- exit codes, stdout/stderr, probe file existence, ACL dumps, whoami output,
- process tree after kill, task status/card status, budget records.

## 8. Key, network, and failure handling

Keys:

- Do not copy .env or .dsh credentials into a task workspace or a VM shared folder.
- Inject only the needed variables into the sandbox process.
- OpenCode should receive no DeepSeek key and no Telegram token.
- DeepSeek should receive only DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL.
- Acceptance commands should receive no secrets.
- Store the sandbox user's password in Credential Manager/DPAPI, not in Git/logs.

Network:

- Allow api.deepseek.com:443 and the OpenCode provider endpoints.
- Block other egress from the sandbox account/VM unless explicitly required.
- Keep the Hermes CLI/card bridge on the trusted side.
- Do not expose the Host's loopback services.

Failure:

- Budget is checked before launching the sandbox process.
- On timeout/crash, kill the process tree via Job Object/VM stop.
- On abnormal exit, mark blocked; do not retry outside budget.
- Snapshot/rollback the project and discard the sandbox workspace.
- Recover orphaned tasks as blocked; no auto-rerun.

## 9. Risks and rollback

Low-priv user:

- Risk: credential storage, ACL misconfiguration, world-readable data, tool update
  breakage, network egress.
- Rollback: stop broker, remove firewall rule, remove ACLs, remove stored credential,
  delete the local user; restore executor launch code.

Hyper-V:

- Risk: host RAM pressure (8 GB total), guest licensing, patching, network, admin
  feature changes.
- Rollback: stop/delete VM and checkpoints, remove shared folder, optionally disable
  Hyper-V after approval.

Windows Sandbox:

- Risk: manual-only, no reliable automation channel, tool install per run, secret
  mapping.
- Rollback: delete .wsb, disable feature if desired.

AppContainer:

- Risk: compatibility and launcher complexity; unverified child-process/network
  behavior.
- Rollback: delete profile/ACL, revert launcher.

Current state:

- No persistent change was made. The temporary il-probe directory was removed.
  Rollback is not needed for this assessment.

## 10. Authorization required

The following require explicit approval/admin elevation; none was performed:

1. Create a local low-privilege user and set its password/credential storage.
2. Set ACLs on task workspaces, tool directories, and the sandbox profile.
3. Add Windows Firewall outbound rules for the sandbox user.
4. Enable Hyper-V and/or Windows Sandbox features (admin, reboot).
5. Provide a Windows guest image/license for Hyper-V if chosen.
6. Install guest tools/Python/OpenCode/DSH in a VM if chosen.
7. Change executor launch code to use the broker (after approval of the design).

No Telegram, service, scheduled task, startup item, or system-policy change is
included.

## 11. Conclusion

Current status: application-layer safety only; no OS isolation. Do not describe
allowed_paths, snapshots, or rollback as a Windows sandbox.

Recommended order:

1. Approve and implement the dedicated low-privilege user + ACL + Job Object broker.
2. Run the isolation probes and a read-only/reversible task through it.
3. If the threat model requires stronger isolation, move to a Hyper-V VM.
4. Consider Windows Sandbox for manual validation only.
5. Prototype AppContainer only after the simpler OS boundary is working.

No production source, configuration, credential, database, system policy, Telegram
setting, service, or scheduled task was changed during this assessment.
