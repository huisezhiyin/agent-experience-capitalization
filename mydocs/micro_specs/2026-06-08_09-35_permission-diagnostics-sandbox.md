# Permission Diagnostics Sandbox Clarity

## Goal

Optimize expcap permission diagnostics so `doctor/status/review-maintenance` can explain when primary write failures are likely caused by the current agent/runtime sandbox rather than by broken `.expcap` ownership or chmod state.

## Scope

- Touch only runtime diagnostic/reporting code and focused tests.
- Do not change persistence fallback behavior.
- Do not add deep retrieval checks or change Milvus indexing behavior.

## Done Contract

- Primary write health exposes enough structured detail to distinguish permission-like write probe failures from confirmed path ownership/mode problems.
- Doctor recommendations tell users to verify outside the current sandbox before changing filesystem permissions when probe failures look environment-induced.
- Existing healthy primary path behavior remains unchanged.
- Focused unit tests pass.

## Checkpoint

- Existing code already marks `permission_induced`, but the wording still says “restore writable access to ~/.expcap,” which misled the automation after a workspace-write sandbox run.
- The implementation should prefer clearer cause labels and recommendations without treating heuristics as absolute truth.

## Change Log

- Added `permission_snapshot`, `write_block_class`, and `diagnostic_hint` to primary write health.
- Classified write probe failures into `environment_or_acl_restriction`, `mixed_permission_and_environment_restriction`, `filesystem_permission`, `permission_or_sandbox`, or `runtime_failure`.
- Updated doctor recommendations to tell users to verify in a less restricted shell/session before chmod/chown when mode bits already look writable.
- Exposed the new block class and diagnostic hint in the dashboard backend runtime panel.
- Added regression tests for environment/sandbox-like failures and real filesystem permission failures.

## Validation

- `python3 -m unittest tests.test_cli_flow.CliFlowTests.test_build_primary_write_health_reports_primary_writable tests.test_cli_flow.CliFlowTests.test_build_primary_write_health_reports_fallback_only_when_all_probes_fail tests.test_cli_flow.CliFlowTests.test_build_primary_write_health_classifies_filesystem_permission_when_mode_blocks_user tests.test_cli_flow.CliFlowTests.test_build_persistence_summary_reports_degraded_success_for_fallback_only tests.test_cli_flow.CliFlowTests.test_cli_doctor_warns_when_primary_write_path_is_fallback_only tests.test_cli_flow.CliFlowTests.test_cli_doctor_describes_permission_induced_milvus_probe_degradation` passed.
- `python3 -m py_compile runtime/cli/main.py` passed.
- `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap doctor --workspace "$PWD"` passed with primary write and local Milvus checks healthy.
- `python3 -m unittest tests.test_cli_flow` passed: 125 tests in 94.426s.

## Resume

- Current goal is complete. Remaining doctor warnings are governance backlog/asset review quality, not permission diagnostics.
