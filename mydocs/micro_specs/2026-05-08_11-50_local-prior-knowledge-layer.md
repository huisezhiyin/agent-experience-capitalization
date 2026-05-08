# Micro Spec: local-prior knowledge layer

## Goal

Shift expcap from a generic "experience knowledge base" toward a sparse, high-value local-prior layer for coding agents.

The system should not try to store general truths that modern LLMs already know. It should capture the things the model does not reliably know unless the project, team, or user tells it:

- "I succeeded with this approach before; try it again when similar."
- "I prefer working this way."
- "The team has historical constraints and old decisions we must respect."
- "This design exists because of a past incident or migration."
- "Do not make me repeat this instruction every time."
- "This repository has a codemap and operational conventions that should be recalled."

Top-level knowledge can be rare, but it must be valuable enough to change future agent behavior. Lower layers can stay raw and faithful because the consumer is an LLM, not a human-facing knowledge portal.

## Product Thesis

In the LLM era, a project memory system should mostly provide local priors, not universal knowledge.

General engineering truths are already inside the model. The missing layer is local, historical, preference-shaped, and often non-universal:

- Personal habits and defaults.
- Team conventions and long-lived inertia.
- Project-specific architecture and code ownership.
- Historical reasons behind odd designs.
- Repeated instructions the user does not want to restate.
- Past successes and failures that are useful as analogies, not laws.

This makes expcap closer to a "project/team/user prior store" than a traditional wiki or knowledge graph.

## Non-Goals

- Do not build a many-layer ontology for its own sake.
- Do not force every trace or markdown file into polished knowledge.
- Do not treat extracted assets as truth.
- Do not over-promote local workarounds into team/company guidance.
- Do not optimize for human browsing first; optimize for agent retrieval and reasoning.

## Layer Model

### Layer 0: Raw Evidence

Faithful records of what happened.

Examples:

- task traces
- tool logs
- terminal output summaries
- conversation snippets
- diffs and test results
- imported markdown/docs

Requirements:

- Preserve enough context for later re-analysis.
- Do not aggressively summarize away source details.
- Retrieval can return this layer when top-level priors are insufficient.

### Layer 1: Episodes

Task-level structured records.

Examples:

- what was attempted
- what succeeded or failed
- what constraints mattered
- what files or subsystems were involved
- what verification was run

Requirements:

- Be more structured than raw logs.
- Still remain faithful to the actual task.
- Do not pretend a single episode is a general rule.

### Layer 2: Candidate Priors

Potentially reusable local priors awaiting review or proof.

Examples:

- "For this repo, install/runtime changes usually require both CLI flow tests and install-project tests."
- "The user prefers centralized EXPCAP_HOME storage and does not want runtime data committed."
- "Claude hooks should remain trigger-only; runtime owns policy."

Requirements:

- Carry provenance.
- Carry confidence and review status.
- Be easy to reject, merge, or keep unproven.

### Layer 3: Active Local Priors

Sparse, behavior-shaping assets.

These are the highest-value memories. They should be few, clear, and directly actionable by an LLM during a task.

Requirements:

- Must influence future behavior.
- Must be scoped: user, project, team, or cross-project.
- Must retain provenance and evidence.
- Must decay or be reviewed if they stop helping.

## First-Class Prior Kinds

### `past_win`

Use when a previous approach succeeded and should be considered again in similar conditions.

Signal phrases:

- "以前这么做成功了"
- "上次这个路径有效"
- "验证过"
- "这个 repo 里之前就是这样修的"

Activation behavior:

- Present as an analogy, not a mandate.
- Prefer when task, subsystem, and constraints match.

### `preference`

Use for personal or team preferences that shape execution style.

Signal phrases:

- "我喜欢"
- "我习惯"
- "默认用"
- "不要这样"
- "我们团队一般"

Activation behavior:

- Treat as a strong style/default hint.
- Do not override explicit current instructions.

### `constraint`

Use for project/team constraints, risk boundaries, compatibility rules, and operational guardrails.

Signal phrases:

- "必须"
- "不能"
- "线上风险"
- "兼容"
- "团队顾虑"
- "不要提交"

Activation behavior:

- Rank highly when related files or workflows are touched.
- Surface before implementation choices.

### `decision_memory`

Use for historical design reasons.

Signal phrases:

- "历史原因"
- "当时是因为"
- "之前设计成这样"
- "迁移遗留"
- "远古设计"

Activation behavior:

- Explain why current code may look strange.
- Warn before "cleanups" that could erase intentional history.

### `dont_repeat`

Use for instructions the user does not want to keep repeating.

Signal phrases:

- "我不要重复说"
- "以后别再"
- "不用每次问"
- "默认就这样"
- "不要让我再解释"

Activation behavior:

- Treat as a high-priority behavioral prior.
- Keep sparse and reviewable because it can strongly shape future agent behavior.

### `codemap`

Use for repository structure and navigation knowledge.

Signal sources:

- README
- AGENTS.md
- CLAUDE.md
- architecture docs
- module ownership notes
- service maps

Activation behavior:

- Use to orient the agent before code edits.
- Prefer raw/context retrieval over over-compressed top-level summaries.

## Existing Code Gap

Current extraction still mostly maps task outcomes into generic `pattern` and `anti_pattern`. This was good enough for the initial prototype, but it hides the distinction between:

- a reusable technical tactic
- a user preference
- a team constraint
- a historical design reason
- a "do not ask me again" instruction
- a repository codemap

This weakens activation because the rendered context becomes too generic, for example:

```text
[project/pattern] ... 时先用最小验证命令快速定位问题，再扩大修改范围。
```

That is often useful, but it does not capture the highest-value priors the user actually cares about.

## Proposed Runtime Changes

### 1. Add canonical prior kind constants

Add a small central module or constant map for recognized knowledge kinds:

- `pattern`
- `anti_pattern`
- `rule`
- `context`
- `checklist`
- `past_win`
- `preference`
- `constraint`
- `decision_memory`
- `dont_repeat`
- `codemap`

Keep schema compatibility by allowing arbitrary strings, but make these kinds first-class in ranking, rendering, docs, and tests.

### 2. Update candidate extraction

Extend candidate extraction so it can infer local prior kinds from episode text.

Initial heuristic implementation is acceptable:

- success + "以前/上次/验证过/成功" -> `past_win`
- "我喜欢/我习惯/默认用/不要用" -> `preference`
- "必须/不能/兼容/线上/风险/团队顾虑" -> `constraint`
- "历史原因/当时/之前设计/迁移遗留/远古" -> `decision_memory`
- "不要重复/以后别再/不用每次问/不要让我再解释" -> `dont_repeat`
- docs/codemap ingestion path -> `codemap`

Do not require perfect classification in the first pass. Prefer visible, testable behavior over hidden cleverness.

### 3. Update activation ranking

Local priors should shape behavior more strongly than generic patterns when task relevance is similar.

Suggested relative priority:

- highest: `dont_repeat`, `constraint`, `preference`
- high: `decision_memory`, `codemap`
- medium: `past_win`, `rule`, `context`
- normal: `pattern`, `checklist`
- cautionary: `anti_pattern`

Ranking must still respect workspace scope, evidence, historical help, and risk flags. A stale or weakly matched preference should not beat a directly relevant project constraint.

### 4. Update rendered activation context

Render local priors in a way that tells the LLM how to use them.

Examples:

```text
[project/preference] 用户偏好：默认使用集中存储 EXPCAP_HOME=$HOME/.expcap，不把运行数据写入仓库。
```

```text
[project/decision_memory] 历史决策：hooks 只作为触发层；expcap runtime 负责策略、去重和可观测性。
```

```text
[project/dont_repeat] 用户长期指令：不要反复要求用户手工记 expcap 命令，能由 Codex 代跑就代跑。
```

### 5. Add review support

Candidate review should expose local prior kinds clearly.

Minimum improvements:

- show `knowledge_kind` in candidate queue output
- allow filtering by `knowledge_kind`
- add `review-candidates --limit` because current CLI ergonomics already shows this missing

### 6. Add doc/codemap ingestion later

Do not block the taxonomy slice on full document ingestion.

Next slice after taxonomy:

- import `README.md`, `AGENTS.md`, `CLAUDE.md`, architecture docs, and selected markdown files
- store as raw/context/codemap assets
- preserve original text or chunks
- let activation retrieve them and let the LLM summarize on demand

## Implementation Plan

### Phase 1: Taxonomy slice

Deliverables:

- canonical prior kinds
- extraction heuristics for local priors
- ranking weights for prior kinds
- activation rendering examples
- unit tests for classification and ranking

Success criteria:

- a task result containing "我以后不想重复说这个" can produce `dont_repeat`
- a task result containing "历史原因" can produce `decision_memory`
- a task result containing "我喜欢默认这样" can produce `preference`
- activation output preserves these kinds instead of collapsing them into `pattern`

### Phase 2: Review and dashboard visibility

Deliverables:

- candidate queue shows local prior kinds
- status/dashboard summarize prior-kind distribution
- review flow can approve/reject local prior candidates
- add CLI filtering by kind

Success criteria:

- reviewer can quickly find all `dont_repeat` or `preference` candidates
- dashboard can reveal whether the system is creating too many high-priority priors

### Phase 3: Codemap/doc ingestion

Deliverables:

- project markdown ingestion command
- chunking strategy for agent-oriented docs
- `codemap` assets or raw context entries
- retrieval path that can mix sparse priors with raw docs

Success criteria:

- AGENTS/README architecture guidance can be retrieved without manually pasting it
- raw docs remain available even when not promoted into top-level priors

Minimal implementation boundary:

- Add an `ingest-docs` CLI command.
- Default sources: `README.md`, `README.zh-CN.md`, `AGENTS.md`, `CLAUDE.md`, and markdown files under `docs/`.
- Store chunks as active project assets with `asset_type=context` and `knowledge_kind=codemap`.
- Preserve original document text in chunks; do not summarize into polished knowledge.
- Save JSON assets, upsert SQLite asset rows, and upsert Milvus vectors.
- Make the command idempotent by deriving asset ids from relative path and chunk index.
- Do not ingest runtime memory directories, local SQLite, Milvus files, traces, candidates, or `.env`.

### Phase 4: Module refactor

Only after the semantic slice stabilizes, split thick modules:

- extraction logic
- promotion logic
- activation/ranking logic
- diagnostics/status logic
- hook wrapper policy

Success criteria:

- `runtime/core/engine.py` stops owning every semantic decision
- `runtime/cli/main.py` becomes command orchestration, not business logic

## Migration Strategy

Existing assets do not need immediate migration.

Recommended approach:

- keep old `pattern` / `anti_pattern` assets valid
- classify only new candidates with local prior kinds at first
- optionally add a later backfill command to reclassify high-value existing assets
- never bulk-promote old assets into high-priority prior kinds without review

## Risks

### Too many high-priority priors

If every user comment becomes `preference` or `dont_repeat`, activation will become noisy and over-constrained.

Mitigation:

- require stronger textual signals
- keep review status visible
- cap top-level active priors
- rely on feedback and decay

### Overfitting to one task

A single successful task can look like a durable `past_win`.

Mitigation:

- keep it scoped
- present as analogy
- require evidence before marking healthy

### Losing useful raw context

Over-refining can destroy the details LLMs need.

Mitigation:

- keep raw evidence and docs retrievable
- summarize only top-level priors
- let LLM re-analyze raw material during activation

### Confusing local priors with truth

Preferences and historical decisions are not universal laws.

Mitigation:

- always render scope
- preserve provenance
- let current explicit user instructions override stored priors

## Immediate Next Action

Implement Phase 1 first.

This is the smallest high-leverage change because it makes the existing save/get/log loop capture the right semantic object without requiring a new storage backend, new hook system, or large refactor.

Recommended code targets:

- `runtime/core/engine.py`
- `runtime/core/project_policy.py` only if policy needs kind-specific defaults
- `runtime/cli/main.py` only for review/status display
- `tests/test_engine.py`
- `tests/test_cli_flow.py`

Do not start with a broad architecture rewrite. The system already works; it now needs a sharper definition of what is worth remembering.

## Implementation Status

2026-05-08 Phase 1 initial slice:

- Added canonical local-prior kind support in runtime code.
- Added heuristic candidate classification for strong `dont_repeat`, `preference`, `constraint`, `decision_memory`, and `past_win` signals.
- Kept old `pattern` / `anti_pattern` behavior compatible for episodes without strong local-prior signals.
- Added kind-aware ranking weights so sparse behavior-shaping priors can beat generic patterns when relevance is comparable.
- Added kind-aware activation rendering labels such as `用户偏好`, `项目约束`, `历史决策`, `长期指令`, and `历史成功路径`.
- Expanded `review-candidates --knowledge-kind` choices to include canonical local-prior kinds.
- Added tests for extraction, constraint content preservation, ranking, evidence visibility, and activation rendering.

Not done yet:

- No doc/codemap ingestion.
- No backfill or migration for existing assets.
- No broad module split.

2026-05-08 Phase 2 initial observability slice:

- Candidate review queue now includes `knowledge_kind_summary`.
- `status` now reports `knowledge_kind_summary` for assets, candidates, and review queue.
- `status.recent_candidates` now includes `knowledge_kind`.
- `dashboard` cards now include local-prior and high-priority prior asset counts.
- `dashboard` now renders a `Local Prior Distribution` panel covering assets, candidates, and review queue.
- `dashboard` candidate tables now show candidate kind.

Not done yet:

- No doc/codemap ingestion.
- No backfill or migration for existing assets.
- No broad module split.

2026-05-08 Phase 3 planned minimal doc ingestion slice:

- Add `ingest-docs` as a faithful markdown ingestion path.
- Treat imported docs as `codemap` assets, not universal truth.
- Keep chunks retrievable for LLM re-analysis.
- Exclude runtime memory and secret-bearing files.

2026-05-08 Phase 3 initial ingestion slice:

- Added `ingest-docs` CLI.
- Default import sources are `README.md`, `README.zh-CN.md`, `AGENTS.md`, `CLAUDE.md`, and markdown files under `docs/`.
- Explicit `--path` can import a specific markdown file or directory under the workspace.
- Imported chunks are stored as active project assets with `asset_type=context` and `knowledge_kind=codemap`.
- Asset content preserves document path, chunk index, and original chunk text for LLM re-analysis.
- Ingestion writes JSON assets, upserts SQLite asset rows, and upserts Milvus vectors.
- Asset ids are deterministic from relative path, chunk index, and chunk text.
- Runtime memory directories and hidden/cache/vendor directories are excluded.
- Re-running ingestion prunes previous `context_doc_*` assets and stale Milvus vectors before writing the current doc snapshot.
- `codemap` assets are counted in kind distribution, but excluded from proof-quality backlog and unproven validation queue because they are raw context rather than high-level proven knowledge.

Still not done:

- No smart doc section classification beyond chunking.
- No backfill/migration of old docs into `codemap`.

2026-05-08 Phase 4 initial codemap recall validation:

- `benchmark-milvus` now accepts `--expect-kind` and `--expect-source-document` so doc recall quality can be measured explicitly.
- Milvus documents now carry `source_document` for imported doc chunks.
- Activation retrieval now asks Milvus for a wider local/shared candidate set.
- For tasks that explicitly mention docs, README, AGENTS, architecture, or codemap, activation keeps a codemap context slot when Milvus returns a relevant codemap chunk.
- `rendered_context` now renders the final selected assets instead of the pre-selection rerank list, so codemap slots actually reach the LLM context.

Observed result on the real repository:

- `benchmark-milvus --expect-kind codemap` over three doc-oriented queries hit codemap in 3/3 result sets.
- Top-1 codemap rate was only 1/3 with the current hash embedding profile, so real semantic embeddings remain the likely next retrieval-quality upgrade.
- A README/codemap activation now includes `[project/codemap]` in `rendered_context`.

2026-05-08 Phase 5 initial injection policy slice:

- Added a separate injection policy layer because storage shape and prompt injection shape are different decisions.
- Storage layers remain: Milvus semantic retrieval, summarized markdown/local-prior assets, and raw conversation/evidence/doc records.
- Injection channels are now explicit:
  - `system_prompt`: tiny durable priors such as stable preferences, constraints, and `dont_repeat` instructions.
  - `runtime_context`: task-relevant priors and explicit current constraints.
  - `reference_summary`: codemap, background, and larger raw evidence for LLM re-analysis.
- Activation views now include `injection_plan` while keeping legacy `rendered_context` for compatibility.
- Selected assets now expose `injection_channel` so callers can decide how to inject each memory.
- `status` and `dashboard` now summarize injection channel distribution so system/runtime/reference usage is visible.

Still not done:

- No host-specific system prompt writer for Codex or Claude Code yet.
- No real semantic embedding provider in this slice.
