# Agent Experience Capitalization

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-pre--1.0-orange.svg)](GOVERNANCE.md)
[![Codex Skill](https://img.shields.io/badge/Codex%20Skill-ready-brightgreen.svg)](skills/expcap/SKILL.md)

Project-owned memory for coding agents.

`expcap` turns useful agent work into reusable engineering assets that belong to
the project, not to one person, one machine, or one model account.

Language: [English](README.md) | [Chinese](README.zh-CN.md)

## Use With Codex

Ready-to-use Codex skill: [`skills/expcap/SKILL.md`](skills/expcap/SKILL.md).

One-command local setup:

```bash
git clone <repo-url>
cd agent-experience-capitalization
scripts/codex-skill-quickstart
```

This installs the skill into `~/.codex/skills/expcap`, installs the runtime with
Milvus Lite support, enables the current project, and runs `doctor` so you can
verify the setup immediately.

By default the installed project is marked `active`, which means agent
workflows treat it as an active project in reporting. For dormant or archived
projects, install with `EXPCAP_PROJECT_STATUS=inactive` so the project keeps the
skill and storage contract but is counted separately in reporting and coverage
analysis.

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
- Returns sourced candidates so the LLM can decide whether each asset applies.
- Converts completed work into `trace -> episode -> candidate -> asset`.
- Tracks whether activated experience actually helped.
- Maintains candidate review queues and asset health signals.
- Uses Milvus as the core semantic retrieval layer; SQLite remains a lightweight
  state index and fallback.
- Exposes a backend contract for shared object stores, cloud state indexes, and
  hosted vector search.

## Install

Use the one-command setup above for Codex. Manual setup:

```bash
git clone <repo-url>
cd agent-experience-capitalization

python3 -m venv .venv
. .venv/bin/activate
.venv/bin/pip install -e ".[milvus]"
scripts/install-codex-skill
scripts/expcap --help
```

Milvus Lite is installed by default above because Milvus is the core semantic
retrieval layer:

```bash
scripts/expcap sync-milvus --workspace "$PWD" --include-shared
```

## Quickstart

The recommended entrypoint is the Codex skill in
`skills/expcap/SKILL.md`. Install it once, then let the agent run the workflow
through the skill instead of making every user memorize CLI commands.

Use centralized local storage for short-cycle testing:

```bash
export EXPCAP_STORAGE_PROFILE=user-cache
export EXPCAP_HOME="$HOME/.expcap"
```

Start a task by activating relevant experience:

```bash
expcap auto-start --task "fix pytest import error" --workspace "$PWD"
```

Finish a task by saving the reusable lesson:

```bash
expcap auto-finish \
  --task "fix pytest import error" \
  --workspace "$PWD" \
  --command "python3 -m unittest discover -s tests -v" \
  --verification-status passed \
  --verification-summary "tests passed" \
  --result-status success \
  --result-summary "fixed import path"
```

Check the loop:

```bash
expcap status --workspace "$PWD"
expcap doctor --workspace "$PWD"
```

Runtime data is written to `$EXPCAP_HOME` with the recommended `user-cache`
profile. Keep `.agent-memory/` out of source control for explicit project-local
testing.

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
`AGENTS.expcap.md`. It also ensures `.agent-memory/` is present in
`.gitignore`. Agents can then use the skill-backed default workflow:

```bash
expcap auto-start --task "your task" --workspace "$PWD"
expcap auto-finish --task "your task" --workspace "$PWD" --verification-status passed --result-status success
```

For manual debugging, the lower-level pipeline is still available:
`ingest -> review -> extract -> promote -> activate`.

Active-project control:

```bash
scripts/expcap install-project --workspace /path/to/project --project-status active
scripts/expcap install-project --workspace /path/to/project --project-status inactive
```

New chat still runs `auto-start` in both cases. `active` and `inactive` are
reporting labels so coverage and daily review can focus on genuinely active
projects instead of every installed repository.

## Core Concepts

- `trace`: raw task evidence.
- `episode`: reviewed task narrative.
- `candidate`: reusable lesson proposed from an episode.
- `asset`: promoted project/team memory.
- `activation`: selected assets injected into a future task.
- `feedback`: whether activation helped.

Activation views include `source_provenance`, `match_evidence`, `risk_flags`,
and `llm_use_guidance`. Retrieval provides sourced candidates; the coding agent
is still responsible for deciding whether an asset fits the current task.

Assets carry scope and lifecycle metadata:

- `knowledge_scope`: `project` or `cross-project`.
- `knowledge_kind`: `pattern`, `anti_pattern`, `rule`, `context`, or
  `checklist`.
- `temperature`: `hot`, `warm`, `neutral`, or `cool`.
- `review_status`: `healthy`, `watch`, `needs_review`, or `unproven`.

## Storage

Assets are project-owned even when the data source is shared. The project keeps
identity and ownership metadata; storage can be local, user-level, or remote.

Storage profiles:

- `local`: runtime data lives in the project `.agent-memory/` directory.
- `user-cache`: runtime data lives under `EXPCAP_HOME` and stays out of the
  project directory.
- `shared`: source of truth, state, and retrieval are expected to be shared
  backends.
- `hybrid`: shared source/retrieval with a local cache and SQLite state index.

Recommended default for agent workflows:

```bash
export EXPCAP_STORAGE_PROFILE=user-cache
export EXPCAP_HOME="$HOME/.expcap"
```

This keeps runtime data out of the project directory while preserving
project-owned asset identity.

Explicit local profile:

- JSON files are the source of truth.
- Milvus Lite is the local core semantic retrieval layer.
- SQLite stores lightweight state, review decisions, activation logs, and
  fallback metadata indexes.

To force project-local storage:

```bash
export EXPCAP_STORAGE_PROFILE=local
```

Shared mode uses the same asset contract:

```bash
export EXPCAP_STORAGE_PROFILE=shared
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
- `feedback_cleanup`: stale unresolved activations that were auto-closed as
  `unclear` so metrics stay usable.
- `candidate_review_queue`: candidates that need human review.
- `asset_effectiveness_summary`: asset temperature and review health.
- `retrieval_backends`: Milvus core retrieval readiness and SQLite lightweight
  index health.
- `project_activity`: whether the workspace is `active` or `inactive` for
  reporting and coverage analysis.
- `backend_configuration`: active local/shareable backend profile.

Milvus is the core retrieval capability. If Milvus Lite is locked or
unavailable, the runtime should degrade to JSON/SQLite so work can continue, but
`doctor` should surface the degradation clearly because retrieval quality is
reduced. `doctor` also reports Milvus lock metadata and safe recovery
recommendations.

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
