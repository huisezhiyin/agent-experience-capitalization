# Micro Spec: expcap review remediation

## Goal

Fix the top issues from the 2026-04-29 daily expcap review: user-cache write path instability, stale Milvus lock/runtime degradation, and the single pending activation/candidate queue item.

## Scope

- Workspace: `/Users/wuyue/github_project/agent-experience-capitalization`.
- Runtime profile: `EXPCAP_STORAGE_PROFILE=user-cache`, `EXPCAP_HOME=$HOME/.expcap`.
- Prefer operational cleanup and verification first; only change repository code if the behavior remains broken after live checks.

## Plan

1. Verify activation, dashboard, status, doctor, and auto-finish can write to the user-cache runtime directory.
2. Inspect and remove stale Milvus lock files only when metadata proves the owning process is gone.
3. Resolve the pending activation feedback and review or reject the single stale candidate queue item.
4. Re-run dashboard/status/doctor and record the final health snapshot.

## Validation

- `auto-start` wrote the activation view directly to user-cache storage and selected 5/5 assets from Milvus primary with SQLite hydration.
- Removed stale local Milvus lock metadata after confirming the recorded pid was not alive.
- Recorded feedback for both pending activations: the current remediation activation as `supported_strong`, and the older wukong indexing explanation activation as `unclear`.
- Rejected stale operational candidates `cand_20260428_030933_commit-and-push-milvus-f` and `cand_20260429_020608_fix-expcap-daily-review-`.
- `auto-finish` successfully wrote trace, episode, and candidate files under `$HOME/.expcap/projects/...`, confirming the save path now works in this environment.
- Final `doctor` status is `pass`: SQLite healthy, local Milvus ready, candidate review queue 0, pending feedback 0, stale missing feedback 0.
- Final dashboard remains `overall_score=77`, `verdict=healthy`; asset quality improved to `66/87 healthy`, unproven assets dropped to 17, and Milvus contributed selected assets across 84/135 activations.
