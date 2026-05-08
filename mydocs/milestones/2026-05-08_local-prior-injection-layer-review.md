# Milestone Review: Local Prior + Injection Layer

## Summary

This milestone changes expcap from a generic experience/pattern store into an LLM-oriented local-prior and injection layer.

The product thesis is:

- Top-level knowledge should be sparse, durable, and high value.
- High-value knowledge is not only truth; it includes preferences, habits, team/project background, historical reasons, prior successful paths, and "do not make me repeat this" instructions.
- Lower layers can stay faithful and raw: markdown docs, codemap chunks, trace/episode records, and retrieved context are acceptable because the consuming model can re-analyze them.
- Storage layers and injection modes are separate concerns.

## User-Visible Outcome

- `auto-start` still returns an activation view, but the selected assets now include richer provenance, evidence, risk flags, and injection channels.
- `injection_plan` splits selected knowledge into `system_prompt`, `runtime_context`, and `reference_summary`.
- `save-prior` lets an agent intentionally save explicit durable priors such as `preference`, `constraint`, and `dont_repeat`.
- High-priority explicit priors join an `explicit-prior-pool` so they are not lost when Milvus top-K is dominated by old semantic matches.
- `auto-start` / `activate` materialize host-consumable artifacts:
  - `injections/<activation_id>.md`
  - `injections/<activation_id>.json`
  - `injections/latest.md`
  - `injections/latest.json`
- Claude hooks now inject the rendered Markdown context instead of only listing top asset titles.

## Implementation Slices

1. Hooks integration and policy hardening

- Added Claude hook installation mode via `install-project --integration-mode claude-hooks`.
- Added `scripts/expcap-hook`.
- Added duplicate prompt/stop cooldown handling.
- Added explicit no-save suppression for stop-triggered `auto-finish`.
- Added hook runtime visibility in status/doctor.

2. Local-prior taxonomy

- Added first-class knowledge kinds:
  - `past_win`
  - `preference`
  - `constraint`
  - `decision_memory`
  - `dont_repeat`
  - `codemap`
- Updated extraction, ranking, rendering, and review queue priority.
- Added high-priority prior treatment for `preference`, `constraint`, and `dont_repeat`.

3. Codemap/doc ingestion and recall

- Added `ingest-docs` to import README/AGENTS/CLAUDE/docs markdown as faithful `codemap` context assets.
- Added pruning of stale doc chunks/vectors.
- Added codemap visibility in status/dashboard.
- Added benchmark expectations for `knowledge_kind` and `source_document`.
- Added codemap slot preservation for doc/architecture tasks.

4. Injection policy

- Added `runtime/core/injection_policy.py`.
- Added `injection_plan` to activation views.
- Routed selected assets into:
  - `system_prompt`: tiny durable priors
  - `runtime_context`: task-relevant priors and constraints
  - `reference_summary`: codemap/raw/background material
- Added injection observability in status/dashboard.

5. Explicit prior save path

- Added `save-prior`.
- Saved explicit priors as active project assets.
- Added `explicit-prior-pool` activation path.
- Verified a real `dont_repeat` asset routes into `system_prompt`.

6. Injection materialization

- Added `runtime/core/injection_materializer.py`.
- Wrote injection Markdown/JSON artifacts under the memory root.
- Added non-fatal `injection_artifact_warning`.
- Updated Claude hook output to consume rendered injection Markdown.

7. Project-first retrieval guardrail

- Full CLI regression exposed an edge case where Milvus could return only shared assets while local project assets were skipped.
- Added `project-priority-pool` so active local project assets can compete when Milvus has only shared candidates.

## Primary Changed Files

- `runtime/core/engine.py`
- `runtime/core/knowledge_kinds.py`
- `runtime/core/injection_policy.py`
- `runtime/core/injection_materializer.py`
- `runtime/core/project_install.py`
- `runtime/core/project_policy.py`
- `runtime/core/hook_activity.py`
- `runtime/cli/main.py`
- `runtime/storage/milvus_store.py`
- `scripts/expcap-hook`
- `tests/test_engine.py`
- `tests/test_cli_flow.py`
- `tests/test_install_project.py`
- `README.md`
- `README.zh-CN.md`

## Runtime Snapshot

Latest dashboard snapshot:

- Overall score: `78`
- Verdict: `healthy`
- Assets: `121`
- Candidates: `128`
- Activation logs: `168`
- Asset quality: `70/95 healthy`
- Activation help: `115/144 helpful`
- Milvus contribution: `69% activations`
- Local-prior assets: `27`
- High-priority prior assets: `1`
- System prompt items: `3`
- Reference summary items: `7`
- Write activity: `246 writes / 14d`

## Validation

Passed validation before milestone freeze:

- `.venv/bin/python -m unittest tests.test_cli_flow`
- `.venv/bin/python -m unittest tests.test_engine`
- `.venv/bin/python -m unittest tests.test_install_project`
- `git diff --check`
- Real `auto-start` generated `injections/latest.md`.
- Real `auto-start` confirmed the saved `dont_repeat` prior enters `system_prompt`.

## Review Focus

Reviewers should focus on:

- `runtime/cli/main.py` size and whether follow-up extraction is needed before merging.
- Ranking semantics in `engine.py`, especially `explicit-prior-pool`, `project-priority-pool`, codemap slot preservation, and Milvus-first behavior.
- Whether `system_prompt` eligibility is conservative enough.
- Whether generated Claude hooks are safe, non-destructive, and compatible with existing `.claude/settings.json`.
- Whether dashboard/status metrics are stable under old activation views without `injection_plan`.

## Known Risks

- The diff is large and should not be expanded further before review.
- `runtime/cli/main.py` and `runtime/core/engine.py` are now carrying too much responsibility.
- Hash embeddings still limit semantic top-1 quality; real embeddings remain a later quality upgrade.
- `system_prompt` is now proven in real activation, but only across a small number of new activations.
- Several new runtime files are untracked and must be intentionally staged if this becomes a commit.

## Recommended Next Step

Freeze this milestone and review it as one conceptual change: "local priors plus injection channels".

After review, choose one next slice:

- Codex host adapter / hook consumption for `injections/latest.md`.
- Real embedding provider with hash fallback.
- Refactor `runtime/cli/main.py` into smaller command modules.
