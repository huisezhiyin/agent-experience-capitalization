# Micro Spec: auto-start / auto-finish SQLite degrade handling

## Goal

Make `auto-start` and `auto-finish` degrade gracefully when stale activation feedback cleanup cannot write to SQLite, instead of exiting with `sqlite3.OperationalError`.

## Scope

- Workspace: `/Users/wuyue/github_project/agent-experience-capitalization`
- Files: `runtime/cli/main.py`, `tests/test_cli_flow.py`
- Keep warnings explicit so readonly/locked SQLite is still visible in output.

## Plan

1. Reuse `_safe_feedback_cleanup()` in `auto-start` and `auto-finish`.
2. Return a dedicated warning field in command output when cleanup falls back.
3. Add regression tests covering readonly cleanup failure for both commands.

## Validation

- `python3 -m unittest tests.test_cli_flow.CliFlowTests.test_cli_auto_start_warns_when_feedback_cleanup_is_unwritable tests.test_cli_flow.CliFlowTests.test_cli_auto_finish_warns_when_feedback_cleanup_is_unwritable tests.test_cli_flow.CliFlowTests.test_cli_auto_start_and_auto_finish_flow` passed.
- `python3 -m unittest tests.test_cli_flow.CliFlowTests.test_cli_status_auto_resolves_stale_unresolved_activation_feedback tests.test_cli_flow.CliFlowTests.test_cli_doctor_reports_workspace_health_and_recommendations` passed.
- Manual `scripts/expcap auto-start` and `scripts/expcap auto-finish` succeeded in the current full-access environment; both returned `feedback_cleanup_warning: null`.
