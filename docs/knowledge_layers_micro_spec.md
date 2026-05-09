# Knowledge Layers Micro Spec

## Goal

Make expcap's knowledge injection and knowledge save architecture explicit in runtime outputs, docs, and tests.

## Injection Layers

- `task_start_runtime_injection`: task input augmentation at `SessionStart`, `UserPromptSubmit`, and `auto-start`.
- `system_prompt_injection`: durable project-level prompt material intended for `AGENTS.md` / `AGENTS.expcap.md`.
- `continuous_runtime_recall_injection`: event-driven recall during a conversation, primarily through `progressive-recall`.

## Save Layers

- `milvus`: semantic retrieval index.
- `sqlite`: lightweight state index for candidates, feedback, review queues, and activation logs.
- `markdown_files`: human-readable and reviewable knowledge artifacts such as injection markdown, docs, and prompt files.
- `logs`: raw execution evidence such as traces, episodes, hook events, and activation views.

## Done Contract

- Activation views expose both legacy `injection_channel` and new `injection_layer` metadata.
- Injection plans expose `injection_layers` with the three-layer architecture.
- Status output exposes `knowledge_save_layers` with the four save layers and current paths/counts.
- Focused tests cover the new layer metadata without breaking legacy fields.

## Validation

- `python3 -m unittest discover -s tests -v` passed 119 tests on 2026-05-09.
- `benchmark-milvus` now reports a marked `state-index-fallback` when Milvus runtime is unavailable, so codemap expectation diagnostics still work without pretending the result came from Milvus.
