# 面向 Codex / Claude Code 的经验资本化增强层

## 1. 文档定位

本文档不是最终实现 spec，而是一份偏技术架构设计的研究草案。

目标不是复刻 Hermes Agent 全部工程能力，而是抽取其中最有价值的经验治理理念，设计一层可挂载在 `Codex` 与 `Claude Code` 之上的本地优先增强层，用于实现更稳健的“自学习 / 自强化”闭环。

本文档重点回答以下问题：

- 这个系统的技术边界是什么
- 为什么适合做成 `skill + plugin` 协同架构
- 系统应当分成哪些模块
- 数据应当如何分层
- 经验如何从日志和任务过程进入可复用资产
- 新任务开始时，这些资产如何被激活


## 2. 问题定义

对于 `Codex` 与 `Claude Code` 这类 coding agent，当前的短板通常不在“单次任务执行能力”，而在“任务经验如何转化为未来任务中的结构性优势”。

具体来说，现有系统通常缺少以下能力：

- 对任务过程中的关键证据做稳定留痕
- 从原始痕迹中提炼结构化 episode
- 区分哪些经验只是局部偶然，哪些值得长期保留
- 将经验按不同层级晋升为不同类型的资产
- 在未来任务中按需激活，而不是无差别灌入上下文

因此，本项目研究对象不是泛泛的“自学习 agent”，而是：

> 一个面向 coding agent 的经验资本化增强层。

它不替代宿主 agent，而是增强宿主 agent 的长期经验治理能力。


## 3. 设计目标与非目标

### 3.1 设计目标

- 面向 `Codex` 与 `Claude Code` 两类宿主优先设计
- 采用 `local-first` 架构，本地文件与本地数据库为主
- 通过 `plugin` 提供证据采集、调度与存储能力
- 通过 `skill` 提供经验提炼、估值与晋升能力
- 保持宿主中立，尽量减少对单一厂商接口的强依赖
- 优先支持 coding workflow，而不是泛用聊天记忆
- 优先构建“经验闭环”，而不是构建“大而全 agent 平台”

### 3.2 非目标

- 不复刻 Hermes 的 CLI、gateway、cron、browser、terminal tool 全套体系
- 不在第一阶段构建全自动自治 agent
- 不在第一阶段追求完全通用的宿主兼容层
- 不在第一阶段引入复杂的在线服务端依赖
- 不默认采集全部对话和全部过程细节


## 4. 设计原则

### 4.1 先留证据，再谈知识

任何高层经验资产都必须可以追溯到足够的原始证据。没有证据链支撑的高层知识容易污染系统。

### 4.2 先候选，再晋升

任务后总结不应直接进入高权重长期层。经验应先进入候选层，经过估值与筛选后再晋升。

### 4.3 默认调高层，必要时下钻

新任务开始时，优先激活 `rule / skill / pattern` 等高层资产；只有在必要时才下钻到 `episode` 或更低层的原始痕迹。

### 4.4 以外部可验证信号为主

系统应优先依赖 `diff`、测试结果、错误日志、用户反馈等外部可验证证据，而不是过度依赖 agent 自述。

### 4.5 核心通用，适配器局部化

经验资本化内核尽量通用，但宿主接入、日志解析、激活方式等宿主相关部分必须允许局部化实现。


## 5. 总体架构

整体建议采用四层结构：

1. 宿主接入层
2. 经验资本化内核
3. 资产存储层
4. 激活与交互层

可用如下逻辑表示：

```text
Codex / Claude Code
        |
        v
  Host Adapter Layer
        |
        v
  Trace Collection + Event Orchestration
        |
        v
  Episode Distillation + Candidate Promotion
        |
        v
  Asset Store (files + sqlite)
        |
        v
  Activation Engine
        |
        v
Skill / Prompt Injection / Context Hints
```

其中：

- `plugin` 主要承担宿主接入、留痕、编排、存储、召回
- `skill` 主要承担总结、提炼、估值、晋升、解释

从系统实现重心看，后续更推荐演化为：

- 轻 `skill`
- 重 `local runtime`

也就是：

- `skill` 作为 agent-facing API
- `local runtime` 作为本地经验后端
- 文件与数据库作为长期存储与治理基础


## 6. 为什么是 Skill + Plugin 协同

单独做 `skill` 不够，因为它缺乏稳定的证据采集与任务生命周期控制能力。

单独做 `plugin` 也不够，因为它只能管理数据和流程，无法高质量完成经验语义提炼与资产判断。

因此，合理的技术划分应是：

### 6.1 Plugin 的职责

- 采集本地日志、diff、测试结果、错误信息
- 感知任务开始、进行中、结束等生命周期事件
- 将原始证据整理为统一 trace 结构
- 持久化存储到本地文件与数据库
- 在适当时机触发 skill 进行总结或召回
- 将激活结果以适合宿主的方式注入当前任务上下文

### 6.2 Skill 的职责

- 基于 trace 与上下文材料提炼任务 episode
- 从 episode 中识别 lesson、pattern、anti-pattern
- 判断经验是否值得进入候选池或晋升长期层
- 输出结构化资产内容
- 在激活阶段对候选资产做简化解释或重写

### 6.3 协同收益

- plugin 让系统具备“看见发生了什么”的能力
- skill 让系统具备“理解什么值得保留”的能力
- 两者结合才能形成闭环的经验资本化层

### 6.4 推荐的技术重心

从长期演化上看，更推荐以下重心分布：

- skill 轻量化
- 本地 runtime 重量化

原因是：

- skill 更适合做宿主接口，而不是长期状态中心
- 候选池、评分、冲突处理、索引与检索更适合由本地程序承担
- runtime 更容易支持多宿主复用
- 复杂治理逻辑如果被压进 skill 文本协议，后期会很脆弱

因此，后续设计可以理解成：

> `skill` 是“接口”，`runtime` 是“后端”。


## 7. 宿主接入层设计

系统初期只面向两类宿主：

- `Codex`
- `Claude Code`

这里不要求两者底层机制完全一致，而要求形成一致的抽象接口。

### 7.1 Host Adapter 抽象

建议定义统一适配器接口：

```text
HostAdapter
  - on_task_start()
  - on_task_update()
  - on_task_end()
  - collect_trace()
  - inject_context()
  - get_workspace_identity()
  - get_session_identity()
```

### 7.2 宿主差异的处理原则

- 宿主原生日志能力不同：允许适配器从不同来源采集
- 宿主上下文注入机制不同：允许适配器输出不同注入形式
- 宿主任务粒度不同：允许适配器定义自己的 task boundary

### 7.3 初期接入策略

第一阶段不追求深入 IDE / 平台内部 API，可以优先采用：

- 本地日志读取
- 工作区文件观察
- git diff 读取
- 测试命令输出读取
- 本地任务标记文件

也就是说，初期的“插件”更像一个 `local companion layer`，而不必一开始就是重型官方扩展。

### 7.4 宿主接入输出形式

宿主接入层建议支持以下输出形态：

- 直接注入 agent 的简短文本
- 供 skill 使用的结构化 JSON
- 供用户查看的 markdown 摘要
- 供 runtime 内部继续处理的标准事件对象

这样可以降低不同宿主之间的耦合成本。


## 7A. 运行形态设计

从当前阶段看，建议优先实现为本地命令行 runtime，而不是一开始就做常驻 daemon。

### 7A.1 推荐演化路径

#### 阶段 1：CLI Runtime

通过统一命令入口运行，例如：

```text
expcap review
expcap activate
expcap promote
expcap explain
```

优点：

- 易调试
- 易接入不同 agent
- 无需先解决进程管理与服务生命周期
- 很适合 PoC 与研究验证

#### 阶段 2：Stateful Local Runtime

增加本地数据库、缓存、索引与策略模块，但仍以 CLI 作为主要调用面。

#### 阶段 3：Optional Local Service

当调用频率、状态共享和延迟要求更高时，再考虑演化为本地 daemon / service。

### 7A.2 为什么不建议一开始就服务化

- 当前真正不确定的是对象模型与晋升秩序，不是服务治理
- daemon 会提前引入进程管理、健康检查、重启恢复等复杂度
- 对 `Codex` 与 `Claude Code` 而言，CLI 调用已经足以支撑第一阶段验证


## 7B. Agent API 设计

如果 skill 是 agent-facing API，那么 runtime 需要一套稳定的能力面。

### 7B.1 建议的顶层能力

- `review`
  - 对任务进行复盘，生成 episode

- `extract`
  - 从 episode 中提取 candidate

- `promote`
  - 将 candidate 晋升为长期 asset

- `activate`
  - 为当前任务检索并生成最小激活包

- `explain`
  - 解释某条经验为什么被激活、晋升或拒绝

- `govern`
  - 降权、失效、标注边界、合并冲突资产

### 7B.2 CLI 形态的接口示例

```text
expcap review --input trace_bundle.json
expcap extract --episode episode_20260412_01.json
expcap promote --candidate cand_001
expcap activate --task "fix pytest import error"
expcap explain --asset pattern_021
```

### 7B.3 Skill 的职责

skill 不负责保存复杂状态，而是负责：

- 发现何时应调用 runtime
- 组织调用参数
- 消费返回结果
- 用 agent 友好的方式继续行动


## 8. 信号采集架构

### 8.1 必须采集的信号

以下信号应构成最小闭环：

- 任务输入
  - 用户请求
  - 用户补充约束
  - 验收标准

- 关键操作轨迹
  - 关键文件读取
  - 关键文件修改
  - 关键命令执行
  - 关键错误出现

- 最终代码变更
  - git diff
  - 涉及文件列表

- 验证结果
  - 测试命令
  - 通过 / 失败
  - 失败摘要

- 结果状态
  - 成功
  - 部分完成
  - 失败
  - 放弃

### 8.2 推荐采集的增强信号

- 用户纠正与反馈
- 尝试过但放弃的路径
- 重复失败序列
- 工作区环境信息
- 任务耗时与试错成本

### 8.3 不建议默认采集的高噪声信号

- 全量逐字对话长期索引
- 所有 shell 输出全文
- 高频细粒度编辑行为
- 所有中间推理内容

这些可以保留在原始 trace 层，但不应默认进入高层经验资产层。

### 8.4 Trace Bundle 标准化建议

建议在采集层与提炼层之间引入统一 `trace bundle` 对象。

建议字段至少包括：

- `trace_id`
- `host`
- `workspace`
- `session_id`
- `task_hint`
- `user_request`
- `constraints`
- `events`
- `files_changed`
- `verification`
- `result`
- `artifacts`
- `timestamps`

这样可以让不同宿主的异构输入先被规整成统一对象，再进入 episode 提炼层。


## 8A. 任务边界设计

任务边界是整个系统最基础、也最需要审慎建模的部分之一。

### 8A.1 为什么任务边界重要

任务边界会直接影响：

- episode 的完整性
- candidate 的粒度
- 激活相关性
- 来源追溯能力

### 8A.2 当前建议

初期建议优先采用“工作目标边界”，而不是简单“消息边界”。

也就是：

- 同一目标下的连续多轮工作尽量归入同一 task
- 目标明显切换时再切新 task
- 长任务内部允许记录多个 checkpoint

### 8A.3 PoC 阶段可接受的近似规则

- 用户显式提出新目标
- 工作区主题明显切换
- 任务通过验证并完成
- 用户明确接受结果


## 9. 数据分层模型

建议采用五层模型。

### 9.1 Layer 0: Raw Trace

作用：保留原始证据，用于追溯与二次提炼。

典型内容：

- 宿主日志片段
- 关键命令输出
- 错误日志
- diff
- 测试结果

### 9.2 Layer 1: Episode

作用：将一次任务整理为结构化案例。

典型字段：

- `task_id`
- `goal`
- `constraints`
- `workspace`
- `files_touched`
- `commands`
- `turning_points`
- `result`
- `verification`
- `user_feedback`
- `lesson`

建议进一步增加：

- `attempted_paths`
- `abandoned_paths`
- `decision_rationale`
- `scope_hint`
- `confidence_hint`

### 9.3 Layer 2: Candidate Asset

作用：承接从 episode 中提炼出的候选知识，但尚未晋升为长期高权重资产。

典型字段：

- `candidate_id`
- `source_episode_ids`
- `candidate_type`
- `content`
- `reusability_score`
- `stability_score`
- `confidence_score`
- `constraint_value_score`
- `scope`
- `conflicts_with`
- `status`

这里的 `status` 可包含：

- `new`
- `needs_review`
- `promoted`
- `rejected`
- `expired`

### 9.3A Candidate 层的意义

`candidate` 不是“还没写好的 asset”，而是制度化缓冲层。

它的作用在于：

- 防止一次总结直接污染长期层
- 支持多个 episode 为同一候选累积证据
- 允许经验先被记录，再被审查，再被晋升

没有 candidate 层，系统很容易退化成“自动写总结仓库”。

### 9.4 Layer 3: Promoted Asset

作用：承接已经通过筛选的长期资产。

资产类型可包括：

- `fact`
- `pattern`
- `anti_pattern`
- `rule`
- `skill`

建议每类 asset 至少包含：

- `asset_id`
- `asset_type`
- `content`
- `scope`
- `source_episode_ids`
- `source_candidate_ids`
- `confidence`
- `last_used_at`
- `created_at`
- `updated_at`
- `status`

### 9.5 Layer 4: Activation View

作用：面向当前任务的运行时视图，不是长期存储层。

它是根据当前任务动态拼出的“最小激活包”，通常只包含少量高相关资产摘要。

建议其输出对象至少包含：

- `activation_id`
- `task_query`
- `selected_assets`
- `why_selected`
- `rendered_context`
- `fallback_episode_refs`


## 10. 存储层设计

建议采用“文件 + SQLite”双存储策略。

### 10.1 文件存储

适合存放：

- 可读性强的 markdown 资产
- skill 文件
- episode 文档
- 人工审核记录

建议目录示例：

```text
.agent-memory/
  traces/
  episodes/
  candidates/
  assets/
    facts/
    patterns/
    anti_patterns/
    rules/
    skills/
  views/
```

建议其中：

- `traces/` 保存原始 trace bundle
- `episodes/` 保存任务案例
- `candidates/` 保存候选经验对象与审核记录
- `assets/` 保存晋升后的长期资产
- `views/` 保存激活阶段生成的临时结果

### 10.2 SQLite 存储

适合存放：

- 索引
- 元数据
- 检索字段
- 评分字段
- 激活历史
- 资产关系图

建议核心表：

- `tasks`
- `traces`
- `episodes`
- `candidates`
- `assets`
- `asset_links`
- `activation_logs`
- `host_sessions`

如需进一步细化，建议补充：

- `candidate_evidence`
- `asset_conflicts`
- `task_boundaries`
- `runtime_events`

### 10.3 文件与数据库的关系

- 文件负责可读、可编辑、可审查
- SQLite 负责检索、关联、评分、调度

这样既保留了本地透明性，又避免所有查询都依赖 markdown 扫描。

### 10.4 存储一致性建议

建议采用“数据库为索引真源、文件为内容真源”的弱双写模型：

- SQLite 负责检索、状态、评分、关系
- 文件负责正文、审查、人工编辑

这样更适合 local-first 场景，也更利于和 agent 直接协作。


## 11. 任务后数据流

任务结束后，推荐按如下管线处理：

### 11.1 Trace Collection

plugin 收集本次任务关键证据，生成统一 trace bundle。

### 11.2 Episode Distillation

skill 基于 trace bundle 生成一张 episode 卡片。

### 11.3 Candidate Extraction

从 episode 中提炼潜在可复用经验，生成一个或多个 candidate。

### 11.4 Candidate Evaluation

对 candidate 打分并写入候选池。

### 11.5 Promotion

满足阈值的 candidate 晋升为高层资产。

### 11.6 Persistence

文件与数据库同步落盘。

整个过程可表示为：

```text
task end
  -> collect trace
  -> distill episode
  -> extract candidates
  -> evaluate candidates
  -> promote selected candidates
  -> persist all layers
```

### 11.1A 失败任务的处理策略

失败任务同样是高价值经验来源。

对于 coding agent 而言，失败样本更适合提炼：

- `anti-pattern`
- `constraint`
- `failure signature`
- `fallback rule`

因此推荐：

- 成功任务优先提炼 `pattern / rule`
- 失败任务优先提炼 `anti-pattern / warning`


## 12. 激活架构设计

激活是系统最关键的一环，因为它决定经验是否真正改变未来执行。

### 12.1 激活输入

新任务开始时，系统应读取以下上下文：

- 当前任务描述
- 当前工作区特征
- 当前宿主会话信息
- 当前用户显式约束

### 12.2 激活顺序

推荐采用高层优先策略：

1. `rule / skill`
2. `pattern / anti_pattern / fact`
3. `episode`
4. `raw trace`

### 12.3 激活输出

激活输出应是精简后的运行时视图，而不是原始资产全文堆叠。

可包含：

- 当前任务最相关规则
- 推荐路径
- 已知陷阱
- 项目局部偏好
- 必要时的来源引用

### 12.4 激活方式

对于 `Codex` 与 `Claude Code`，推荐保留多种注入方式：

- skill 文本预加载
- 用户消息前置提示
- 任务启动时上下文摘要
- 独立的“相关经验摘要文件”

初期不必只押注单一注入形式。

### 12.5 激活排序策略

建议初期采用如下排序逻辑：

1. 作用域匹配度
2. 资产类型权重
3. 置信度
4. 最近命中情况
5. 最近验证效果

这样可以降低老旧通用资产长期压制局部高相关经验的风险。

### 12.6 激活后的反馈回流

建议记录：

- 本次激活了哪些资产
- agent 是否实际采用了这些建议
- 最终结果是正向还是负向

这些信息应反向进入 `activation_logs` 与后续估值中。


## 13. Skill 系统设计

在本架构中，skill 不是唯一资产类型，而是最高权重的一类程序性资产。

### 13.1 Skill 的适用范围

skill 适合表达：

- 固定任务流程
- 多步排错流程
- 仓库局部工作约定
- 高置信度的最佳路径

### 13.2 Skill 的来源

skill 不应由一次任务总结直接生成。

更合理的路径是：

```text
raw trace -> episode -> candidate -> promoted skill
```

### 13.3 Skill 的治理要求

- 必须可追溯来源 episode
- 必须有适用边界
- 必须可回滚
- 必须支持修订
- 必须与普通 fact / pattern 区分权重

### 13.4 Skill 的接口化原则

在本项目中，skill 更适合作为 agent-facing API，而不是业务后端。

因此建议：

- skill 定义“怎么调用后端”
- runtime 定义“怎么完成业务”

这样能避免复杂状态和长期治理逻辑被塞进 prompt 协议中。


## 14. 插件编排层设计

plugin 不只是采集器，还应是编排器。

### 14.1 事件模型

建议支持如下事件：

- `task_started`
- `task_updated`
- `task_checkpoint`
- `task_failed`
- `task_completed`
- `activation_requested`
- `review_requested`

### 14.2 编排策略

推荐的默认策略：

- 任务开始时尝试激活相关高层资产
- 任务完成后自动生成 episode
- 任务失败后优先提炼 anti-pattern 候选
- 用户显式请求时触发详细复盘

### 14.3 人工干预接口

即使以自动化为目标，也应保留人工接口：

- 强制复盘
- 强制晋升
- 拒绝晋升
- 标记过时
- 查看来源证据

否则系统会缺少可治理性。

### 14.4 Runtime 的解耦目标

编排层应尽量满足：

- 对宿主 API 低耦合
- 对日志来源高容忍
- 对输入材料支持降级运行

即使未来某个宿主没有稳定 API，只要还能拿到：

- 用户请求
- diff
- 测试结果
- 结果状态

系统就应能运行最小闭环。


## 15. 评分与晋升架构

这是整个系统最关键、也是最需要继续研究的部分。

### 15.1 经验势能

建议用“经验势能”作为 candidate 估值框架。

候选评分可由以下维度组成：

- `reusability`
  - 是否可能跨任务复用

- `stability`
  - 是否具有长期有效性

- `confidence`
  - 是否有足够证据支持

- `constraint_value`
  - 是否能显著减少错误、缩短路径或提升收敛

### 15.2 晋升原则

- 一次成功不自动成为 skill
- 局部 workaround 不自动成为 pattern
- 未验证经验不进入高权重层
- 高层资产必须能够解释其来源

### 15.3 冲突处理

当 candidate 与已有资产冲突时，应支持：

- 降权
- 进入人工审核
- 标记为局部例外
- 直接拒绝晋升

### 15.4 建议支持的治理动作

- `promote`
- `downgrade`
- `expire`
- `merge`
- `split`
- `reject`
- `annotate_scope`
- `mark_conflict`


## 16. 安全与污染控制

经验资本化系统最大的风险不是“学不会”，而是“学坏”。

因此需要内建污染控制。

### 16.1 风险来源

- 局部 workaround 被误学为通用规则
- 某项目偏好污染跨项目层
- 失败经验被误判为成功路径
- 过时经验长期残留
- 低质量日志被高权重注入

### 16.2 控制机制

- 候选层隔离
- 资产类型分层
- 适用范围标注
- 来源追溯
- 置信度门槛
- 过期与回滚机制

### 16.3 作用域隔离建议

建议至少支持三类作用域：

- `global`
- `workspace`
- `task-family`

其中：

- `global` 只承载高稳定、高置信资产
- `workspace` 承载仓库局部经验
- `task-family` 承载某类问题域的专项经验


## 17. 可观测性设计

这个系统需要能回答“它为什么在这里激活了这些经验”。

建议保留以下可观测数据：

- 本次任务激活了哪些资产
- 每个资产的匹配原因
- 每个资产来自哪些 episode
- 哪些 candidate 被拒绝，以及为什么
- 哪些资产长期未命中

没有可解释性，这个系统很难长期被信任。

### 17.1 面向用户的解释输出

系统应尽量能解释：

- 为什么这次注入了这些规则
- 某条 skill 来自哪些历史任务
- 为什么某条 candidate 没有晋升
- 为什么某条资产已经降权或过期


## 18. MVP 建议

虽然本文档聚焦架构设计，但从技术风险控制角度看，MVP 应尽量窄。

建议最小可行闭环仅包含：

1. 任务结束后收集 trace
2. 自动生成 episode
3. episode 提炼出 candidate
4. 新任务开始前仅激活高分 `rule / pattern`

初期不必：

- 自动生成大量 skill
- 做全量长期对话索引
- 过度依赖宿主内部 API
- 追求多宿主完全统一实现

### 18.1 MVP 的推荐技术形态

MVP 更推荐：

- 轻 skill
- CLI runtime
- 本地 SQLite
- markdown / JSON 资产文件

而不是：

- 重型插件
- 常驻服务
- 复杂多宿主 SDK
- 过早自动化全部晋升流程


## 19. 与 Hermes 的关系

该架构不是 Hermes 的翻版，而是对 Hermes 理念的抽取与重组。

Hermes 提供了重要启发：

- 留痕：会话和检索
- memory：持久化长期信息
- skill：程序性经验
- session search：跨会话召回
- background review：任务后复盘

但本架构在以下方面比 Hermes 的现有机制更进一步：

- 显式引入 candidate 层
- 显式引入估值与晋升秩序
- 显式区分高层资产与低层证据
- 显式围绕 `Codex / Claude Code` 设计宿主接入层

此外，两者定位也不同：

- Hermes 更像完整 agent 平台
- 本项目更像可挂载在现有 agent 上的本地经验后端


## 20. 当前开放问题

以下问题仍需继续研究，暂不在本文件中定死：

- `Codex` 与 `Claude Code` 的任务边界应如何定义
- trace bundle 的最小字段集合应如何标准化
- candidate 的评分是否应完全由 LLM 给出，还是引入规则与统计特征
- asset 的文件格式是否统一为 markdown frontmatter，还是部分使用 JSON
- skill 激活应更多依赖宿主技能机制，还是依赖外部上下文注入
- 跨项目经验与项目局部经验应如何隔离
- CLI runtime 演化为 daemon 的时机如何判断
- runtime 返回结果是否应定义统一 JSON schema
- 人工治理与自动治理之间的权责边界如何划分


## 20A. 推荐的新项目形态

如果后续单独立项，推荐项目定位为：

> 一个以本地 runtime 为后端、以轻 skill 为 agent 接口的经验资本化系统。

建议目录形态如下：

```text
project-root/
  docs/
  skills/
  runtime/
    cli/
    core/
    collectors/
    storage/
    policy/
    activation/
  schemas/
  examples/
  tests/
```

其中：

- `skills/` 放宿主侧轻 skill
- `runtime/core/` 放 episode、candidate、asset 领域逻辑
- `runtime/collectors/` 放日志、diff、测试结果采集器
- `runtime/storage/` 放 sqlite 与文件层
- `runtime/policy/` 放评分、晋升、冲突治理
- `runtime/activation/` 放激活与上下文渲染


## 21. 当前结论

从技术架构上看，一个面向 `Codex / Claude Code` 的经验资本化增强层是成立的，并且最合理的形态不是“新 agent”，而是：

> 一个 `local-first` 的 `skill + plugin` 协同系统。

其中：

- plugin 负责证据采集、任务编排、存储检索、运行时激活
- skill 负责经验提炼、候选估值、资产晋升、解释重写

整个系统的关键不在于“能否自动写总结”，而在于：

> 能否建立从原始任务过程到高层经验资产之间的制度化晋升秩序。

这将决定它最终是一个真正的自强化层，还是一个会不断堆积噪声的日志系统。

从工程重心看，当前更推荐的路线是：

> 轻 skill + 本地 runtime

而不是：

> 重 skill + 薄弱状态层

因为前者更利于：

- 多宿主接入
- 状态治理
- 候选池设计
- 长期演化
