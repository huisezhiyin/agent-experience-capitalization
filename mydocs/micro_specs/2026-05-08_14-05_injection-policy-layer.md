# Micro Spec: Injection Policy Layer

## Goal

Turn expcap's local-prior thesis into an explicit injection policy. Knowledge is not only truth; it includes habits, preferences, historical background, prior wins, and "do not repeat this" instructions that save user/team repetition.

## Scope

- Add a runtime policy that routes selected assets into `runtime_context`, `system_prompt`, or `reference_summary`.
- Keep storage layering separate from injection mode: Milvus retrieves, markdown/local-prior assets summarize, raw evidence stays reference-friendly.
- Preserve the existing `rendered_context` field for compatibility while adding a structured `injection_plan`.

## Risks

- System-prompt injection must stay tiny and only include durable/high-priority priors.
- Codemap and raw context must not crowd out active task guidance.
- Existing activation tests and CLI outputs must remain backward compatible.

## Validation

- Add unit tests for channel assignment and activation payload shape.
- Run targeted engine/CLI tests plus `git diff --check`.

## Result

- Added `runtime/core/injection_policy.py`.
- Activation now emits structured `injection_plan` with `system_prompt`, `runtime_context`, and `reference_summary` channels.
- Selected assets now carry `injection_channel`.
- Legacy `rendered_context` remains available for existing callers.
- Documentation and the local-prior phase spec now describe injection policy separately from storage layers.
- `status` now reports `injection_policy_summary` for channel totals, plan coverage, and selected asset channel coverage.
- `dashboard` now shows injection channel cards, an `Injection Channels` panel, and per-activation system/runtime/reference counts.
- `review-candidates --knowledge-kind` now filters queues correctly, and high-priority local-prior candidates are weighted toward review.
- `save-prior` now lets an agent intentionally create sparse active priors from explicit user/team/project instructions.
- Explicit high-priority priors now join a small always-considered activation pool, so durable `preference`, `constraint`, and `dont_repeat` items can reach `system_prompt` even when Milvus top-K is dominated by older semantic matches.

## Validation Log

- `.venv/bin/python -m unittest tests.test_engine` passed.
- `.venv/bin/python -m unittest tests.test_cli_flow.CliFlowTests.test_cli_auto_start_and_auto_finish_flow tests.test_cli_flow.CliFlowTests.test_cli_ingest_docs_imports_markdown_as_codemap_assets` passed.
- `.venv/bin/python -m unittest tests.test_cli_flow.CliFlowTests.test_cli_status_summarizes_short_test_signals tests.test_cli_flow.CliFlowTests.test_cli_dashboard_generates_local_html_and_json` passed.
- Targeted candidate-review and save-prior tests passed.
- `git diff --check` passed.
- Real `auto-start` produced an activation view with `injection_plan` and `route_injection`.
- Real `auto-start` confirmed the saved `dont_repeat` prior was selected from `explicit-prior-pool` and routed to `system_prompt`.
