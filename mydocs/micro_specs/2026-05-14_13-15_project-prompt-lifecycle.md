# Spec: project-prompt lifecycle MVP

## Goal
- 要解决什么问题：一旦开始接管项目级 prompt 文件，就不能只做 install-time 模板生成，至少要支持状态查看、候选建议、稳定规则应用。
- 最终目标：把项目级 prompt 真源做成可维护对象。
- 本轮核心目标：落地 `project-prompt status / suggest / apply` 最小闭环。
- 验收结果：可以看到当前 prompt 结构、从稳定资产里拿到建议、并把选中的规则写入 `PROJECT_PROMPT.md` 受管区块。

## Done Contract
- 什么算完成：CLI 新增 `project-prompt` 子命令，包含 `status` / `suggest` / `apply`。
- 由什么证明：CLI 测试覆盖 status/suggest/apply 主路径，真实 dry-run 或真实 apply 可产出受管区块。
- 哪些情况仍算未完成：delete/archive/extract/use/audit 还没做，这轮接受。

## Scope
- In: `PROJECT_PROMPT.md` 受管区块、稳定资产建议、CLI 输出与测试。
- Out: 宿主全覆盖桥接、归档恢复、自学习合并、复杂冲突解决。
- 用户已切分的任务单元：足够小
- 轻量评估：足够小

## Facts / Constraints
- 已确认事实：仓库已存在 `PROJECT_PROMPT.md` 稳定规则层与 `AGENTS.expcap.md` 动态经验层。
- 技术/业务约束：不覆盖用户手写主体内容，只在受管区块内写入 promoted stable rules。
- 已知风险：规则应用如果没有受管边界，容易和人工编辑互相踩踏。

## Restated Understanding
- 我理解当前任务是：把项目级 prompt 从“文件存在”推进到“最小可维护”。
- 当前核心目标是：让稳定规则真源可查看、可建议、可应用。
- 当前边界是：先做最小闭环，不做完整生命周期全量能力。
- 暂不处理：delete/archive/revert/workflow orchestration。

## Checkpoint Summary
- 当前任务理解：新增 `project-prompt status / suggest / apply` CLI。
- 当前核心目标：建立项目级 prompt 真源的最小维护闭环。
- 当前进度：已定位 `project_install`、CLI parser、状态与资产加载入口。
- 下一步 1: 实现受管区块解析与写入。
- 下一步 2: 实现 suggestions 过滤稳定资产。
- 涉及文件 / 模块：`runtime/cli/main.py`、`tests/test_cli_flow.py`
- 风险：建议过滤过宽会把不够稳定的资产推入 prompt 真源。
- 验证方式：`python3 -m unittest tests.test_cli_flow`
- Execution Approval: `Approved`
