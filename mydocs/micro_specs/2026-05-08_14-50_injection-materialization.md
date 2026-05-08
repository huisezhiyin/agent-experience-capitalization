# Micro Spec: Injection Materialization

## Goal

Move `injection_plan` from an internal activation JSON field to stable host-consumable artifacts. The core idea is still sparse top-level priors plus faithful lower-level context; this slice makes the three injection channels easy for Codex, Claude hooks, and other hosts to read.

## Scope

- Materialize latest activation injection channels into JSON and Markdown files under the project memory root.
- Keep files in `$EXPCAP_HOME` / memory storage, not in the business repository.
- Make `auto-start` and `activate` return artifact paths so hooks and agents can consume them.
- Let Claude `UserPromptSubmit` hook prefer rendered injection context over the old title-only summary.

## Risks

- Do not over-inject: `system_prompt` must stay tiny and `reference_summary` should remain bounded.
- Preserve current activation payload shape and existing hook behavior for empty plans.
- Avoid writing runtime artifacts into the repo working tree.

## Validation

- Add unit/CLI tests for artifact creation and hook context rendering.
- Run targeted CLI/hook tests, `tests.test_engine`, and `git diff --check`.

## Result

- Added `runtime/core/injection_materializer.py`.
- `auto-start` and `activate` now write `injections/<activation_id>.md/json` plus `injections/latest.md/json`.
- Activation payloads and saved activation views include `injection_artifacts` paths when materialization succeeds.
- Claude `UserPromptSubmit` hook now renders the structured injection Markdown into `additionalContext` instead of only listing top asset titles.
- Artifact write failures are reported as `injection_artifact_warning` without failing the whole activation.
- Full CLI regression also exposed and fixed a project-first retrieval edge case: when Milvus only returns shared assets, local active project assets now join a `project-priority-pool` and receive enough ranking weight to compete ahead of cross-project guidance.

## Validation Log

- `tests.test_cli_flow` passed.
- `tests.test_engine` passed.
- `tests.test_install_project` passed.
- `git diff --check` passed.
- Real `auto-start` generated `latest.md` with `System Prompt Priors`, `Runtime Context`, and `Reference Summary`.
