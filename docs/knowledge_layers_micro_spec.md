# Knowledge Layers Micro Spec

## Goal

Make expcap's knowledge injection and knowledge save architecture explicit in runtime outputs, docs, and tests.

## Injection Layers

- `task_start_runtime_injection`: task input augmentation at `SessionStart`, `UserPromptSubmit`, and `auto-start`.
- `system_prompt_injection`: durable project-level prompt material intended for `AGENTS.md` / `AGENTS.expcap.md`.
- `continuous_runtime_recall_injection`: event-driven recall during a conversation, primarily through `progressive-recall`.

## Continuous Runtime Recall Trigger v1

- `PostToolUse` failures and stderr signals trigger `progressive-recall` with phase `fix`.
- The hook passes the failed command, stderr, and file path hints as delta evidence.
- When delta recall returns new assets, the hook returns Codex `additionalContext` headed by `continuous_runtime_recall_injection`.
- Repeated identical failure signals are suppressed by a hook-level cooldown before calling `progressive-recall` again.

## Save Layers

- `milvus`: semantic retrieval index.
- `sqlite`: governance ledger for candidates, assets, feedback, review queues, lifecycle state, and activation logs.
- `markdown_files`: human-readable and reviewable knowledge artifacts such as injection markdown, docs, and prompt files.
- `logs`: raw execution evidence such as traces, episodes, hook events, and activation views.

## Boundary Clarification

- `milvus` is responsible for findability, not source-of-truth trust.
- `sqlite` is responsible for lifecycle, relationships, and governance status, not semantic understanding.
- `markdown_files` carry small, stable, reviewable rules rather than large-scale episodic recall.
- `logs` remain the recoverable evidence source for raw task memory.

## Governance Gates

- Saved candidates and assets should carry a `scope_profile` with at least `task_type`, `module`, `language`, and `framework` when derivable.
- Retrieval should reward matching `scope_profile` metadata instead of relying only on broad `task-family` scope.
- Assets marked `quarantined` or `deprecated` should not enter the final activation set by default.
- Assets listed in each other's `conflicts_with` set should not be injected together in the same activation batch.

## View Adapters

- Runtime should expose governance-native summaries before any CLI/dashboard rendering layer.
- Validation queue and governance summary should have dedicated view adapters so status/dashboard can consume stable shapes instead of rebuilding counts ad hoc.
- Governance-facing commands should support both explicit filters (`review_status`, `quarantine_status`, `asset_status`) and a few high-signal presets such as `only_deprecated`, `only_quarantined`, and `only_needs_review`.

## Done Contract

- Activation views expose both legacy `injection_channel` and new `injection_layer` metadata.
- Injection plans expose `injection_layers` with the three-layer architecture.
- Post-tool failure hooks can inject progressive recall output as `continuous_runtime_recall_injection`.
- Status output exposes `knowledge_save_layers` with the four save layers and current paths/counts.
- Focused tests cover the new layer metadata without breaking legacy fields.

## Validation

- `python3 -m unittest discover -s tests -v` passed 119 tests on 2026-05-09.
- `benchmark-milvus` now reports a marked `state-index-fallback` when Milvus runtime is unavailable, so codemap expectation diagnostics still work without pretending the result came from Milvus.
