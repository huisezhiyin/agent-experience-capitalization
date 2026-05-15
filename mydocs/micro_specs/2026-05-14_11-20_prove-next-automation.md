# Spec: prove-next automation

## Goal
- 要解决什么问题：当前 unproven backlog 的 proof 需要人工逐条构造 query、检查命中、再手动回写 feedback。
- 最终目标：新增一个最小可用的 `prove-next` CLI，把 proof 批处理入口做出来。
- 本轮核心目标：实现 `validation-plan -> activation -> target-hit validation -> feedback` 的自动化闭环。
- 验收结果：命中目标资产时自动回写 proof；未命中时只记录结果，不自动写强帮助。

## Done Contract
- 什么算完成：新增 `prove-next` 命令，支持按 top-N unproven 资产自动执行 proof。
- 由什么证明：CLI 测试覆盖“命中自动回写”和“未命中不回写”两条核心路径。
- 哪些情况仍算未完成：如果 query 模板质量一般、还不能覆盖所有资产类型，这轮接受。

## Scope
- In: CLI parser、validation-plan 复用、proof query 生成、target hit 校验、结果报告。
- Out: 更复杂的 query learning、邻近命中自动降级成弱信号、dashboard 新展示。
- 用户已切分的任务单元：足够小
- 轻量评估：足够小

## Facts / Constraints
- 已确认事实：现有 `validation-plan` 只出榜单，不执行 proof；`feedback` 已能独立更新 activation 和 asset effectiveness。
- 技术/业务约束：只有命中目标资产时才自动写 `supported_strong`，避免假阳性污染资产池。
- 已知风险：proof query 仍可能命中邻近老资产；这轮先把“命中校验”做严。

## Restated Understanding
- 我理解当前任务是：把现在半自动的 unproven proof 流程收敛成一个最小 CLI 自动化入口。
- 当前核心目标是：减少人工 proof 成本，同时守住资产治理质量。
- 当前边界是：只做最小闭环，不扩写成完整智能 proof orchestration。
- 暂不处理：复杂 query ranking、自学习模板、dashboard 可视化。

## Checkpoint Summary
- 当前任务理解：新增 `prove-next` 命令并复用现有 activation/feedback 机制。
- 当前核心目标：target hit 才自动 proof。
- 当前进度：已定位 parser、validation-plan、feedback 入口。
- 下一步 1: 在 CLI 中实现 `prove-next` handler 与 query builder。
- 下一步 2: 补测试，验证 hit/miss 两条主路径。
- 涉及文件 / 模块：`runtime/cli/main.py`、`tests/test_cli_flow.py`
- 风险：proof query 过宽时会命中邻近资产。
- 验证方式：`python3 -m unittest tests.test_cli_flow`
- Execution Approval: `Approved`
