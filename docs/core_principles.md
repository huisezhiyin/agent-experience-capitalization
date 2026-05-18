# Core Principles

## North Star

`expcap` does not compete with Codex, Claude Code, or other assistant-level
personal memory systems.

Its core purpose is to turn coding-agent work into project-owned, team-shareable,
deliverable engineering experience assets with evidence, scope, lifecycle, and
feedback governance.

Short name: **TEAM memory** — **Transferable Engineering Asset Memory**.
Preferred product framing: **fault-tolerant experience governance layer**.

## What This Means

- The primary owner of an asset is a project, team, or organization, not a
  person's model account.
- Assets should be portable across agents, machines, teammates, and deployment
  environments.
- Local mode is valid for solo teams, offline development, and fast testing.
- Cloud/shared mode is the natural target for team, company, and deliverable
  engineering memory.
- The product boundary is experience governance: evidence, extraction,
  candidate review, promotion, retrieval, activation, feedback, and decay.
- Raw evidence stays first-class even after abstractions are produced.
- Retrieval should return sourced candidates, not commands.
- LLM consolidation should create candidates, not truth.

## What This Is Not

- Not a replacement for Codex or Claude Code personal memory.
- Not a generic chat memory store.
- Not a private preference profile bound to one user.
- Not just a vector database wrapper.
- Not an auto-compressing memory-consolidation pile.

## Fault-Tolerant Governance Rules

- Raw trace is never replaced by abstraction.
- Consolidation creates candidates, not truth.
- Promotion requires evidence, review, and scope.
- Retrieval returns sourced candidates, not commands.
- Activated assets must receive feedback, decay, quarantine, or deprecation.
- Cross-task contamination should be prevented by default.
- Abstract assets must remain grounded in recoverable evidence.

## Asset Levels

- `personal / local prior`: Local preferences, collaboration boundaries, and
  dont-repeat context that should not silently pollute shared memory.
- `project`: Default asset level. Captures decisions, patterns, rules, and
  lessons that belong to one repository or product.
- `team`: Shared asset level. Captures experience validated across multiple
  projects or owned by a team.
- `organization`: Future asset level. Captures stable engineering knowledge
  that a company wants to preserve and transfer across teams.

## Promotion Ladder

- `trace -> episode -> candidate -> project asset -> team asset -> organization asset`

Each step upward should require stronger evidence, clearer scope, and stricter
review. Team- or organization-level assets should never be auto-promoted from a
single task without additional governance.

## Storage Philosophy

The storage backend is replaceable, but the asset contract is not.

- Evidence files / logs are the recoverable source of truth for raw traces,
  task input, tool calls, diffs, tests, errors, activation views, and feedback.
- Curated Markdown memory is the human-readable and reviewable layer for stable
  rules, prompts, docs, and curated memory notes.
- Milvus is the core semantic retrieval capability, not the source of truth.
- SQLite is a lightweight governance DB: state index, review log, activation
  log, relationship store, and fallback metadata layer.
- Solo/local mode can use `.agent-memory/`, SQLite, and Milvus Lite, but
  Milvus Lite should still be treated as the primary retrieval path.
- Project ownership does not require project-directory storage. A project can
  own assets whose source of truth lives in a user cache or shared backend.
- Team mode should support shared asset stores, shared state indexes, and shared
  vector retrieval.
- Retrieval should expose provenance so agents can judge whether a shared asset
  applies to the current task.
- Cloud backends should make assets easier to share and deliver, not make local
  development impossible.
- Local and cloud modes should share the same asset contract. Cloud adoption
  should be a backend configuration change, not a product rewrite.
- Markdown should not be overloaded as the large-scale retrieval substrate.
- SQLite should not be overloaded as the semantic understanding layer.
- LLM summaries should never be allowed to erase the underlying evidence.
