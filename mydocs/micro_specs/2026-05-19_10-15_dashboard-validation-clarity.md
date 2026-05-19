## Goal

Clarify the dashboard/status surface so `pending_validation` and `unproven_validation` are visibly different concepts, and reduce low-value review noise if the remaining `new` candidates are obvious duplicates.

## Scope

- `runtime/cli/main.py`
- related tests in `tests/test_cli_flow.py`
- runtime candidate review actions only if evidence shows the 2 pending candidates are redundant

## Risks

- We should not silently change governance semantics; only clarify naming/presentation unless tests show an actual bug.
- We should not promote weak candidates just to clear warnings.

## Done Contract

- Dashboard JSON/summary makes the two validation counts unambiguous.
- Tests cover the clarified fields.
- Candidate queue is only changed if the two pending items are clearly redundant or low-value.
