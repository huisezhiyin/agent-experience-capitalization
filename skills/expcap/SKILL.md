---
name: expcap
description: Use agent-experience-capitalization as a project-owned, team-shareable engineering memory layer for coding agents. Use it to activate prior experience before work, save reusable lessons after work, inspect save/get/log health, and diagnose Milvus-centric retrieval.
---

# Expcap

## Positioning

This skill is the recommended entrypoint. The `expcap` CLI is the execution
layer behind it.

`expcap` does not compete with personal memory in Codex, Claude Code, or other
agents. It focuses on project-level, team-level, and organization-level
engineering experience assets: shareable, reviewable, and deliverable with the
codebase.

Default runtime profile:

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap"
```

- Assets remain project-owned even when stored outside the project directory.
- Runtime data goes under `$HOME/.expcap/projects/...` by default.
- Milvus is the core semantic retrieval layer.
- SQLite is a lightweight state index, activation log, and fallback path.
- Activation returns sourced candidates; the current agent decides whether each
  asset applies to the task.
- Only `active` projects should auto-run `expcap auto-start`; dormant projects
  can stay installed but marked `inactive`.

## When To Use

- Before coding work, activate relevant project experience.
- After completed work, save reusable lessons, rules, patterns, or context.
- When checking usage, activation feedback, asset temperature, review status,
  candidate queues, or new assets.
- When validating the save/get/log loop.
- When diagnosing Milvus, SQLite, centralized storage, or shared backend
  configuration.
- When installing project-owned/team-shareable memory into another repository.

## Default Commands

Start a task by activating experience:

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap auto-start --task "<task summary>" --workspace "$PWD"
```

Finish a task by saving experience:

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap auto-finish --task "<task summary>" --workspace "$PWD" --verification-status passed --result-status success
```

Check runtime health:

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap status --workspace "$PWD"
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap doctor --workspace "$PWD"
```

Sync the Milvus retrieval index:

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap sync-milvus --workspace "$PWD" --include-shared
```

Install into another project:

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap install-project --workspace /path/to/project
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap install-project --workspace /path/to/project --project-status inactive
```

## Operating Rules

- Run `auto-start` before substantive analysis, edits, or verification.
- Skip default `auto-start` when the project is marked `inactive`; that keeps
  dormant repositories from generating noisy activation logs.
- If experience is activated, summarize what matched, why it matched, and how
  it affects the current strategy.
- Run `auto-finish` after a coherent task is complete or a stable lesson has
  emerged.
- Do not save when the task is unresolved, the conclusion is unstable, the
  change is only a temporary workaround, verification is missing, or the user
  asked not to record.
- Treat repository-specific experience as `project` scope by default.
- Promote to `cross-project` only after the lesson has been validated across
  more than one project.
- Store project conventions, historical decisions, and directory constraints as
  `context` or `rule` assets when they guide future work.
- Do not make users memorize commands. Run the workflow for them and report the
  result, source, and risk.

## Diagnostics

Watch these status fields:

- `activation_feedback_summary`: whether activations helped.
- `feedback_cleanup`: stale unresolved activations that were auto-closed as
  `unclear`.
- `candidate_review_queue`: candidates waiting for review.
- `asset_effectiveness_summary`: asset temperature and review status.
- `retrieval_backends`: Milvus core retrieval readiness and SQLite lightweight
  index health.
- `project_activity`: whether the current workspace is active for default
  auto-start behavior.
- `backend_configuration`: active `local`, `user-cache`, `shared`, or `hybrid`
  profile.

If Milvus Lite is locked or unavailable, the runtime may degrade to JSON/SQLite
so work can continue. Treat that as reduced retrieval quality and prioritize
Milvus recovery for meaningful testing.
