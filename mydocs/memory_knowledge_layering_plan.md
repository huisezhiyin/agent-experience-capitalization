# Expcap 记忆与知识分层方案

## 1. 目标

本方案的目标不是让 agent “自动形成越来越多的长期记忆”，而是建立一套高维护性、低破坏性的经验分层系统：

- 少量、稳定、高置信的内容进入项目/系统/个人级规则层
- 大量原始经验保留为可召回的 episodic memory
- 总结产物先进入候选层，而不是直接进入高权重长期层
- 召回优先回到原始经验或轻摘要，而不是多轮抽象后的“记忆结论”

一句话：

> `expcap` 应优先建设经验治理分层，而不是无限 memory consolidation。


## 2. 总体结构

建议把系统分成四层，但其中“知识层”和“记忆层”是主轴，另外两层是支撑层：

```text
稳定规则层
  -> AGENTS.md / CLAUDE.md / PROJECT_PROMPT.md

原始记忆层
  -> trace / episode / diff / tests / errors / activation / feedback

治理账本层
  -> SQLite / Postgres

语义召回层
  -> Milvus / Milvus Lite
```

更准确地说：

- `规则层` 负责长期稳定、默认生效的知识
- `原始记忆层` 负责真实发生过什么
- `治理账本层` 负责状态、关系、生命周期
- `语义召回层` 负责找得到什么可能相关


## 3. 第一主轴：知识如何分层

### 3.1 稳定规则层

这一层放少量、高置信、长期有效、值得每次默认生效的内容。

典型载体：

- `PROJECT_PROMPT.md`
- `AGENTS.md`
- `CLAUDE.md`
- `AGENTS.expcap.md`

适合放：

- 项目级稳定约定
- 个人偏好和协作边界
- 团队约定
- dont_repeat
- 稳定 checklist
- decision memory

不适合放：

- raw trace
- 大量排障细节
- 一次性 workaround
- 尚未证明有效的总结

这层的核心是：

- 少
- 稳
- 短
- 可维护
- 可 review

### 3.2 候选知识层

这一层是 `candidate`。

作用：

- 承接任务后总结
- 隔离未验证经验
- 防止总结直接污染长期层

原则：

- consolidation 只生成 candidate
- candidate 默认不进入高权重长期层
- candidate 只有在证据、作用域、反馈都足够后，才 promote 成 asset

### 3.3 长期经验资产层

这一层是 `asset`。

建议明确晋升梯子：

```text
trace
  -> episode
  -> candidate
  -> project asset
  -> team asset
  -> organization asset
```

解释：

- `project asset` 是默认主形态
- `team asset` 必须经过多个项目验证，且有 owner / evidence / review
- `organization asset` 要求更严格，不应由单次任务自动晋升


## 4. 第二主轴：记忆如何分层

### 4.1 原始记忆层

这一层的核心不是“提炼”，而是“保真”。

应保存：

- raw trace
- task input
- tool calls
- diff
- test result
- error log
- activation view
- user feedback

这层是第一真源。

要求：

- 原始记忆不能被抽象覆盖
- 后续所有 candidate / asset 都应能回链到这里
- 如果高层资产有问题，应该能回到这里复查

### 4.2 Episode 层

`episode` 是记忆和知识之间的中间层。

它不是最终知识，也不是纯原始日志。

它的作用是：

- 把一次任务整理成可复盘案例
- 保留结构化叙事
- 但仍然绑定 raw evidence

所以它更像：

- recoverable case
- structured task memory

而不是：

- final truth


## 5. 四类介质各自负责什么

### 5.1 Markdown

代表：

- `PROJECT_PROMPT.md`
- `AGENTS.md`
- `CLAUDE.md`
- 稳定 docs / memory notes

职责：

- 承载稳定规则
- 提供人类可读性
- 支持 review / PR / 手工维护

不负责：

- 大规模召回
- 原始真源
- 大量自动总结堆积

### 5.2 Milvus

职责：

- 语义召回
- 找到相关 trace / episode / asset / codemap

原则：

- Milvus 负责“找得到”
- Milvus 不负责“信得过”
- Milvus 不是正文真源

所以每个向量对象都应带 source pointer，能回到 JSON / Markdown / object storage 正文。

### 5.3 SQLite

SQLite 不是“知识本体”，也不是“记忆真源”。

它最合理的定位是：

> 治理账本层

职责：

- 记录有哪些对象
- 记录对象之间的关系
- 记录生命周期状态
- 记录激活和反馈历史
- 支撑 dashboard / doctor / queue / review

适合存：

- trace_id / episode_id / candidate_id / asset_id
- source pointer
- review_status
- temperature
- activation_count
- feedback_count
- promotion state
- deprecation state
- scope
- owner
- version

不适合存：

- 大段正文的唯一真源
- 语义向量
- 需要 LLM 理解的长文本知识

### 5.4 本地日志 / JSON

职责：

- 保存原始运行证据
- 作为 recoverable source of truth

这层非常重要，因为它决定系统不会因为总结错误而彻底失真。


## 6. 怎么存

建议的写入路径：

```text
任务执行
  -> 记录 raw trace / logs / diff / tests / errors
  -> 生成 episode
  -> 从 episode 提炼 candidate
  -> candidate 进入治理账本
  -> 少量高置信内容 promote 成 asset
  -> 极少量稳定内容再晋升到 AGENTS / PROJECT_PROMPT
```

关键原则：

- 任务结束后默认只生成 `candidate`
- 不默认把大量内容写进 `AGENTS.md` / `CLAUDE.md`
- 不默认把 LLM 总结覆盖原始材料


## 7. 怎么取

建议按“先召回、再过滤、再下钻”的顺序：

### 7.1 第一步：召回

从 Milvus 找语义相似内容。

候选来源可以包括：

- asset
- episode
- trace summary
- codemap
- curated docs

### 7.2 第二步：过滤

由 SQLite 过滤：

- scope
- review_status
- temperature
- ownership
- project / team / org
- 最近是否已经激活过

### 7.3 第三步：返回少量候选

返回给 agent 的不是“记忆命令”，而是带来源的少量候选。

每条至少应有：

- `source_provenance`
- `match_evidence`
- `risk_flags`
- `llm_use_guidance`

### 7.4 第四步：必要时下钻

默认顺序：

1. 先看稳定规则层
2. 再看 asset
3. 不够再看 episode
4. 还不够再回 raw trace

这样可以降低“高层抽象误导当前任务”的风险。


## 8. 怎么维护

维护的核心不是“继续总结”，而是“继续治理”。

### 8.1 候选治理

- 新总结默认进入 `candidate`
- 默认 `unproven`
- 需要证据、scope、review 后再 promote

### 8.2 资产治理

资产必须有：

- review_status
- temperature
- activation history
- feedback history

帮助信号弱时：

- 降温
- 进入 `watch`
- 进入 `needs_review`
- 必要时 quarantine / deprecate

### 8.3 层级隔离

建议明确四级：

- `personal / local prior`
- `project`
- `team`
- `organization`

默认隔离，不自动污染。

### 8.4 任务类型隔离

不同任务类型不应混成一个 consolidation 池：

- bugfix
- refactor
- test
- docs
- architecture
- performance
- infra

这样能减少错误泛化。


## 9. 我对 SQLite 的最终定位

SQLite 不是没用。

但它的作用不是“存知识”，而是：

> 管知识

更准确地说，它是：

- 目录
- 关系表
- 生命周期状态机
- 反馈账本
- 运营和治理底座

如果没有 SQLite，会很难做：

- candidate review queue
- activation/feedback 统计
- temperature / decay
- promote / reject / deprecate
- provenance 关系查询
- dashboard / doctor

所以建议保留它，但明确降格定位：

- 不是 memory body
- 不是 semantic layer
- 是 governance ledger


## 10. 推荐的最终表达

我建议把 `expcap` 的主结构固定成这句话：

> `AGENTS/CLAUDE 稳定规则层 + Milvus 原始记忆召回层 + SQLite 治理账本层 + JSON/log 原始证据真源层`

其中：

- `AGENTS/CLAUDE/PROJECT_PROMPT`：少量稳定知识
- `Milvus`：召回原始经验和正文
- `SQLite`：管理状态、关系、生命周期
- `JSON/logs`：保存真实发生过什么


## 11. 后续代码演进建议

如果未来进入实现，建议优先顺序是：

1. 先补 SQLite 治理字段
   - owner
   - version
   - validity_window
   - quarantine_status
   - promotion_history
   - deprecation_history

2. 再补更细 scope tagging
   - module
   - language
   - framework
   - task_type
   - applicable_conditions
   - known_counterexamples

3. 再补 conflict detection / replay validation

不要反过来先做更强 consolidation。


## 12. 一句话结论

`expcap` 最合理的方向，不是“让 agent 越记越多”，而是“让稳定规则尽量少、让原始记忆可召回、让治理状态可维护”。
