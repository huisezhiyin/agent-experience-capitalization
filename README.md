# Agent Experience Capitalization

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-pre--1.0-orange.svg)](GOVERNANCE.md)

Project-owned memory for coding agents.

`expcap` turns useful agent work into reusable engineering assets that belong to
the project, not to one person, one machine, or one model account.

Language: [English](README.md) | [Chinese](README.zh-CN.md)

## Why

Personal memory helps one agent remember one user. Teams need something
different: engineering experience that can move with the codebase.

`expcap` is **TEAM memory**: **Transferable Engineering Asset Memory**.

It is designed for:

- project-level rules, patterns, context, and checklists;
- team-shareable assets that can be reviewed and delivered;
- agent workflows that automatically get context before work and save lessons
  after work;
- local-first development with a path to shared cloud backends.

## What It Does

- Activates relevant project experience at the start of a task.
- Converts completed work into `trace -> episode -> candidate -> asset`.
- Tracks whether activated experience actually helped.
- Maintains candidate review queues and asset health signals.
- Supports local JSON/SQLite storage and optional Milvus Lite retrieval.
- Exposes a backend contract for shared object stores, cloud state indexes, and
  hosted vector search.

## Install

```bash
git clone <repo-url>
cd agent-experience-capitalization

python3 -m venv .venv
.venv/bin/pip install -e .
scripts/expcap --help
```

Optional Milvus Lite support:

```bash
.venv/bin/pip install -e ".[milvus]"
scripts/expcap sync-milvus --workspace "$PWD" --include-shared
```

## Quickstart

Create a trace, review it, extract a candidate, promote it into an asset, then
activate it:

```bash
scripts/expcap ingest \
  --workspace "$PWD" \
  --task "fix pytest import error" \
  --command "python3 -m unittest discover -s tests -v" \
  --error "ModuleNotFoundError: no module named foo" \
  --verification-status passed \
  --verification-summary "tests passed" \
  --result-status success \
  --result-summary "fixed import path" \
  --trace-id trace_demo_import_fix

scripts/expcap review --input .agent-memory/traces/bundles/trace_demo_import_fix.json
scripts/expcap extract --episode .agent-memory/episodes/ep_demo_import_fix.json
scripts/expcap promote --candidate .agent-memory/candidates/cand_demo_import_fix.json
scripts/expcap activate --task "fix pytest import error" --workspace "$PWD"
scripts/expcap status --workspace "$PWD"
```

Runtime data is written to `.agent-memory/`, which is ignored by git by default.

## Agent Workflow

Install `expcap` into another project:

```bash
scripts/expcap install-project --workspace /path/to/project
```

Also update `CLAUDE.md`:

```bash
scripts/expcap install-project --workspace /path/to/project --include-claude
```

The installer appends non-destructive instructions and creates
`AGENTS.expcap.md`. Agents can then use:

```bash
expcap auto-start --task "your task" --workspace "$PWD"
expcap auto-finish --task "your task" --workspace "$PWD" --verification-status passed --result-status success
```

## Core Concepts

- `trace`: raw task evidence.
- `episode`: reviewed task narrative.
- `candidate`: reusable lesson proposed from an episode.
- `asset`: promoted project/team memory.
- `activation`: selected assets injected into a future task.
- `feedback`: whether activation helped.

Assets carry scope and lifecycle metadata:

- `knowledge_scope`: `project` or `cross-project`.
- `knowledge_kind`: `pattern`, `anti_pattern`, `rule`, `context`, or
  `checklist`.
- `temperature`: `hot`, `warm`, `neutral`, or `cool`.
- `review_status`: `healthy`, `watch`, `needs_review`, or `unproven`.

## Storage

Default local mode:

- JSON files are the source of truth.
- SQLite stores state, indexes, review decisions, and activation logs.
- Milvus Lite can be used as an optional semantic retrieval layer.

Shared mode is expressed through the same asset contract:

```bash
export EXPCAP_SOURCE_OF_TRUTH_BACKEND=object-storage
export EXPCAP_STATE_INDEX_BACKEND=cloud-sql
export EXPCAP_RETRIEVAL_BACKEND=milvus
export EXPCAP_SHARING_BACKEND=cloud-shared
export EXPCAP_PROJECT_ID=github:org/repo
export EXPCAP_OWNING_TEAM=agent-platform
export EXPCAP_ASSET_STORE_URI=s3://bucket/expcap/assets
export EXPCAP_STATE_INDEX_URI=postgres://expcap
export EXPCAP_RETRIEVAL_INDEX_URI=https://milvus.example.com
export EXPCAP_SHARED_ASSET_STORE_URI=s3://bucket/expcap/shared
```

The current implementation focuses on the local runtime and the portable asset
contract. Cloud backends are intended to be enabled by backend configuration,
not by changing the product model.

## Status Signals

Use `status` for short-cycle evaluation:

```bash
expcap status --workspace "$PWD"
```

Use `doctor` when you need actionable diagnostics:

```bash
expcap doctor --workspace "$PWD"
```

Watch these fields:

- `activation_feedback_summary`: helped, pending, or stale missing feedback.
- `candidate_review_queue`: candidates that need human review.
- `asset_effectiveness_summary`: asset temperature and review health.
- `retrieval_backends`: SQLite and Milvus readiness.
- `backend_configuration`: active local/shareable backend profile.

Milvus Lite is intentionally optional. If it is locked or unavailable, the
runtime should degrade to JSON/SQLite instead of blocking the workflow.
`doctor` also reports Milvus lock metadata and safe recovery recommendations.

## Documentation

- [Core principles](docs/core_principles.md)
- [Architecture](docs/experience_capitalization_architecture.md)
- [MVP spec](docs/mvp_spec.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Governance](GOVERNANCE.md)

## Project Status

This project is pre-1.0. The current goal is to validate the experience asset
model, local runtime, activation feedback loop, and storage contract before
expanding the cloud backend surface.

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

## License

Apache-2.0. See [LICENSE](LICENSE).
