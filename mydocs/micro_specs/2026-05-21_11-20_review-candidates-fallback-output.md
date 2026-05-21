# Spec: review-candidates fallback output

## Goal
- 要解决什么问题：`review-candidates` 在默认输出路径不可写时会直接失败，而 `dashboard/status/doctor` 已经具备 fallback 写出能力。
- 最终目标：让 `review-candidates` 的默认写出行为与其它 review/diagnostic 命令保持一致。
- 本轮核心目标：为 `review-candidates` 接入默认输出 fallback，并补一条针对性回归测试。
- 验收结果：默认 reviews 路径不可写时命令仍成功，且返回 `save_warning` 与 fallback `saved_to`。

## Done Contract
- 什么算完成：`review-candidates` 默认输出不可写时自动写入 fallback review 路径；显式 `--output` 仍保留原有失败语义。
- 由什么证明：目标单测通过，必要时再做一次真实 CLI 验证。
- 哪些情况仍算未完成：仍然直接抛 `PermissionError`，或 fallback 生效但缺少结构化 warning。

## Scope
- In: `runtime/cli/main.py`、`tests/test_cli_flow.py`
- Out: 更大的 review queue 治理逻辑、主写权限修复
- 用户已切分的任务单元：是，已聚焦到单个 CLI 输出退化问题。
- 轻量评估：足够小，按 `standard` 推进。

## Facts / Constraints
- 已确认事实：今日 daily review 中 `review-candidates` 默认写到 `~/.expcap/.../reviews/candidate_review_queue.json` 时直接失败；显式 `--output /private/tmp/...` 可成功。
- 技术/业务约束：要复用现有 fallback helper，避免引入新的输出分支；不能破坏显式输出路径的 fail-fast 行为。
- 已知风险：如果测试只覆盖 happy path，容易遗漏 `save_warning` 结构和 fallback 路径形态。

## Restated Understanding
- 我理解当前任务是：直接修掉 `review-candidates` 缺少 fallback 的实现缺口，而不是继续汇报问题。
- 当前核心目标是：让 review 队列命令在默认路径不可写时也具备降级可用性。
- 当前边界是：只做最小代码修复和回归测试，不扩展到其它权限问题。

## Checkpoint Summary
- 当前任务理解：补 `review-candidates` 默认输出 fallback。
- 当前核心目标：和 `dashboard/status/doctor` 对齐输出退化行为。
- 当前进度：已定位到 `_handle_review_candidates` 直接 `save_json(output_path, queue)`。
- 下一步 1: 接入 `_save_review_json`。
- 下一步 2: 增加默认输出不可写时的单测。
- 风险：误改显式 `--output` 语义。
- 验证方式：目标单测 + `py_compile`
- Execution Approval: `Approved`

## Change Log
- 2026-05-21: 为 `_handle_review_candidates` 接入 `_save_review_json`，默认输出不可写时自动回退到 fallback review 路径，并保留显式 `--output` 的 fail-fast 行为。
- 2026-05-21: 新增 `test_cli_review_candidates_falls_back_when_default_output_is_unwritable`，锁定 `save_warning`、fallback `saved_to` 和成功返回语义。

## Validation
- Self-check: 复用了现有 review 输出 fallback helper，没有引入新的写出分支。
- Static checks: `python3 -m py_compile runtime/cli/main.py tests/test_cli_flow.py`
- Runtime / Test: `python3 -m unittest tests.test_cli_flow.CliFlowTests.test_cli_review_candidates_falls_back_when_default_output_is_unwritable tests.test_cli_flow.CliFlowTests.test_cli_dashboard_falls_back_when_default_json_sidecar_is_unwritable`
- Human confirmation: 未额外请求人工确认。
- 结果汇总：2 条目标测试通过，默认 review queue 输出已具备 fallback 能力。
- 核心目标是否已由证据证明完成：是。
- 剩余风险：本轮未处理 `~/.expcap` 主写权限本身；只是保证 review-candidates 在默认路径不可写时可降级成功。

## Resume / Handoff
- 当前状态：实现与回归测试已完成，并已执行 `auto-finish` 沉淀经验。
- 当前卡点：无代码阻塞；若继续 daily review 路线，下一步更适合回到主写权限恢复和治理积压消化。
- 下一步唯一动作：下次遇到受限环境时，确认 `review-candidates` 是否和 `dashboard/status/doctor` 一样返回 fallback warning。
