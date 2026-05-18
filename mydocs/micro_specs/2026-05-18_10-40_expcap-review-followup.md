# Spec: Repair Expcap Review Follow-up

## Goal
- 要解决什么问题：把 daily review 里剩余的主写闭环、Milvus probe、unproven 验证三件事真正落地。
- 最终目标：确认当前环境下 save/get/log 与 Milvus 探针的真实状态，并只在确有缺口时改代码。
- 本轮核心目标：先用真实命令验证，再决定是否需要代码修复，并消化最高价值 unproven 资产。
- 验收结果：`status/doctor/dashboard/auto-finish` 证据一致，且产出明确结论或修复。

## Done Contract
- 什么算完成：主写闭环与 Milvus probe 已验证，必要代码已修复，至少处理一批高价值 unproven 资产。
- 由什么证明：CLI 实跑结果、针对性测试、`auto-finish` 回写结果。
- 哪些情况仍算未完成：仍存在未解释的主写退化、Milvus probe 误报，或未推进 unproven 队列。

## Scope
- In: `runtime/cli/main.py`、`runtime/storage/milvus_store.py`、相关测试、真实 CLI 验证。
- Out: 与当前闭环无关的架构改造、embedding provider 升级。
- 用户已切分的任务单元：是，已聚焦到 review follow-up。
- 轻量评估：足够小，按 `standard` 推进。

## Facts / Constraints
- 已确认事实：昨天在受限环境里主写探针和 Milvus runtime probe 都显示 permission/sandbox-induced degrade。
- 技术/业务约束：不能覆盖用户现有未提交改动；先看真实运行证据再决定是否修改实现。
- 已知风险：当前全权限环境可能已经恢复正常，导致“问题”只是环境特性而非代码缺陷。

## Open Questions
- [ ] 当前全权限环境下是否还会出现 `fallback_only` 或 `unix_socket_bind_unavailable`
- [ ] 最高优先 unproven 资产能否通过真实任务获得更明确反馈

## Restated Understanding
- 我理解当前任务是：把 review 里建议的 3 个动作直接执行，而不是只做汇报。
- 当前核心目标是：先恢复或验证闭环，再把结果沉淀进经验层。
- 当前边界是：优先真实验证和小范围修复，不扩展到大规模重构。
- 暂不处理：长期 embedding 质量升级。

## Goal Alignment Check
- 当前动作是否仍服务于核心目标：是，先用 CLI 验证主写与 Milvus 真实状态。
- 模型当前路径是否仍在用户边界内：是。
- 是否出现更适合代码地形的水流路径：是，真实验证表明本轮重点应从“修代码”转为“确认环境差异并推进 unproven 消化”。
- 若否，偏差在哪里：昨天的退化告警来自受限环境，不是当前运行时逻辑故障。
- 是否需要调整本轮目标或范围：已收缩为证据确认 + `prove-next` 消化。

## Checkpoint Summary
- 当前任务理解：执行 review follow-up 三件事。
- 当前核心目标：验证主写闭环和 Milvus probe 的真实状态，并推进 unproven 验证。
- 当前进度：已读取记忆、skill、相关代码与现有 diff。
- 下一步 1: 运行 status/doctor/dashboard/auto-finish 获取当前环境证据。
- 下一步 2: 如果仍退化，最小范围修复并补测试。
- 涉及文件 / 模块：`runtime/cli/main.py`、`runtime/storage/milvus_store.py`、`tests/test_cli_flow.py`、`tests/test_milvus_store.py`
- 风险：把环境限制误判成产品 bug，或反过来漏掉真实缺陷。
- 验证方式：真实 CLI + 目标单测。
- Execution Approval: `Approved`

## Change Log
- 2026-05-18: 建立 follow-up micro-spec，先验证再决定改动范围。
- 2026-05-18: 在全权限环境实跑 `status/doctor/dashboard/auto-finish`，确认主写闭环与 Milvus probe 均恢复正常。
- 2026-05-18: 运行 `validation-plan` 与 `prove-next --limit 4`，为 4 条 unproven 资产自动回写 `supported_strong`。

## Validation
- Self-check: 已确认本轮无需额外代码修复，避免对用户现有未提交改动造成干扰。
- Static checks: 未新增代码，无需补跑针对性单测。
- Runtime / Test:
  - `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap status --workspace "$PWD"`
  - `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap doctor --workspace "$PWD"`
  - `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap dashboard --workspace "$PWD"`
  - `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap auto-finish --task "修复 expcap 主写闭环退化并推进 unproven 与 Milvus probe 优化" --workspace "$PWD" --verification-status passed --result-status success`
  - `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap validation-plan --workspace "$PWD" --limit 3`
  - `EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap prove-next --workspace "$PWD" --limit 4`
- Human confirmation: 当前未请求额外人工确认，依据 CLI 实跑结果收口。
- 结果汇总：
  - `dashboard` persistence 已从 `degraded_success` 恢复为 `closed_loop`
  - `doctor` 最终 `overall_status=pass`
  - `primary_write_path=pass`
  - `local_milvus=pass`
  - `activation_feedback` 更新为 `strong=175 weak=9 pending=0 stale_missing=0`
  - `unproven` 从 `47` 降到 `43`，队列从 `46/47` 左右降到 `42`
- 核心目标是否已由证据证明完成：是。
- 若未完成，当前剩余差距：无本轮阻塞；后续仍可继续批量消化剩余 unproven。
- 剩余风险：受限沙箱环境下仍会看到 permission/sandbox-induced degrade，这属于环境特性而非当前全权限运行缺陷。

## Resume / Handoff
- 当前状态：本轮 follow-up 已完成，结论已沉淀到主存储路径。
- 当前卡点：无阻塞；后续若继续治理，可直接处理新的 unproven 队首资产。
- 下一步唯一动作：下一次 daily review 时继续对新的 unproven 队首做 `prove-next` 或真实任务验证。
- 下一轮核心目标：继续降低 unproven 比例，同时避免把受限环境退化误判为产品故障。

## Project Sync Candidates
- 是否发现可复用项目事实：Yes
- 候选事实：
  - daily review 中看到的 `fallback_only` / `unix_socket_bind_unavailable` 需要先区分运行环境是否受限，再决定是否进入代码修复路径。
  - `prove-next --limit 4` 能在不手工挑资产的情况下，同时覆盖“刚生成资产 + 3 条高价值 backlog”这一类日常消化场景。
- 建议同步位置：
  - `PROJECT_PROMPT.md`
  - Project memory / expcap asset
- 同步状态：Not synced
