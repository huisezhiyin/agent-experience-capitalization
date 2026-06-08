# Agent Experience Capitalization

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-pre--1.0-orange.svg)](GOVERNANCE.md)
[![Codex Skill](https://img.shields.io/badge/Codex%20Skill-ready-brightgreen.svg)](skills/expcap/SKILL.md)

Project-owned, team-shareable experience governance for coding agents.

`expcap` turns useful agent work into evidence-backed engineering assets that
belong to the project, not to one person, one machine, or one model account.

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
different: engineering experience that can move with the codebase, stay tied to
recoverable evidence, and remain governable over time.

`expcap` is a **fault-tolerant experience governance layer** for coding agents.

It is designed for:

- project-level rules, patterns, context, and checklists;
- team-shareable assets that can be reviewed and delivered;
- agent workflows that get context before work and create governed candidates
  after work;
- local-first development with a path to shared cloud backends.

It is not a memory-consolidation pile. Raw traces remain first-class evidence;
LLM consolidation creates candidates rather than truth; promoted assets stay
scoped, reviewable, and feedback-governed.

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

## Fault-Tolerant Memory Governance

`expcap` is designed to avoid the naive pattern of repeatedly compressing old
memories into new abstract memories until drift accumulates.

Core rules:

- Raw trace is never replaced by abstraction.
- Consolidation creates candidates, not truth.
- Promotion requires evidence, review, and scope.
- Retrieval returns sourced candidates, not commands.
- Activated assets must receive feedback, decay, or quarantine.
- Cross-task contamination should be prevented by default.
- Abstractions must remain grounded in recoverable evidence.

One-line positioning:

> `expcap` is not a memory-consolidation pile. It is a fault-tolerant
> experience governance layer for coding agents.

## Prompt Layering

`expcap` works best as the dynamic layer beneath project-level prompt files,
not as a replacement for them.

- [`PROJECT_PROMPT.md`](PROJECT_PROMPT.md): host-neutral stable project rules
  that should apply every session across hosts.
- [`AGENTS.md`](AGENTS.md): primary project entrypoint for agent instructions.
- [`AGENTS.expcap.md`](AGENTS.expcap.md): dynamic `get/save` integration layer
  installed by `expcap install-project`.

The intended flow is: discover experience in `expcap`, prove it through real
task activations, then promote the small stable subset into project-level prompt
files.

Maintenance then stays host-neutral:

- edit stable rules in [`PROJECT_PROMPT.md`](PROJECT_PROMPT.md)
- use `expcap project-prompt suggest` to find assets worth promoting
- use `expcap project-prompt apply --sync-after` to write promoted rules into the managed block and refresh host bridge files
- use `expcap project-prompt archive --sync-after` to retire stale promoted rules with a traceable archive reason and refresh host bridge files
- use `expcap project-prompt sync` to refresh host bridge files such as
  `AGENTS.md` and `CLAUDE.md`

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
scripts/expcap benchmark-milvus --workspace "$PWD" --sample-size 5 --limit 3
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

The default embedding provider is zero-config `hash`. To test real OpenAI
embeddings while keeping the current Milvus Lite collection compatible, start
with 128 dimensions:

```bash
export EXPCAP_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY="..."
export EXPCAP_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
export EXPCAP_OPENAI_EMBEDDING_DIM=128
```

If no API key is present, expcap falls back to `hash` and reports that fallback
in `status` / `doctor`.

Milvus Lite indexes are namespaced by embedding profile, for example
`hash-token-sha256-signhash-128` or `openai-text-embedding-3-small-128`, so
different providers and dimensions do not share the same local DB file.

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
expcap dashboard --workspace "$PWD"
```

Open the local dashboard in a browser on macOS:

```bash
DASHBOARD_PATH="$(
  EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" \
    expcap dashboard --workspace "$PWD" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["saved_to"])'
)"
open "$DASHBOARD_PATH"
```

On Linux, use `xdg-open "$DASHBOARD_PATH"` instead of `open`. The dashboard is
a static local HTML file; no background web server is required.

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

Install Claude hooks (Phase 1 runnable integration):

```bash
scripts/expcap install-project --workspace /path/to/project --integration-mode claude-hooks
```

Install Codex hooks as the preferred host adapter:

```bash
scripts/expcap install-project --workspace /path/to/project --integration-mode codex-hooks
```

The installer appends non-destructive instructions and creates
`AGENTS.expcap.md`. It also ensures `PROJECT_PROMPT.md` exists as the
host-neutral stable rule source and that `.agent-memory/` is present in
`.gitignore`. In `codex-hooks` mode it writes `.codex/hooks.json` plus
`.codex/hooks/expcap_user_prompt_submit.sh` and `.codex/hooks/expcap_stop.sh`.
These wrappers route Codex `UserPromptSubmit` / `Stop` events to
`scripts/expcap-hook` with the default `user-cache` storage profile. If your
Codex build only reads the user-level `~/.codex/hooks.json`, merge the generated
project `.codex/hooks.json` entries into the user-level file. In `claude-hooks`
mode it additionally writes
`.claude/settings.json` plus hook scripts under `.claude/hooks/`, all routed
through `scripts/expcap-hook` to call `auto-start` / `auto-finish`. Agents can
then use the skill-backed default workflow:

```bash
expcap auto-start --task "your task" --workspace "$PWD"
expcap progressive-recall \
  --task "your task" \
  --workspace "$PWD" \
  --message "new error, file scope, or phase change"
expcap feedback \
  --workspace "$PWD" \
  --activation-id "<activation id>" \
  --help-signal supported_strong
expcap auto-finish --task "your task" --workspace "$PWD" --verification-status passed --result-status success
```

Use `progressive-recall` only when the conversation meaningfully changes:
new errors, new files/modules, topic drift, or phase changes such as
discussion -> implementation -> test -> fix. It applies a cooldown and returns
only delta assets that were not already activated recently.

Use `feedback` after validating whether an activation actually helped. It
records the help signal on the activation and refreshes linked assets'
temperature and review status.

For manual debugging, the lower-level pipeline is still available:
`ingest -> review -> extract -> promote -> activate`.

To import project docs as faithful codemap/context assets for LLM recall:

```bash
expcap ingest-docs --workspace "$PWD"
```

By default this scans `README*`, `AGENTS.md`, `CLAUDE.md`, and `docs/*.md`,
then stores chunks as `knowledge_kind=codemap` assets. It preserves source text
instead of rewriting docs into polished "truth".

To intentionally save a sparse top-level prior:

```bash
expcap save-prior \
  --workspace "$PWD" \
  --knowledge-kind dont_repeat \
  --title "Do not re-explain the project memory thesis" \
  --content "expcap is a local-prior layer for saving repeated user/team/project context, not a truth-only knowledge base."
```

Use this for explicit, durable preferences, constraints, historical decisions,
or "do not make me repeat this" instructions. High-priority priors are saved as
active project assets so the injection policy can route small stable items into
`system_prompt`. During activation, explicit high-priority priors also join a
small always-considered prior pool, so they are not lost just because Milvus
top-K retrieval favored older semantic matches.

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
- `asset`: promoted project/team experience asset.
- `activation`: selected assets injected into a future task.
- `feedback`: whether activation helped.

Promotion ladder:

- `trace -> episode -> candidate -> project asset -> team asset -> organization asset`

Each step upward should require stronger evidence, clearer scope, and stricter
review.

Activation views include `source_provenance`, `match_evidence`, `risk_flags`,
and `llm_use_guidance`. Retrieval provides sourced candidates; the coding agent
is still responsible for deciding whether an asset fits the current task.

Activation views also include an `injection_plan` that separates retrieval from
injection. The injection architecture has three layers:

- `task_start_runtime_injection`: task input augmentation at `SessionStart`,
  `UserPromptSubmit`, and `auto-start`.
- `system_prompt_injection`: durable project-level prompt material intended for
  `AGENTS.md` / `AGENTS.expcap.md`.
- `continuous_runtime_recall_injection`: event-driven recall during a
  conversation, primarily through `progressive-recall`.

For compatibility, runtime payloads still expose the legacy `system_prompt`,
`runtime_context`, and `reference_summary` channel fields; those channels map
onto the three injection layers above.

Each `auto-start` / `activate` call also materializes the plan as host-friendly
artifacts under the project memory root:

- `injections/<activation_id>.md`
- `injections/<activation_id>.json`
- `injections/latest.md`
- `injections/latest.json`

These files are runtime artifacts in `$EXPCAP_HOME` (or `.agent-memory/` only
when using the local profile). Claude hooks use the rendered Markdown as
`additionalContext`; other hosts can read `latest.md` or `latest.json` directly.

Assets carry scope and lifecycle metadata:

- `knowledge_scope`: currently `project` or `cross-project`; future `team` and
  `organization` scopes should require stricter promotion gates.
- `knowledge_kind`: `pattern`, `anti_pattern`, `rule`, `context`, `checklist`,
  `past_win`, `preference`, `constraint`, `decision_memory`, `dont_repeat`, or
  `codemap`.
- `temperature`: `hot`, `warm`, `neutral`, or `cool`.
- `review_status`: `healthy`, `watch`, `needs_review`, or `unproven`.

Recommended memory/governance levels:

- `personal / local prior`: local preferences, working style, and dont-repeat
  context that should not silently pollute shared knowledge.
- `project asset`: the default level for repository-specific rules, decisions,
  patterns, and pitfalls.
- `team asset`: cross-project experience with explicit owner, evidence, and
  review.
- `organization asset`: stable company-wide knowledge with version, validity
  window, and deprecation rules.

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

Storage responsibilities are intentionally split:

- `Evidence Store`: the recoverable source of truth for raw traces, task input,
  tool calls, diffs, test results, error logs, activation views, and user
  feedback. In local mode this is JSON / JSONL / logs under the runtime root;
  in team mode it should evolve toward object storage.
- `Curated Markdown Memory`: stable human-readable rules and prompts such as
  `PROJECT_PROMPT.md`, `AGENTS.md`, `AGENTS.expcap.md`, curated memory docs,
  and reviewed codemap/docs material.
- `Governance DB`: lifecycle, state, and relationship management for traces,
  episodes, candidates, assets, activations, review queues, temperature, and
  promotion/deprecation history. Today this is SQLite locally; team mode should
  evolve toward Postgres / Cloud SQL.
- `Semantic Retrieval Layer`: embeddings plus metadata filters for finding
  relevant candidates and assets. Today this is Milvus Lite or hosted Milvus.

One-line boundary:

- Milvus is for retrieval, not truth.
- SQLite is for governance, not semantic understanding.
- Markdown is for reviewability, not large-scale recall.
- Evidence files/logs are the recoverable source of truth.

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

Hosted Milvus is connectable today. When `EXPCAP_RETRIEVAL_BACKEND=milvus` and
`EXPCAP_RETRIEVAL_INDEX_URI` are set, expcap uses that remote Milvus endpoint
instead of a local Milvus Lite DB:

```bash
export EXPCAP_RETRIEVAL_BACKEND=milvus
export EXPCAP_RETRIEVAL_INDEX_URI=https://milvus.example.com
export EXPCAP_RETRIEVAL_INDEX_TOKEN="..."
export EXPCAP_MILVUS_DB_NAME=expcap
export EXPCAP_MILVUS_COLLECTION=experience_assets
```

`EXPCAP_RETRIEVAL_INDEX_TOKEN`, `EXPCAP_MILVUS_DB_NAME`, and
`EXPCAP_MILVUS_COLLECTION` are optional. Object storage and cloud SQL remain
backend-contract fields until their adapters are implemented; the product model
does not change when those adapters are added.

Semantic retrieval items should always stay traceable back to recoverable source
bodies. Milvus can point at trace summaries, episodes, assets, or codemap
chunks, but it should never become the only surviving copy of the memory.

## Status Signals

Use `status` for short-cycle evaluation:

```bash
expcap status --workspace "$PWD"
```

Use `doctor` when you need actionable diagnostics:

```bash
expcap doctor --workspace "$PWD"
expcap benchmark-milvus --workspace "$PWD" --sample-size 5 --limit 3 --include-shared
```

Use `dashboard` when you want a local read-only view of assets, activation
quality, retrieval contribution, candidate review queues, and write frequency:

```bash
expcap dashboard --workspace "$PWD"
```

The command writes `dashboard.html` plus a JSON sidecar under the workspace
review directory for the active storage profile. With the recommended
`user-cache` profile this lives under `$EXPCAP_HOME`, not inside the project.
Copy the printed `saved_to` path into a browser, or use the macOS one-liner in
Quickstart to generate and open it directly.

`benchmark-milvus` pre-syncs the active embedding-profile Milvus index before
querying, so profile switches do not look like retrieval failures. If the
Milvus runtime is unavailable, the benchmark reports that state clearly and can
use a marked state-index fallback for expectation diagnostics.

For codemap/doc recall checks, add expectations:

```bash
expcap benchmark-milvus \
  --workspace "$PWD" \
  --query "README ingest-docs codemap" \
  --expect-kind codemap
```

Watch these fields:

- `activation_feedback_summary`: helped, pending, or stale missing feedback.
- `feedback_cleanup`: stale unresolved activations that were auto-closed as
  `unclear` so metrics stay usable.
- `candidate_review_queue`: candidates that need human review.
- `asset_effectiveness_summary`: asset temperature and review health.
- `retrieval_backends`: Milvus core retrieval readiness and SQLite lightweight
  index health.
- `milvus_benchmark`: sampled Milvus retrieval quality, including provider
  metadata, top scores, and historical selected-asset hit rate.
- `injection_policy_summary`: whether recent activation plans are routing
  items into `system_prompt`, `runtime_context`, or `reference_summary`.
- `project_activity`: whether the workspace is `active` or `inactive` for
  reporting and coverage analysis.
- `backend_configuration`: active local/shareable backend profile.

Milvus is the core retrieval capability. If Milvus Lite is locked or
unavailable, the runtime should degrade to JSON/SQLite so work can continue, but
`doctor` should surface the degradation clearly because retrieval quality is
reduced. `doctor` also reports Milvus lock metadata and safe recovery
recommendations.

## Governance Audit

The current implementation already supports several anti-faulty-memory
mechanisms:

- Raw trace retention: traces, episodes, activation views, and feedback remain
  first-class persisted objects.
- Candidate quarantine by default: task summaries land in `candidate` first and
  promoted assets still carry `unproven` / `watch` / `needs_review` lifecycle
  states.
- Activation provenance: selected assets include `source_provenance`,
  `match_evidence`, `risk_flags`, and `llm_use_guidance`.
- Feedback-driven health: activation feedback updates `temperature` and
  `review_status`.
- Progressive recall gating: `progressive-recall` is explicitly reserved for
  new errors, files/modules, phase changes, or topic drift.

The current implementation is easy to extend but not complete yet in these
areas:

- richer scope tagging such as module, language, framework, task type,
  applicable conditions, and known counterexamples;
- explicit quarantine / deprecation / validity-window fields in the governance
  DB;
- replay-based validation for important assets;
- conflict detection and conflict-aware injection suppression;
- stricter promotion rails for future `team` and `organization` assets.

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
