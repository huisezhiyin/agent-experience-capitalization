# Core Principles

## North Star

`expcap` does not compete with Codex, Claude Code, or other assistant-level
personal memory systems.

Its core purpose is to turn coding-agent work into project-owned, team-shareable,
deliverable engineering experience assets.

Short name: **TEAM memory** — **Transferable Engineering Asset Memory**.

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

## What This Is Not

- Not a replacement for Codex or Claude Code personal memory.
- Not a generic chat memory store.
- Not a private preference profile bound to one user.
- Not just a vector database wrapper.

## Asset Levels

- `project`: Default asset level. Captures decisions, patterns, rules, and
  lessons that belong to one repository or product.
- `team`: Shared asset level. Captures experience validated across multiple
  projects or owned by a team.
- `organization`: Future asset level. Captures stable engineering knowledge
  that a company wants to preserve and transfer across teams.

## Storage Philosophy

The storage backend is replaceable, but the asset contract is not.

- Solo/local mode can use `.agent-memory/`, SQLite, and Milvus Lite.
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
