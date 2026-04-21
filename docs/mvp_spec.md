# Agent Experience Capitalization MVP Spec

## 1. 文档目标

本文档用于把当前研究型架构稿收敛为第一版可实现的 MVP 规格。

它回答的不是“最终系统应该有多完整”，而是：

- 第一阶段到底要做什么
- 第一阶段明确不做什么
- 需要先稳定哪些对象模型
- 本地 runtime 的最小能力面是什么
- skill / plugin / runtime 在 MVP 中如何分工

本文档默认面向 `Codex` 与 `Claude Code` 这类 coding agent 宿主。


## 2. MVP 目标

MVP 只验证一件事：

> agent 的历史任务经验，是否可以被稳定地提炼成可复用资产，并在新任务开始时以最小上下文包的形式被有效激活。

但这句话需要收紧成两个更硬的目标：

- `自动化`：默认自动发生，而不是主要依赖用户手工提醒 agent 去 `get/save`
- `有效强化`：激活出来的经验必须能帮助当前任务，而不是只完成一次“看起来有命中”的检索

因此，第一阶段不追求“完全自动自学习”，但必须追求一个可靠、可解释、可审查、并且尽量自动触发的最小闭环。

这个闭环是：

```text
task end
  -> collect trace bundle
  -> distill episode
  -> extract candidates
  -> score candidates
  -> persist

next task start
  -> activate top assets
  -> render minimal context pack
```


## 3. MVP 非目标

以下事项明确不属于第一阶段：

- 不做完整 agent 平台
- 不做常驻 daemon 或复杂服务编排
- 不做全量对话长期索引
- 不做全自动 skill 大量生成
- 不追求多宿主深度统一 SDK
- 不默认采集所有 shell 输出与细粒度编辑行为
- 不把 candidate 自动直接晋升为高权重长期资产


## 4. MVP 成功标准

MVP 是否成功，不以“接入了多少项目”作为首要标准，而以“是否形成自动且有效的强化闭环”作为首要标准。

第一阶段至少要回答下面 4 个问题：

- 任务开始时，agent 能否默认自动激活相关经验
- 任务结束时，agent 能否默认自动沉淀候选经验
- 被激活的经验是否与当前任务真正相关
- 这些经验是否真的改变了执行策略、减少重复劳动、避免错误路径

如果只有存储，没有默认触发，不算成功。

如果只有触发，没有帮助，不算成功。

如果命中结果经常错误或污染上下文，也不算成功。


## 5. MVP 设计结论

MVP 推荐形态：

- `light skill`
- `CLI runtime`
- `local files + SQLite`
- `manual review-friendly`

职责分工如下：

### 5.1 Runtime

负责真正的业务后端能力：

- 接收 trace bundle
- 生成 episode
- 生成与评分 candidate
- 存储文件与索引
- 按任务生成 activation view
- 解释某条经验的来源与命中原因

### 5.2 Skill

负责 agent-facing 接口：

- 判断何时调用 runtime
- 组织输入参数
- 消费 runtime 输出
- 将 activation view 以宿主友好方式注入上下文

### 5.3 Plugin / Host Adapter

在 MVP 中可先弱化为本地伴随层，优先承担：

- 任务开始 / 结束事件感知
- 本地材料采集
- 调用 runtime

第一阶段允许它不是重型官方插件，只要能拿到最小输入即可。


## 6. MVP 闭环边界

### 6.1 输入

MVP 必须能拿到以下信息：

- 用户请求
- 用户补充约束
- 关键命令摘要
- 涉及文件列表
- 最终 diff
- 验证结果
- 最终结果状态

### 6.2 输出

MVP 需要产出以下 4 类核心对象：

- `trace_bundle`
- `episode`
- `candidate`
- `activation_view`

### 6.3 第一阶段只激活高层资产

新任务开始时，默认只激活：

- `rule`
- `pattern`
- `anti_pattern`

第一阶段不默认把完整 `episode` 或 `raw trace` 注入上下文，只保留引用能力。


## 7. 核心对象模型

第一阶段最重要的不是代码框架，而是先稳定对象模型。

### 7.1 Trace Bundle

作用：

- 承接宿主侧异构输入
- 为后续 episode 提炼提供统一原材料

建议字段：

```json
{
  "trace_id": "trace_20260412_001",
  "host": "codex",
  "workspace": "/abs/path/to/workspace",
  "session_id": "sess_abc",
  "task_hint": "fix pytest import error",
  "user_request": "修复 pytest 导入错误，并确保测试通过",
  "constraints": [
    "不要改 public API",
    "优先最小改动"
  ],
  "events": [
    {
      "type": "command",
      "content": "uv run pytest tests/test_imports.py",
      "important": true
    },
    {
      "type": "error",
      "content": "ModuleNotFoundError: no module named foo",
      "important": true
    }
  ],
  "files_changed": [
    "pkg/module.py",
    "tests/test_imports.py"
  ],
  "verification": {
    "commands": [
      "uv run pytest tests/test_imports.py"
    ],
    "status": "passed",
    "summary": "1 passed"
  },
  "result": {
    "status": "success",
    "summary": "修复导入路径并补充回归测试"
  },
  "artifacts": {
    "diff_path": ".agent-memory/traces/diffs/trace_20260412_001.diff"
  },
  "timestamps": {
    "started_at": "2026-04-12T10:00:00Z",
    "ended_at": "2026-04-12T10:18:00Z"
  }
}
```

约束：

- `events` 只保留关键事件，不做全量流水
- `verification.summary` 必须是压缩摘要，不存整份原始输出
- 大文本材料以文件引用形式挂在 `artifacts`

### 7.2 Episode

作用：

- 把一次任务整理成结构化案例
- 作为 candidate 提炼的直接来源

建议字段：

```json
{
  "episode_id": "ep_20260412_001",
  "trace_id": "trace_20260412_001",
  "goal": "修复 pytest 导入错误",
  "constraints": [
    "不要改 public API",
    "优先最小改动"
  ],
  "workspace": "/abs/path/to/workspace",
  "files_touched": [
    "pkg/module.py",
    "tests/test_imports.py"
  ],
  "commands": [
    "uv run pytest tests/test_imports.py"
  ],
  "turning_points": [
    "发现导入失败来自包根路径假设错误",
    "通过显式包内导入修复"
  ],
  "attempted_paths": [
    "先尝试修改 test path",
    "后改为修复模块导入方式"
  ],
  "abandoned_paths": [
    "未采用通过改 PYTHONPATH 的方式规避"
  ],
  "decision_rationale": [
    "优先修正代码真实导入关系，而不是依赖执行环境补丁"
  ],
  "result": "success",
  "verification": "pytest passed",
  "user_feedback": "accepted",
  "lesson": "导入类错误优先修复包结构或导入路径，不优先依赖运行环境兜底",
  "scope_hint": "python-import-error",
  "confidence_hint": 0.78
}
```

约束：

- `lesson` 必须可读、可审查
- `turning_points` 只记录关键转折，不做流水账
- `confidence_hint` 只是提示，不等同于晋升后的资产置信度

### 7.3 Candidate

作用：

- 作为经验进入长期层之前的制度化缓冲区
- 避免一次总结直接污染长期资产层

建议字段：

```json
{
  "candidate_id": "cand_20260412_001",
  "source_episode_ids": [
    "ep_20260412_001"
  ],
  "candidate_type": "pattern",
  "title": "优先修复真实导入关系而不是依赖环境补丁",
  "content": "遇到 Python 导入错误时，优先检查包结构和模块引用关系；只有确认是运行器配置问题时才考虑 PYTHONPATH 类兜底。",
  "reusability_score": 0.82,
  "stability_score": 0.70,
  "confidence_score": 0.76,
  "constraint_value_score": 0.74,
  "scope": {
    "level": "task-family",
    "value": "python-import-error"
  },
  "conflicts_with": [],
  "status": "new"
}
```

候选状态第一阶段建议仅支持：

- `new`
- `needs_review`
- `promoted`
- `rejected`
- `expired`

### 7.4 Asset

第一阶段不要求自动生成很多资产类型，优先支持：

- `pattern`
- `anti_pattern`
- `rule`

建议字段：

```json
{
  "asset_id": "pattern_001",
  "asset_type": "pattern",
  "title": "优先修复真实导入关系",
  "content": "Python 导入错误先检查包结构与导入路径，再考虑环境变量兜底。",
  "scope": {
    "level": "task-family",
    "value": "python-import-error"
  },
  "source_episode_ids": [
    "ep_20260412_001"
  ],
  "source_candidate_ids": [
    "cand_20260412_001"
  ],
  "confidence": 0.80,
  "status": "active",
  "last_used_at": null,
  "created_at": "2026-04-12T10:20:00Z",
  "updated_at": "2026-04-12T10:20:00Z"
}
```

### 7.5 Activation View

作用：

- 面向当前任务动态生成最小激活包
- 提供上下文摘要，而不是资产仓库全量展开

建议字段：

```json
{
  "activation_id": "act_20260412_001",
  "task_query": "fix pytest import error",
  "selected_assets": [
    {
      "asset_id": "pattern_001",
      "asset_type": "pattern",
      "title": "优先修复真实导入关系",
      "reason": "任务类型匹配 python-import-error，且最近验证有效"
    }
  ],
  "why_selected": [
    "scope 命中 task-family",
    "asset_type 属于高优先级 pattern",
    "confidence 高于阈值"
  ],
  "rendered_context": [
    "相关经验：遇到 Python 导入错误时，优先检查包结构与导入路径，不优先依赖 PYTHONPATH 规避。"
  ],
  "fallback_episode_refs": [
    "ep_20260412_001"
  ]
}
```


## 8. 存储与目录结构

MVP 建议直接采用“文件 + SQLite”双存储。

### 8.1 文件目录

```text
.agent-memory/
  traces/
    bundles/
    diffs/
  episodes/
  candidates/
  assets/
    patterns/
    anti_patterns/
    rules/
  views/
```

建议：

- `trace bundle` 使用 JSON
- `episode` 可优先使用 JSON，后续再评估是否加入 markdown 可读视图
- `candidate` 与 `asset` 可采用 markdown frontmatter 或 JSON

第一阶段为了降低实现复杂度，推荐：

- 索引对象先统一用 JSON 落盘
- 面向人工阅读的文档后补

### 8.2 SQLite 表

MVP 最小表集合：

- `traces`
- `episodes`
- `candidates`
- `assets`
- `activation_logs`

其中字段不要求一开始完全拆开，可以采用：

- 主键字段
- 状态字段
- 若干检索字段
- `payload_json`

第一阶段先保证可用，再逐步拆表。


## 9. CLI Runtime 设计

MVP 的 runtime 优先做成 CLI，而不是常驻服务。

### 9.1 顶层命令

建议第一版只做这 5 个命令：

```text
expcap review
expcap extract
expcap promote
expcap activate
expcap explain
```

### 9.2 命令定义

#### `expcap review`

输入：

- `trace_bundle.json`

输出：

- `episode.json`

示例：

```text
expcap review --input .agent-memory/traces/bundles/trace_20260412_001.json
```

#### `expcap extract`

输入：

- `episode.json`

输出：

- 一个或多个 `candidate.json`

示例：

```text
expcap extract --episode .agent-memory/episodes/ep_20260412_001.json
```

#### `expcap promote`

输入：

- candidate id 或 candidate 文件

输出：

- promoted asset

示例：

```text
expcap promote --candidate cand_20260412_001
```

MVP 默认建议为半自动：

- 支持规则阈值自动晋升
- 支持人工显式晋升

#### `expcap activate`

输入：

- 当前任务描述
- 当前工作区
- 可选约束

输出：

- `activation_view.json`
- 简短上下文摘要

示例：

```text
expcap activate --task "fix pytest import error" --workspace "$PWD"
```

#### `expcap explain`

输入：

- asset id / candidate id / episode id

输出：

- 来源证据
- 为什么被激活 / 为什么未晋升

示例：

```text
expcap explain --asset pattern_001
```


## 10. 评分与晋升 V1

候选评分先保持简单，不追求一步到位。

### 10.1 评分维度

第一版采用 4 维评分：

- `reusability`
- `stability`
- `confidence`
- `constraint_value`

### 10.2 评分来源

建议采用混合模式：

- 规则特征负责底层证据判断
- LLM 负责语义判断与归纳

规则特征示例：

- 是否有测试通过信号
- 是否只有单次偶然成功
- 是否存在明确失败 / 回滚
- 是否涉及项目局部 workaround

LLM 判断示例：

- 是否可跨任务复用
- 是否更像通用模式而不是局部技巧
- 是否应该归类为 `pattern` 还是 `anti_pattern`

### 10.3 晋升阈值

MVP 先用简单阈值：

- 总分高于阈值才允许自动晋升
- 置信度不足时进入 `needs_review`
- 与现有资产冲突时不自动晋升

建议：

- 自动晋升只对 `pattern` / `anti_pattern` 开放
- `rule` 初期更适合人工确认后再晋升


## 11. 激活策略 V1

激活是 MVP 成败的关键。

### 11.1 激活输入

- 当前任务描述
- 当前工作区路径
- 当前宿主类型
- 用户显式约束

### 11.2 排序策略

第一版按以下顺序排序：

1. scope 匹配度
2. asset_type 权重
3. confidence
4. 最近验证有效性
5. 最近使用时间

### 11.3 注入策略

第一阶段只输出最小上下文包，控制在少量高相关摘要内。

建议约束：

- 默认只输出 3 到 5 条资产摘要
- 每条摘要使用 1 到 3 句
- 必要时附 episode 引用，但不展开全文

### 11.4 反馈回流

MVP 要记录：

- 激活了哪些资产
- 最终任务结果是否成功
- 用户是否接受结果

这部分先写入 `activation_logs`，为后续估值迭代提供基础。


## 12. 任务生命周期

MVP 的默认任务流如下：

### 12.1 任务开始

- adapter 收到任务开始信号
- runtime 调用 `activate`
- 生成 activation view
- skill 或宿主层注入上下文摘要

### 12.2 任务进行中

- 仅收集关键事件
- 不追求实时复杂分析

### 12.3 任务结束

- 组装 trace bundle
- 调用 `review`
- 调用 `extract`
- 写入候选池
- 满足规则时尝试 `promote`

### 12.4 人工治理

MVP 至少保留以下人工动作：

- 查看 episode
- 查看 candidate
- 强制晋升
- 拒绝晋升
- 查看来源解释


## 13. 目录建议

如果开始工程化，推荐目录如下：

```text
project-root/
  docs/
  runtime/
    cli/
    core/
    storage/
    activation/
    policy/
  schemas/
  examples/
  tests/
  skills/
```

模块职责建议：

- `runtime/cli/`：命令入口
- `runtime/core/`：episode / candidate / asset 领域逻辑
- `runtime/storage/`：文件与 SQLite
- `runtime/activation/`：检索、排序、渲染
- `runtime/policy/`：评分、晋升、冲突处理
- `schemas/`：对象 schema
- `examples/`：trace bundle 与 activation 样例
- `skills/`：宿主侧轻 skill


## 14. 第一阶段实现顺序

推荐实现顺序如下：

1. 定义 `trace_bundle` / `episode` / `candidate` / `activation_view` schema
2. 实现文件落盘与 SQLite 索引
3. 实现 `review` 与 `extract`
4. 实现最小 `activate`
5. 实现 `explain`
6. 最后再做 `promote` 自动化增强

这个顺序的核心是：

- 先把数据流打通
- 再优化治理与自动化


## 15. 延后问题

以下问题先明确延后，不阻塞 MVP：

- daemon 化时机
- 跨宿主统一事件协议的完整设计
- 全自动 skill 生成
- 复杂资产关系图
- 细粒度会话搜索
- 跨项目经验迁移策略
- 大规模资产老化与淘汰策略


## 16. MVP 验收标准

如果以下条件成立，就说明第一阶段达到了可接受的 MVP 验收线：

- 能从真实任务生成稳定的 `trace_bundle`
- 能把 `trace_bundle` 提炼成可读的 `episode`
- 能从 `episode` 中提炼出少量高质量 `candidate`
- 任务开始与结束时，系统默认流程可以较稳定地自动触发
- 能在新任务开始前激活有限且相关的高层经验
- 激活结果在相当比例上对当前任务真正有帮助，而不只是“命中了相似文本”
- 激活结果对当前任务具有可解释性
- 全过程可本地落盘、可审查、可回溯

换句话说，MVP 的成功不是“记住了很多”，而是：

> 系统开始能够用少量、可信、相关的历史经验改变新任务的行为质量。
