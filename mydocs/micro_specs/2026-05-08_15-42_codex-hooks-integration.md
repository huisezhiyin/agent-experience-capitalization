# Codex hooks integration smoke slice

- Goal: make Codex the first-class hook target by adding a codex-hooks install mode, project-local hook config/snippet, wrappers, status/doctor visibility, and focused tests.
- Scope: project installer, CLI install/status/doctor, hook wrapper compatibility, README notes, tests. Do not modify user global ~/.codex/hooks.json automatically.
- Risk: Codex hook config discovery may differ by Codex version, so generated config must be explicit and safe to merge into global hooks when needed.
- Validation: targeted install/status/hook tests, real codex-host smoke where possible, git diff check.

## Result

- Added `codex-hooks` integration mode with `.codex/hooks.json` plus `UserPromptSubmit` and `Stop` wrappers.
- Wrappers prefer project-local `scripts/expcap-hook` and fall back to the current runtime hook path so external projects can run immediately.
- Status and doctor now report Codex hook configuration and recent hook activity.
- README documents Codex-first install and the user-level `~/.codex/hooks.json` merge caveat.
- Validation passed: `tests.test_install_project`, `tests.test_engine`, `tests.test_cli_flow`, focused Codex hook tests, real temporary-project wrapper smoke, and `git diff --check`.
