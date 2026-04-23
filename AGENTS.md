# AGENTS.md

本仓库默认启用 `expcap` 经验资本化工作流。

## 默认目标

让 Codex 在这个项目中把“历史经验激活”和“任务后经验沉淀”当成默认行为，而不是只有在用户显式提醒时才做。

## 核心定位

`expcap` 不和 Codex / Claude Code 的个人记忆竞争。它专注于项目级、团队级、公司级的工程经验资产：可共享、可审阅、可交割，不绑定某一个人的模型账号。

## 默认规则

默认使用集中数据中心，而不是把运行数据写入项目目录：

```bash
export EXPCAP_STORAGE_PROFILE=user-cache
export EXPCAP_HOME="$HOME/.expcap"
```

项目仍然拥有经验资产；只是 trace、episode、candidate、asset、SQLite、Milvus Lite 等运行数据默认存放到 `EXPCAP_HOME/projects/...`。

### 1. 任务开始前默认先做 get

在开始实质性分析、改代码、跑命令之前：

- 先根据用户当前任务描述，调用：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap auto-start --task "<当前任务摘要>" --workspace "$PWD"
```

- 读取命令输出中的 `activation_view` 或 `saved_to` 指向的 view 文件
- 如果存在命中经验，在正式工作前向用户用 1-3 句话简短说明：
  - 激活了什么经验
  - 为什么命中
  - 会怎样影响当前执行策略

### 2. 任务结束后默认尝试做 save

当满足以下任一条件时，默认触发经验沉淀：

- 已完成一轮明确任务
- 已完成重要修复或实现
- 已形成稳定 lesson / pattern / anti-pattern
- 用户明确要求“总结经验 / 沉淀经验 / 保存经验”

默认顺序：

1. 优先执行 `auto-finish` 生成最小闭环
2. 它会自动完成 `trace_bundle -> review -> extract`
3. 如果满足阈值，会自动执行 `promote`
4. 如需更细控制，再单独调用底层子命令

### 3. 作用域默认策略

- 与当前仓库强相关的经验，默认视为 project-owned 经验
- 只有经过多个项目验证的稳定经验，才考虑后续晋升为 team/company shared 经验
- 第一阶段不要把项目局部 workaround 误提升为团队/公司级知识
- 任务开始时优先读取 `project` 资产，再补充 `cross-project` 资产
- 当项目规范、历史决策、目录约定能直接指导开发时，可作为 `context/rule` 资产处理，而不只是 `pattern`

### 4. 什么时候不要自动 save

遇到以下情况先不要沉淀：

- 任务尚未收敛
- 结论明显不稳定
- 只是临时 workaround
- 缺少验证结果
- 用户明确表示这次不要记录

### 5. 用户体验要求

- 不要让用户手工记忆命令
- 能由 Codex 代跑的 `expcap` 命令，默认由 Codex 自己跑
- 对用户主要展示结论、命中的经验、产出的对象，而不是命令细节

## 当前运行时入口

优先使用集中存储模式运行本地包装器：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" scripts/expcap <subcommand> ...
```

或使用安装后的 CLI：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap <subcommand> ...
```

## 当前阶段定位

- 这是一个边用边改的原型项目
- 允许半自动，不要求一开始就完美全自动
- 但默认行为必须向“自动 get / 自动 save”方向靠拢
