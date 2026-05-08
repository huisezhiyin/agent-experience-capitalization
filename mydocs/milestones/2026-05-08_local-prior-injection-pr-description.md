# PR Draft: Local Prior Knowledge + Injection Channels

## What changed

This PR turns expcap into a local-prior layer for LLM agents, not just a generic pattern store.

It adds:

- First-class local-prior kinds: `past_win`, `preference`, `constraint`, `decision_memory`, `dont_repeat`, `codemap`.
- Doc/codemap ingestion for README/AGENTS/CLAUDE/docs markdown.
- Three injection channels: `system_prompt`, `runtime_context`, `reference_summary`.
- `save-prior` for explicit durable user/team/project priors.
- `explicit-prior-pool` so sparse high-value priors are always considered.
- Injection materialization as `injections/latest.md/json`.
- Claude hook integration that consumes rendered injection context.
- Dashboard/status observability for local priors and injection channels.

## Why

The core user goal is saving repetition:

- "I already said this; do not make me repeat it."
- "This worked before; reference it next time."
- "This is our team/project habit or historical background."
- "This design exists because of old constraints."

The system should not treat knowledge as only truth/facts. It should preserve local priors, habits, preferences, and background so the next LLM run starts with better defaults.

## Validation

- `.venv/bin/python -m unittest tests.test_cli_flow`
- `.venv/bin/python -m unittest tests.test_engine`
- `.venv/bin/python -m unittest tests.test_install_project`
- `git diff --check`
- Real `auto-start` generated `injections/latest.md`.
- Real `auto-start` routed saved `dont_repeat` prior to `system_prompt`.

## Review Notes

- This is a large milestone diff. Prefer reviewing by conceptual slice, not file order.
- `runtime/cli/main.py` and `runtime/core/engine.py` should probably be refactored after this lands.
- The next feature slice should be either Codex host consumption, real embeddings, or CLI module extraction.
