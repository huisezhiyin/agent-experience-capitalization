# Codex Hooks Lifecycle Micro Spec

## Goal

Move `expcap` Codex integration beyond skill-only activation by using Codex lifecycle hooks as a lightweight host adapter.

## Slice

- Keep `UserPromptSubmit` and `Stop` as the default activation/save loop.
- Add Codex `SessionStart` and `PostToolUse` wrapper support.
- Record tool-use evidence without promoting every tool call into long-term knowledge.
- Feed recent command/error evidence into `auto-finish`.

## Done Contract

- `install-project --integration-mode codex-hooks` writes lifecycle hook config and scripts.
- `scripts/expcap-hook post-tool-use` records compact command/error evidence.
- Failed `PostToolUse` events can trigger `progressive-recall` and return continuous runtime recall `additionalContext`.
- `scripts/expcap-hook stop` passes recent evidence as `--command` / `--error`.
- Focused tests prove install output and trace evidence behavior.

## PostToolUse Progressive Recall v1

- Successful tool calls stay quiet and only record compact evidence.
- Failed tool calls or stderr signals invoke `progressive-recall --phase fix`.
- The hook forwards failed command text, error text, and file path hints.
- Identical failure signals are de-duplicated by hook cooldown to avoid repeated recall noise.

## SessionStart Follow-up

- `SessionStart` should activate workspace-level project experience and return Codex `additionalContext`.
- Repeated startup/resume events within cooldown should reuse `injections/latest.md` instead of creating noisy duplicate activations.
- The session-start task string is intentionally broad because no user prompt exists yet.

## PreToolUse Guardrail v1

- `PreToolUse` should be quiet by default: record the attempted tool call and exit 0 with no output.
- Only high-confidence policy hits should block with Codex `permissionDecision: deny`.
- Initial deny rules cover destructive git/shell commands, attempts to git-add `.agent-memory/`, and file edits that introduce obvious local secrets.

## PermissionRequest Guardrail v1

- `PermissionRequest` should not auto-approve requests in v1.
- Quietly record normal approval requests and let Codex show the native approval prompt.
- Reuse the same high-confidence deny rules as `PreToolUse` for destructive commands, local runtime data, and obvious secrets.

## Validation

- `python3 -m unittest tests.test_install_project ... test_status_and_doctor_report_codex_hook_activity` passed 21 focused lifecycle hook tests on 2026-05-09.
- Dogfood project install now emits six Codex lifecycle hooks: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, and `Stop`.
- Full test discovery was previously attempted in this environment; the remaining known unrelated failure is a docs ingestion test that needs `pymilvus`.

## Resume

- Next useful slice is publishing or PR refresh: decide whether to include the generated project dogfood files (`.codex/`, `.expcap-project.json`, `AGENTS.expcap.md`, `AGENTS.md`) or keep them local-only.
