# Micro Spec: expcap hooks integration enhancement

## Goal

Upgrade expcap from docs-only host integration to runnable hook-based integration, starting with Claude Code and keeping Codex compatibility as an optional/experimental adapter.

## Current State

- `install-project` writes `AGENTS.expcap.md`, updates `AGENTS.md`, optionally updates `CLAUDE.md`, and writes `.expcap-project.json`.
- `install-project --include-claude` currently documents behavior for Claude, but does not install `.claude/settings*.json` or project hook scripts.
- Core runtime behavior already exists in `auto-start`, `progressive-recall`, `feedback`, `auto-finish`, `status`, `doctor`, and `dashboard`.
- Host support is asymmetric:
  - Claude Code has documented hook events and project settings.
  - Codex appears to have hook/event surfaces, but public/stable docs are weaker, so adapter logic must tolerate absence or drift.

## Product Direction

Treat host hooks as a trigger layer, not as the system of record.

- Hooks decide when to invoke expcap.
- `scripts/expcap` and Python runtime decide what to do.
- AGENTS/CLAUDE sidecar text remains as guidance and fallback.

## Proposed Architecture

### 1. Integration modes

Add install-time integration modes instead of a single `--include-claude` boolean.

- `docs-only`
  - Current behavior.
  - Write `AGENTS.expcap.md`, managed blocks, `.expcap-project.json`, `.gitignore`.
- `claude-hooks`
  - Install Claude project hook config and scripts in addition to docs-only behavior.
- `codex-hooks`
  - Install Codex-compatible hook config/scripts when supported; otherwise emit explicit fallback metadata and keep docs-only behavior.
- `hybrid`
  - Install every supported host integration for the workspace while preserving docs fallback.

Backward compatibility:

- Keep `--include-claude` as a shorthand for `--integration-mode claude-hooks` during a transition window.

### 2. Unified hook wrapper

Introduce a single host-agnostic wrapper:

- `scripts/expcap-hook`

Responsibilities:

- Normalize host event payloads into a common internal contract.
- Detect workspace, project policy, and runtime env.
- Decide whether to run `auto-start`, `progressive-recall`, `feedback`, or `auto-finish`.
- Emit structured logs and skip reasons.
- Never own business policy beyond lightweight event-to-command routing.

Example contract:

- `scripts/expcap-hook user-prompt-submit --host claude --workspace "$PWD" --task "<summary>"`
- `scripts/expcap-hook stop --host claude --workspace "$PWD" --task "<summary>" --result-status success`

### 3. Host adapters

#### Claude Code

Install:

- `.claude/settings.json` or `.claude/settings.local.json`
- `.claude/hooks/` scripts that call `scripts/expcap-hook`

Minimum first version:

- `UserPromptSubmit -> auto-start`
- `Stop -> auto-finish`

Optional phase 2:

- `PostToolUse` for targeted `progressive-recall` or lightweight `log`

#### Codex

Install:

- project-local Codex config/hook artifacts only if the runtime/config surface is detected and supported

Minimum first version:

- same semantic intent as Claude:
  - prompt/session start -> `auto-start`
  - stop/turn completion -> `auto-finish`

Fallback:

- if no supported hook surface is detected, installation records `fallback_mode=docs-only` and preserves sidecar guidance without pretending hooks are active

### 4. Policy and guardrails

Move hook-execution policy into runtime/wrapper so it is shared across hosts.

Initial policies:

- Do not run `auto-start` for empty or trivial prompts.
- Do not run `auto-finish` when task state is obviously unconverged.
- Respect explicit “不要记录 / don’t save” signals.
- Dedupe repeated starts/stops for the same task within a cooldown window.
- Make skip reasons observable.

### 5. Observability

Add hook runtime visibility so the system does not become a black box.

Track:

- last hook event per workspace
- host type
- trigger count
- skip reason
- fallback reason
- command result summary

Surfacing:

- `status` should summarize current integration mode and recent hook activity.
- `doctor` should warn on broken hook installation or repeated hook failures.
- `dashboard` can later expose hook activity as an operational panel.

## Implementation Phases

### Phase 1: Claude-first runnable integration

- Add install mode support.
- Add `scripts/expcap-hook`.
- Add Claude hook config/script generation.
- Add tests for install output and wrapper routing.

### Phase 2: Observability + policy hardening

- Persist hook execution records.
- Add dedupe/skip policy.
- Surface recent hook health in `status`/`doctor`.

### Phase 3: Codex compatibility layer

- Detect supported Codex hook/config surface.
- Install best-effort adapter.
- Record explicit fallback when unsupported.

## Key File Changes

- `runtime/cli/main.py`
  - extend `install-project` arguments and output metadata
- `runtime/core/project_install.py`
  - generate integration-mode-specific files
- `scripts/expcap-hook`
  - new wrapper entrypoint
- `tests/test_install_project.py`
  - integration mode coverage
- `tests/test_cli_flow.py`
  - wrapper/runtime behavior coverage
- docs
  - update README / README.zh-CN with host integration modes

## Risks

- Codex hook surface may differ across app/CLI versions; adapter must be capability-driven, not assumption-driven.
- Hook-triggered `auto-finish` can create noisy saves unless task-convergence guards are explicit.
- Host-specific config generation can become brittle if too much policy leaks into generated files.

## Validation

- `install-project --integration-mode claude-hooks` generates deterministic Claude config and hook scripts.
- Re-running installation is idempotent.
- Wrapper handles supported events and emits structured skip/fallback results.
- `status`/`doctor` can explain whether hook integration is active, degraded, or docs-only.

## Implementation Status

- Phase 1 completed on 2026-05-08.
- Phase 2 minimum observability completed on 2026-05-08.
- Phase 2 policy hardening baseline completed on 2026-05-08.
- Implemented `--integration-mode claude-hooks` plus backward-compatible `--include-claude`.
- Added `scripts/expcap-hook` with `user-prompt-submit` and `stop` routing.
- Added Claude `.claude/settings.json` generation and project hook scripts.
- Persisted hook event records under workspace memory and exposed `hook_integration` in `status`.
- Added `hook_runtime` health checks to `doctor`.
- Added hook wrapper cooldown-based duplicate skipping and explicit no-save skip handling for stop events.
- Validation passed:
  - `.venv/bin/python -m unittest tests.test_install_project`
  - `.venv/bin/python -m unittest tests.test_cli_flow.CliFlowTests.test_cli_install_project_can_enable_claude_hooks_mode tests.test_cli_flow.CliFlowTests.test_expcap_hook_user_prompt_submit_routes_to_auto_start tests.test_cli_flow.CliFlowTests.test_expcap_hook_stop_routes_to_auto_finish`
  - `.venv/bin/python -m unittest tests.test_cli_flow.CliFlowTests.test_cli_auto_start_and_auto_finish_flow tests.test_cli_flow.CliFlowTests.test_cli_auto_start_still_runs_for_inactive_project tests.test_install_project.InstallProjectTests.test_install_project_is_idempotent`
  - `.venv/bin/python -m unittest tests.test_install_project tests.test_cli_flow.CliFlowTests.test_status_and_doctor_report_claude_hook_activity tests.test_cli_flow.CliFlowTests.test_expcap_hook_user_prompt_submit_routes_to_auto_start tests.test_cli_flow.CliFlowTests.test_expcap_hook_stop_routes_to_auto_finish`
  - `.venv/bin/python -m unittest tests.test_cli_flow.CliFlowTests.test_expcap_hook_user_prompt_submit_skips_duplicate_within_cooldown tests.test_cli_flow.CliFlowTests.test_expcap_hook_stop_skips_when_user_requests_no_save tests.test_cli_flow.CliFlowTests.test_status_and_doctor_report_claude_hook_activity`
