# Spec: Fault-Tolerant Governance Repositioning

## Goal
- 要解决什么问题：把 expcap 从“agent memory tool”叙事明确升级为“evidence-backed experience governance layer”。
- 最终目标：统一 README / 中文 README / core principles / architecture 的定位、治理原则、存储职责与能力边界。
- 本轮核心目标：完成文档级重定位，并给出当前实现对 anti-faulty-memory 机制的支持审计。
- 验收结果：关键文档一致表达 fault-tolerant governance 立场，且清楚区分已支持、易扩展、未实现能力。

## Done Contract
- 什么算完成：四份核心文档完成更新，并反映新的产品定位与设计原则。
- 由什么证明：文档 diff 可读、互相一致、能覆盖用户给出的核心设计结论。
- 哪些情况仍算未完成：仍然把 expcap 主要描述成“自动长期记忆”，或仍然混淆 Milvus / SQLite / Markdown / evidence store 职责。

## Scope
- In: `README.md`、`README.zh-CN.md`、`docs/core_principles.md`、`docs/experience_capitalization_architecture.md`
- Out: 运行时代码和 schema 改造；新增冲突检测、quarantine 表结构、team/org 自动晋升逻辑。
- 用户已切分的任务单元：是，已聚焦到设计复盘后的项目优化。
- 轻量评估：足够小，按 `standard` 推进。

## Facts / Constraints
- 已确认事实：当前实现已经支持 provenance、risk_flags、llm_use_guidance、review_status、temperature、candidate->promote、progressive recall gate 等治理方向。
- 技术/业务约束：`runtime/cli/main.py` 与 `tests/test_cli_flow.py` 有未提交改动，本轮不触碰。
- 已知风险：文档会先于实现更完整；需要明确哪些机制是“已支持/易扩展/待实现”，避免过度承诺。

## Open Questions
- [ ] 是否需要在本轮同时更新 `docs/expcap_positioning_zh.md`

## Restated Understanding
- 我理解当前任务是：基于 faulty-memory 论文复盘，重写项目定位与治理叙事，而不是立刻做大规模 schema 重构。
- 当前核心目标是：把“总结不是事实、治理比 consolidation 更重要”写成项目级主叙事。
- 当前边界是：先改核心文档，再把实现缺口标清楚。
- 暂不处理：新增数据库字段、冲突检测执行逻辑、team/org 晋升自动化。

## Goal Alignment Check
- 当前动作是否仍服务于核心目标：是。
- 模型当前路径是否仍在用户边界内：是。
- 是否出现更适合代码地形的水流路径：是，当前实现基础已经足够支撑一次文档级重定位。
- 若否，偏差在哪里：无。
- 是否需要调整本轮目标或范围：不需要。

## Checkpoint Summary
- 当前任务理解：重写核心文档定位与治理章节。
- 当前核心目标：完成 governance-first 叙事与能力审计。
- 当前进度：已读取关键文档和实现入口，确认本轮以文档更新为主。
- 下一步 1: 更新 README 中的定位、fault-tolerant governance 与四职责存储模型。
- 下一步 2: 更新 core principles 与 architecture，加入治理原则、层级 ladder 和机制审计。
- 涉及文件 / 模块：README / docs
- 风险：文档先于实现；需要显式标注能力状态。
- 验证方式：人工审读 diff，确保四份文档一致。
- Execution Approval: `Approved`

## Change Log
- 2026-05-18: 建立 faulty-memory governance 重定位 micro-spec。
- 2026-05-18: 更新 README / README.zh-CN / core_principles / architecture，把项目主叙事改为 fault-tolerant experience governance。
- 2026-05-18: 更新 `PROJECT_PROMPT.md` 与 `AGENTS.expcap.md`，把 governance-first 定位固化到项目级稳定规则。

## Validation
- Self-check: 已核对关键 diff，确认没有触碰用户现有的 `runtime/cli/main.py` 和 `tests/test_cli_flow.py` 未提交改动。
- Static checks: N/A
- Runtime / Test: N/A，本轮仅涉及文档与项目级规则文本。
- Human confirmation: 待用户继续决定是否把缺口转成 code-level 治理改造。
- 结果汇总：
  - `README.md` / `README.zh-CN.md` 已切换为 experience governance / fault-tolerant governance 叙事
  - `docs/core_principles.md` 已增加 fault-tolerant rules、promotion ladder、四职责存储边界
  - `docs/experience_capitalization_architecture.md` 已增加容错优先原则、四类存储职责和当前实现能力审计
  - `PROJECT_PROMPT.md` / `AGENTS.expcap.md` 已固化“不是长期记忆池”的稳定规则
- 核心目标是否已由证据证明完成：是。
- 若未完成，当前剩余差距：代码层还没有补齐更细 scope tagging、conflict detection、replay/validation、quarantine 字段。
- 剩余风险：文档已经领先于当前 schema / runtime 实现，需要后续用 code-level 治理能力追平。

## Resume / Handoff
- 当前状态：文档级重定位已完成。
- 当前卡点：无阻塞；下一轮若继续，需要把“能力审计中的缺口”拆成最小代码改造单元。
- 下一步唯一动作：决定是否继续做 code-level governance 改造。
- 下一轮核心目标：补齐 richer scope tagging、conflict detection、replay/validation、quarantine/deprecate 字段。

## Project Sync Candidates
- 是否发现可复用项目事实：Yes
- 候选事实：
  - `expcap` 的长期价值应表述为 experience governance，而不是自动 memory consolidation。
  - 需要在产品文档里长期固定四职责存储模型与 provenance-first 原则。
- 建议同步位置：
  - `PROJECT_PROMPT.md`
  - expcap 项目文档
- 同步状态：Not synced
