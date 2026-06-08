# agent-experience-capitalization CodeMap (project)

## 1. Orientation

- Project: `agent-experience-capitalization`
- Role / responsibility: project-owned, team-shareable experience memory for coding agents.
- Main languages / frameworks: Python package with CLI entrypoint, shell wrappers, JSON schemas, local-first storage.
- Runtime / deployment shape: local CLI (`expcap`), Codex/Claude hook adapters, local JSON + SQLite + Milvus Lite by default, optional shared/cloud backend contract.
- Primary entry types:
  - CLI: `runtime/cli/main.py`, `runtime/cli/__main__.py`, `scripts/expcap`
  - Hooks: `scripts/expcap-hook`, generated hooks from `runtime/core/project_install.py`
  - Skill entry: `skills/expcap/SKILL.md`
  - Tests: `tests/test_*.py`
- Confidence:
  - confirmed: README purpose, package metadata, CLI commands, storage modules, hook installer, core engine path, tests.
  - inferred: runtime feature boundaries are organized around lifecycle commands rather than explicit service layers.
  - unknown: cloud backend implementations beyond config contract are not implemented in the inspected files.

## 2. Progressive Context Tree

```text
agent-experience-capitalization
  -> CLI lifecycle
     -> ingest / review / extract / promote / activate
     -> auto-start / progressive-recall / feedback / auto-finish
     -> status / doctor / dashboard
  -> core engine
     -> trace -> episode -> candidate -> asset
     -> activation and effectiveness feedback
     -> injection policy and materialization
  -> storage terrain
     -> filesystem JSON source
     -> SQLite state index
     -> Milvus semantic retrieval
     -> embedding provider
  -> project integration
     -> install-project
     -> AGENTS.expcap.md sidecar
     -> Codex / Claude hooks
  -> validation
     -> unittest tests/test_*.py
```

## 3. Capability Index

| Capability | Main Modules | Entry | Feature CodeMap | Status |
| --- | --- | --- | --- | --- |
| CLI command surface | `runtime/cli/main.py`, `runtime/cli/__main__.py`, `scripts/expcap` | `expcap` console script / `python -m runtime.cli` | pending | confirmed |
| Experience lifecycle | `runtime/core/engine.py`, `runtime/storage/fs_store.py`, `runtime/storage/sqlite_store.py` | `ingest`, `review`, `extract`, `promote`, `activate` | pending | confirmed |
| Automatic task start / finish | `runtime/cli/main.py`, `runtime/core/injection_materializer.py`, `runtime/storage/sqlite_store.py` | `auto-start`, `auto-finish`, `feedback` | pending | confirmed |
| Continuous recall / injection routing | `runtime/core/injection_policy.py`, `runtime/core/injection_materializer.py` | `progressive-recall`, activation rendering | pending | confirmed |
| Project installation | `runtime/core/project_install.py`, `runtime/core/project_policy.py`, `scripts/install-codex-skill`, `scripts/codex-skill-quickstart` | `install-project` | pending | confirmed |
| Backend configuration | `runtime/backends.py`, `runtime/storage/fs_store.py` | env-driven config resolution | pending | confirmed |
| Semantic retrieval | `runtime/storage/milvus_store.py`, `runtime/storage/embeddings.py` | `sync-milvus`, `benchmark-milvus`, asset search | pending | confirmed |
| Hook event capture | `scripts/expcap-hook`, `runtime/core/hook_activity.py` | Codex/Claude hook events | pending | confirmed |
| Document ingestion as codemap/context assets | `runtime/cli/main.py`, README command docs | `ingest-docs` | pending | inferred |

## 4. Module Index

| Module / Package | Path | Responsibility | Key Dependencies | Risk Notes |
| --- | --- | --- | --- | --- |
| CLI | `runtime/cli/main.py` | Argparse command surface and command handlers for lifecycle, activation, status, doctor, dashboard. | `runtime.core.*`, `runtime.storage.*`, `argparse`, `sqlite3`, `json` | Large module; command handlers may be easier to map as feature maps. |
| Runtime backend config | `runtime/backends.py` | Resolve storage/retrieval/sharing backend roles from env. | `os`, `urllib.parse` | Cloud/shared modes are config-level contracts; implementation depth needs per-feature check. |
| Core engine | `runtime/core/engine.py` | Convert trace to episode/candidate/asset; activation selection; effectiveness metadata. | `runtime.storage.*`, `runtime.core.knowledge_kinds`, injection policy | Scoring and promotion behavior should be verified with tests before change. |
| Injection policy | `runtime/core/injection_policy.py` | Route selected assets into system prompt, runtime context, or reference summary layers. | `knowledge_kinds` | Critical context-budget logic; changes can affect all activation output. |
| Injection materializer | `runtime/core/injection_materializer.py` | Render activation artifacts as JSON/Markdown and hook additional context. | `fs_store`, injection policy constants | Writes to memory roots and fallback paths; watch path behavior. |
| Project installer | `runtime/core/project_install.py` | Write AGENTS sidecar, hooks, project policy, and integration config. | `json`, `Path`, `stat`, `project_policy` | Externally visible file writes; treat as higher-risk. |
| Project policy | `runtime/core/project_policy.py` | Read/write project-level expcap policy. | `json`, `Path` | Not fully inspected in this pass. |
| Hook activity | `runtime/core/hook_activity.py` | Record and load recent hook events. | storage/path utilities | Not fully inspected in this pass. |
| Filesystem store | `runtime/storage/fs_store.py` | Define memory roots, storage layout, JSON paths, project keys, Milvus DB paths. | `Path`, `hashlib`, backend config, embeddings | Path policy is central to not writing runtime data into project unexpectedly. |
| SQLite store | `runtime/storage/sqlite_store.py` | State index tables and CRUD for traces, episodes, candidates, assets, activations. | `sqlite3`, JSON | Schema changes need migration/test care. |
| Milvus store | `runtime/storage/milvus_store.py` | Milvus availability, lock/runtime handling, vector search/sync. | `pymilvus`, embeddings, backend config | Runtime/lock behavior is environment-sensitive. |
| Embeddings | `runtime/storage/embeddings.py` | Hash embedding provider and optional OpenAI embeddings. | `hashlib`, `urllib.request`, env vars | External API path needs key safety and fallback checks. |
| Schemas | `schemas/*.schema.json` | JSON schema contracts for trace, episode, candidate, asset, activation view. | JSON Schema | Not deeply inspected; use when validating payload shape. |
| Tests | `tests/test_*.py` | Unit coverage for CLI flow, engine, backend config, storage/retrieval. | `unittest`, `tempfile`, `patch` | Good validation entry for most changes. |

## 5. Entry Index

- CLI / commands:
  - `pyproject.toml` -> `[project.scripts] expcap = "runtime.cli.main:entrypoint"` (confirmed)
  - `scripts/expcap` -> uses `.venv/bin/python -m runtime.cli` when available, else `python3 -m runtime.cli` (confirmed)
  - `runtime/cli/__main__.py` -> module execution entry (confirmed by file presence, not inspected)
  - `runtime/cli/main.py` -> argparse subcommands (confirmed):
    - `ingest`
    - `ingest-docs`
    - `auto-start`
    - `feedback`
    - `progressive-recall`
    - `auto-finish`
    - `install-project`
    - `sync-milvus`
    - `benchmark-milvus`
    - `dashboard`
    - `review`
    - `extract`
    - `promote`
    - `activate`
    - `explain`
    - `review-candidates`
    - `validation-plan`
    - `save-prior`
    - `status`
    - `doctor`
- Hook adapters:
  - `scripts/expcap-hook` -> accepts `user-prompt-submit`, `stop`, `session-start`, `pre-tool-use`, `permission-request`, `post-tool-use` (confirmed)
  - generated Codex hooks are assembled in `runtime/core/project_install.py` (confirmed)
  - generated Claude hooks are assembled in `runtime/core/project_install.py` (confirmed)
- Skill entry:
  - `skills/expcap/SKILL.md` -> Codex skill instructions for auto-start/auto-finish/status/doctor (confirmed by README and file presence)
- Library exports:
  - `runtime/core/engine.py` exports main lifecycle helpers imported by CLI (confirmed)
  - `runtime/storage/*` modules export storage helpers imported by CLI/core (confirmed)

## 6. Domain And Data Index

- Core domain objects:
  - Trace bundle: raw task facts, commands, errors, result, verification.
  - Episode: reviewed task story with goal, constraints, turning points, attempted paths, lesson, confidence.
  - Candidate: reusable lesson/pattern/rule/context awaiting review/promotion.
  - Asset: promoted reusable project/team memory.
  - Activation view: selected assets for a task plus injection plan and artifacts.
- JSON schemas:
  - `schemas/trace_bundle.schema.json`
  - `schemas/episode.schema.json`
  - `schemas/candidate.schema.json`
  - `schemas/asset.schema.json`
  - `schemas/activation_view.schema.json`
- SQLite tables confirmed in `runtime/storage/sqlite_store.py`:
  - `traces`
  - `episodes`
  - `candidates`
  - `assets`
  - `activation_logs`
- Important enums / statuses:
  - candidate statuses in `runtime/cli/main.py`: `new`, `needs_review`, `approved`, `rejected`, `promoted`
  - integration modes in `runtime/core/project_install.py`: `docs-only`, `codex-hooks`, `claude-hooks`
  - storage profiles in `runtime/backends.py`: `local`, `user-cache`, `shared`, `hybrid`
  - injection channels in `runtime/core/injection_policy.py`: `system_prompt`, `runtime_context`, `reference_summary`
- Config namespaces:
  - `EXPCAP_STORAGE_PROFILE`
  - `EXPCAP_HOME`
  - `EXPCAP_*_BACKEND`
  - `EXPCAP_PROJECT_ID`
  - `EXPCAP_OWNING_TEAM`
  - `EXPCAP_RETRIEVAL_INDEX_URI`
  - `EXPCAP_EMBEDDING_PROVIDER`
  - `EXPCAP_OPENAI_*`
  - `CODEX_HOME`

## 7. External Dependency Index

- RPC / HTTP services:
  - OpenAI embeddings endpoint in `runtime/storage/embeddings.py` when `EXPCAP_EMBEDDING_PROVIDER=openai` (confirmed)
  - remote Milvus URI config in `runtime/storage/milvus_store.py` / `runtime/backends.py` (confirmed config path)
- MQ / events:
  - no MQ found in inspected files.
- Third-party SDKs:
  - `pymilvus` optional dependency for Milvus Lite / Milvus client (confirmed)
- Storage / filesystem:
  - project-local `.agent-memory` for local profile (confirmed)
  - `$EXPCAP_HOME/projects/<project-key>` for user-cache profile (confirmed)
  - `$CODEX_HOME/expcap-memory` shared root (confirmed)
  - generated `.codex/hooks/*` and `.claude/hooks/*` under target project during install (confirmed)
- Auth / permission providers:
  - embedding API key via `EXPCAP_OPENAI_API_KEY` or `OPENAI_API_KEY` (confirmed)
  - Milvus token/user/password env vars in `runtime/storage/milvus_store.py` (confirmed)
- Observability dependencies:
  - dashboard output is static local HTML per README; implementation in `runtime/cli/main.py` needs feature map for details.

## 8. Cross-Module Flows

| Flow | Modules | Entry | Effect | Drill-Down |
| --- | --- | --- | --- | --- |
| Manual lifecycle | `runtime/cli/main.py` -> `runtime/core/engine.py` -> `runtime/storage/fs_store.py` / `sqlite_store.py` | `ingest` / `review` / `extract` / `promote` / `activate` | Builds trace, episode, candidate, asset, and activation artifacts. | Feature CodeMap: lifecycle command chain |
| Task auto-start | `runtime/cli/main.py` -> `engine.activate_assets` -> `injection_policy` -> `injection_materializer` -> storage | `auto-start` | Selects assets, logs activation, writes injection artifacts. | Feature CodeMap: auto-start activation |
| Task auto-finish | `runtime/cli/main.py` -> `engine.build_trace_bundle` / `review_trace_bundle` / `extract_candidates` / promotion helpers -> storage | `auto-finish` | Saves trace/episode/candidate and may auto-promote based on threshold. | Feature CodeMap: auto-finish save |
| Progressive recall | `runtime/cli/main.py` -> activation/search helpers -> injection materializer | `progressive-recall` | Returns delta assets when task context changes. | Feature CodeMap: continuous recall |
| Project install | `runtime/cli/main.py` -> `runtime/core/project_install.py` -> filesystem | `install-project` | Writes sidecar docs, project policy, optional Codex/Claude hooks. | Feature CodeMap: install-project |
| Semantic retrieval sync | `runtime/cli/main.py` -> `runtime/storage/milvus_store.py` -> `runtime/storage/embeddings.py` | `sync-milvus`, `benchmark-milvus` | Syncs/searches asset vectors. | Feature CodeMap: Milvus retrieval |

## 9. Validation Index

- Test commands:
  - likely: `python3 -m unittest discover -s tests -v` (confirmed by README auto-finish example and `tests/` using `unittest`)
  - package CLI smoke: `scripts/expcap --help` (inferred)
  - module CLI smoke: `python3 -m runtime.cli --help` (inferred)
- Test directories:
  - `tests/test_cli_flow.py`
  - `tests/test_engine.py`
  - `tests/test_backends.py`
  - `tests/test_install_project.py`
  - `tests/test_milvus_store.py`
- Smoke paths:
  - local project install: `scripts/expcap install-project --workspace <path>`
  - activation: `expcap auto-start --task "<task>" --workspace "$PWD"`
  - save: `expcap auto-finish --task "<task>" --workspace "$PWD" --verification-status passed --result-status success`
  - status/doctor: `expcap status --workspace "$PWD"`, `expcap doctor --workspace "$PWD"`
- Logs / metrics:
  - activation logs in SQLite `activation_logs`
  - hook events through `runtime/core/hook_activity.py` (not deeply inspected)
  - dashboard generated by CLI (not deeply inspected)
- Known CI checks:
  - unknown in this pass; no CI config inspected.

## 10. Risk Areas

- Risk: `runtime/cli/main.py` is a broad command surface.
  - source: many subcommands and imports in one file.
  - affected capabilities: all CLI lifecycle commands.
  - suggested Feature CodeMap: CLI command dispatch and handler map.
- Risk: storage profile decisions can redirect runtime data into/out of the project.
  - source: `runtime/backends.py`, `runtime/storage/fs_store.py`.
  - affected capabilities: local/user-cache/shared/hybrid storage, hooks, activation artifacts.
  - suggested Feature CodeMap: storage layout resolution.
- Risk: project installer writes visible files and hook scripts.
  - source: `runtime/core/project_install.py`.
  - affected capabilities: `install-project`, Codex/Claude integration.
  - suggested Feature CodeMap: install-project side effects.
- Risk: injection policy controls context budget and what assets enter each layer.
  - source: `runtime/core/injection_policy.py`, `runtime/core/injection_materializer.py`.
  - affected capabilities: auto-start, progressive recall, hook additional context.
  - suggested Feature CodeMap: injection routing.
- Risk: Milvus runtime behavior is environment-sensitive.
  - source: `runtime/storage/milvus_store.py`, `tests/test_milvus_store.py`.
  - affected capabilities: sync/search/benchmark.
  - suggested Feature CodeMap: Milvus runtime and locking.

## 11. Quick File Index

- `README.md`: public purpose, install, workflow, hooks, embedding and storage explanation.
- `pyproject.toml`: package metadata, optional Milvus dependency, `expcap` script entry.
- `runtime/cli/main.py`: main CLI command surface and orchestration.
- `runtime/core/engine.py`: trace -> episode -> candidate -> asset lifecycle and activation logic.
- `runtime/core/injection_policy.py`: asset-to-context-layer routing.
- `runtime/core/injection_materializer.py`: activation JSON/Markdown and hook context rendering.
- `runtime/core/project_install.py`: project installation and hook side effects.
- `runtime/backends.py`: env-driven backend mode resolution.
- `runtime/storage/fs_store.py`: memory root, file layout, project key, Milvus DB path policy.
- `runtime/storage/sqlite_store.py`: SQLite state index schema and CRUD.
- `runtime/storage/milvus_store.py`: Milvus availability, runtime, search/sync.
- `runtime/storage/embeddings.py`: hash/OpenAI embedding providers.
- `scripts/expcap`: shell wrapper for CLI.
- `scripts/expcap-hook`: host hook adapter.
- `skills/expcap/SKILL.md`: Codex-facing operational instructions.
- `tests/test_cli_flow.py`: CLI/fallback/injection behavior tests.
- `tests/test_engine.py`: core lifecycle and promotion behavior tests.
- `tests/test_backends.py`: backend/storage layout tests.

## 12. Feature CodeMap Backlog

Create or update Feature CodeMaps for these when a task needs them:

- `auto-start activation`
  - why: central task-start behavior and injection artifact generation.
  - likely entry: `runtime/cli/main.py` `_handle_auto_start` (symbol inferred from tests/imports; locate before mapping).
  - likely files: `runtime/core/engine.py`, `runtime/core/injection_policy.py`, `runtime/core/injection_materializer.py`, `runtime/storage/sqlite_store.py`.
- `auto-finish save`
  - why: converts task outcome into reusable experience assets.
  - likely entry: `runtime/cli/main.py` auto-finish handler.
  - likely files: `runtime/core/engine.py`, `runtime/storage/fs_store.py`, `runtime/storage/sqlite_store.py`.
- `install-project hooks`
  - why: externally visible file writes and integration modes.
  - likely entry: `runtime/core/project_install.py` `install_project_agents`.
  - likely files: `scripts/install-codex-skill`, `scripts/codex-skill-quickstart`, `AGENTS.expcap.md`.
- `injection routing`
  - why: determines what context reaches model layers.
  - likely entry: `runtime/core/injection_policy.py` `build_injection_plan`.
  - likely files: `runtime/core/knowledge_kinds.py`, `runtime/core/injection_materializer.py`.
- `Milvus retrieval`
  - why: core semantic retrieval and environment-sensitive runtime.
  - likely entry: `runtime/storage/milvus_store.py`.
  - likely files: `runtime/storage/embeddings.py`, `tests/test_milvus_store.py`.

## 13. Maintenance Notes

- Refresh this Project CodeMap when CLI command names, integration modes, storage profiles, injection channels, or validation commands change.
- Do not refresh the whole map for a narrow behavior edit; create or update the relevant Feature CodeMap.
- Treat this map as navigation, not truth. Re-read source files before changing behavior.
