# Spec: align memory_root_mode with active runtime state

## Goal
- 要解决什么问题：`status.backend_runtime.memory_root_mode` 目前只根据 fallback 目录是否存在来判断，导致主路径已恢复时仍可能显示 `fallback_active`。
- 最终目标：让 backend runtime 面板反映“当前是否真的在用 fallback / 处于主路径退化”，而不是历史遗留目录状态。
- 本轮核心目标：把 `memory_root_mode` 的推导改成基于实际运行信号，并补回归测试。
- 验收结果：主路径健康但 fallback 根目录存在时，`memory_root_mode` 仍为 `primary_only`；真实 fallback sqlite 场景仍保持 `fallback_active`。

## Done Contract
- 什么算完成：`memory_root_mode` 与 `primary_write_health` / `fallback_state_index_in_use` / runtime warnings 对齐，目标测试通过。
- 由什么证明：新增与更新的 CLI status 单测通过。
- 哪些情况仍算未完成：只要 fallback 根目录存在就继续显示 `fallback_active`，或真实 fallback sqlite 被误判回 `primary_only`。

## Scope
- In: `runtime/cli/main.py`、`tests/test_cli_flow.py`
- Out: 主写权限本身修复、Milvus 运行态治理
- 用户已切分的任务单元：是，已聚焦到 runtime state 误报。
- 轻量评估：足够小，按 `standard` 推进。

## Facts / Constraints
- 已确认事实：当前全权限环境中 `primary_write_health=primary_writable`、dashboard `closure_status=closed_loop`，但 `memory_root_mode` 仍显示 `fallback_active`。
- 技术/业务约束：不能影响真实的 fallback sqlite / fallback write 告警；要尽量复用现有 runtime degradation 信号。
- 已知风险：如果只改一处文案、不改状态推导，dashboard/status 仍会给出自相矛盾结论。

## Checkpoint Summary
- 当前任务理解：修正 `memory_root_mode` 的误报逻辑。
- 当前核心目标：区分“fallback 目录存在”和“fallback 正在活跃”。
- 当前进度：已确认误报来自 `_build_status_payload` 的目录存在判断。
- 下一步 1: 提炼 active-state 推导 helper。
- 下一步 2: 补两条状态测试，锁住健康主路径和 fallback sqlite 两种分支。
- 风险：把 `degraded_primary` 与 `fallback_active` 混成一类。
- 验证方式：目标单测 + `py_compile`
- Execution Approval: `Approved`

## Change Log
- 2026-05-21: 新增 `_derive_memory_root_mode(...)`，基于 `primary_write_health`、SQLite fallback 是否真的在用、以及 runtime warning state 推导 `memory_root_mode`。
- 2026-05-21: `backend_runtime.memory_root_mode` 不再因为 fallback 根目录存在就一律显示 `fallback_active`；同时为 `degraded_primary` 增加了后端运行态文案。
- 2026-05-21: 新增 `test_cli_status_keeps_primary_only_when_fallback_root_exists_but_is_not_active`，并补强 fallback sqlite 场景断言。

## Validation
- Self-check: 状态推导只收紧了“active fallback”的判断，没有改动现有 fallback 写入机制。
- Static checks: `python3 -m py_compile runtime/cli/main.py tests/test_cli_flow.py`
- Runtime / Test:
  - `python3 -m unittest tests.test_cli_flow.CliFlowTests.test_cli_status_marks_fallback_sqlite_as_active_backend tests.test_cli_flow.CliFlowTests.test_cli_status_keeps_primary_only_when_fallback_root_exists_but_is_not_active`
  - `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap status --workspace "$PWD"`
- Human confirmation: 未请求额外人工确认。
- 结果汇总：目标 2 条单测通过；真实 `status` 现在返回 `memory_root_mode=primary_only`、`fallback_memory_root_present=true`、`fallback_state_index_in_use=false`、`primary_write_status=primary_writable`。
- 核心目标是否已由证据证明完成：是。
- 剩余风险：本轮修的是 runtime state 误报，不是宿主权限策略本身；若未来受限环境再次触发真实 fallback，状态仍应回到 `fallback_active`。

## Resume / Handoff
- 当前状态：主写闭环相关的“runtime state 假阳性”已收口，并已执行 `auto-finish`。
- 当前卡点：无代码阻塞；剩余高价值工作回到 candidate / governance backlog 消化。
- 下一步唯一动作：下一轮如果继续 expcap 稳定性治理，优先处理 pending validation 或 2 个 `new` candidates。
