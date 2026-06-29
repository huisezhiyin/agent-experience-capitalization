# 2026-06-29 expcap review maintenance governance convergence

- Goal: after a maintenance action deprecates or quarantines an ignored asset, status/doctor should not keep recommending that same non-actionable asset as the top governance backlog item.
- Scope: governance queue aggregation and doctor recommendation wording only; do not mutate assets or change retrieval behavior.
- Done: focused unit tests cover ignored/deprecated items being excluded from actionable pending counts/recommendations; lightweight status/doctor commands still run.

## Result

- Changed governance queue ordering so actionable replay/review items rank ahead of ignored/quarantined/deprecated audit items.
- Changed doctor recommendation selection to prefer actionable governance items when pending validation remains.
- Verified with focused unit tests plus lightweight `review-maintenance`, `status`, and `doctor`.
