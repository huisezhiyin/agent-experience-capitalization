# Agent Experience Capitalization

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-pre--1.0-orange.svg)](GOVERNANCE.md)
[![Codex Skill](https://img.shields.io/badge/Codex%20Skill-ready-brightgreen.svg)](skills/expcap/SKILL.md)

面向 coding agent 的项目级记忆层。

`expcap` 把有效的 agent 工作沉淀成可复用的工程资产。这些资产属于项目和团队，而不是某个用户、某台机器或某个模型账号。

语言：[English](README.md) | [简体中文](README.zh-CN.md)

## 用 Codex 一键启用

现成 Codex skill：[`skills/expcap/SKILL.md`](skills/expcap/SKILL.md)。

一条命令完成本地接入：

```bash
git clone <repo-url>
cd agent-experience-capitalization
scripts/codex-skill-quickstart
```

它会把 skill 安装到 `~/.codex/skills/expcap`，安装带 Milvus Lite 的 runtime，接入当前项目，并运行 `doctor` 让你立刻确认是否可用。

默认会把接入的项目标记为 `active`，也就是 agent 工作流会自动执行
`expcap auto-start` 的默认口径会把它当作活跃项目统计。如果项目已经休眠、归档或只是偶尔查看，可以用
`EXPCAP_PROJECT_STATUS=inactive` 接入，这样仍保留 skill 和存储契约，但会在报表和覆盖率分析里单独归类。

## 为什么

个人记忆解决的是“一个 agent 记住一个用户”。团队需要的是另一种东西：能跟随代码库流转的工程经验。

`expcap` 是 **TEAM memory**：**Transferable Engineering Asset Memory**。

它关注：

- 项目级规则、模式、上下文和检查清单；
- 可审阅、可共享、可交割的团队资产；
- 任务开始前自动获取经验，任务结束后自动沉淀经验；
- 本地优先运行，同时保留云端共享后端的演进路径。

## 能做什么

- 在任务开始时激活相关项目经验。
- 返回带来源的候选经验，让 LLM 自行判断是否适用。
- 将完成的工作转成 `trace -> episode -> candidate -> asset`。
- 记录被激活的经验是否真的帮到了任务。
- 维护 candidate review queue 和 asset 健康状态。
- Milvus 是核心语义召回层；SQLite 只是轻量状态索引和降级层。
- 提供共享对象存储、云端状态索引和托管向量检索的后端契约。

## 安装

Codex 用户优先使用上面的一键接入。手动安装：

```bash
git clone <repo-url>
cd agent-experience-capitalization

python3 -m venv .venv
. .venv/bin/activate
.venv/bin/pip install -e ".[milvus]"
scripts/install-codex-skill
scripts/expcap --help
```

上面的命令默认安装 Milvus Lite，因为 Milvus 是核心语义召回层：

```bash
scripts/expcap sync-milvus --workspace "$PWD" --include-shared
scripts/expcap benchmark-milvus --workspace "$PWD" --sample-size 5 --limit 3
```

## 快速开始

推荐入口是 Codex skill：`skills/expcap/SKILL.md`。先安装 skill，然后让
agent 通过 skill 执行经验激活和沉淀，而不是让每个用户记住底层 CLI。

短周期测试推荐使用集中本地存储：

```bash
export EXPCAP_STORAGE_PROFILE=user-cache
export EXPCAP_HOME="$HOME/.expcap"
```

默认 embedding provider 是零配置 `hash`。如果要测试真实 OpenAI embedding，同时保持当前 Milvus Lite collection 兼容，先使用 128 维：

```bash
export EXPCAP_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY="..."
export EXPCAP_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
export EXPCAP_OPENAI_EMBEDDING_DIM=128
```

如果没有 API key，expcap 会自动回落到 `hash`，并在 `status` / `doctor` 中暴露 fallback 状态。

Milvus Lite index 会按 embedding profile 隔离，例如 `hash-token-sha256-signhash-128` 或
`openai-text-embedding-3-small-128`，不同 provider 和不同维度不会共用同一个本地 DB 文件。

任务开始前激活相关经验：

```bash
expcap auto-start --task "fix pytest import error" --workspace "$PWD"
```

任务结束后沉淀可复用经验：

```bash
expcap auto-finish \
  --task "fix pytest import error" \
  --workspace "$PWD" \
  --command "python3 -m unittest discover -s tests -v" \
  --verification-status passed \
  --verification-summary "tests passed" \
  --result-status success \
  --result-summary "fixed import path"
```

检查闭环：

```bash
expcap status --workspace "$PWD"
expcap doctor --workspace "$PWD"
expcap dashboard --workspace "$PWD"
```

在 macOS 上生成并直接打开本地后台：

```bash
DASHBOARD_PATH="$(
  EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" \
    expcap dashboard --workspace "$PWD" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["saved_to"])'
)"
open "$DASHBOARD_PATH"
```

Linux 可以把最后一行换成 `xdg-open "$DASHBOARD_PATH"`。这个后台只是一个本地静态
HTML 文件，不需要启动常驻 web server。

推荐 `user-cache` profile 会把运行数据写入 `$EXPCAP_HOME`。如果显式使用项目本地模式，`.agent-memory/`
仍应排除在源码提交之外。

## Agent 工作流

把 `expcap` 接入另一个项目：

```bash
scripts/expcap install-project --workspace /path/to/project
```

同时写入 `CLAUDE.md`：

```bash
scripts/expcap install-project --workspace /path/to/project --include-claude
```

安装 Claude hooks（Phase 1 可运行集成）：

```bash
scripts/expcap install-project --workspace /path/to/project --integration-mode claude-hooks
```

优先安装 Codex hooks：

```bash
scripts/expcap install-project --workspace /path/to/project --integration-mode codex-hooks
```

安装器会非破坏式追加说明，创建 `AGENTS.expcap.md`，并确保 `.agent-memory/`
写入 `.gitignore`。`codex-hooks` 模式会生成 `.codex/hooks.json`、
`.codex/hooks/expcap_user_prompt_submit.sh` 和 `.codex/hooks/expcap_stop.sh`。
这些 wrapper 会把 Codex 的 `UserPromptSubmit` / `Stop` 事件路由到
`scripts/expcap-hook`，并默认使用 `user-cache` 存储。如果当前 Codex 版本只读取
用户级 `~/.codex/hooks.json`，把项目生成的 `.codex/hooks.json` entries 合并进去即可。
`claude-hooks` 模式还会生成 `.claude/settings.json`、
`.claude/hooks/expcap_user_prompt_submit.sh` 和 `.claude/hooks/expcap_stop.sh`，
统一通过 `scripts/expcap-hook` 路由到 `auto-start` / `auto-finish`。之后 agent
可以使用 skill-backed 默认工作流：

```bash
expcap auto-start --task "your task" --workspace "$PWD"
expcap progressive-recall \
  --task "your task" \
  --workspace "$PWD" \
  --message "new error, file scope, or phase change"
expcap feedback \
  --workspace "$PWD" \
  --activation-id "<activation id>" \
  --help-signal supported_strong
expcap auto-finish --task "your task" --workspace "$PWD" --verification-status passed --result-status success
```

只有当对话发生明显变化时才使用 `progressive-recall`：出现新错误、新文件/模块、
topic drift，或从讨论进入实现、测试、修复等阶段变化。它会应用冷却时间，并且只返回
最近没有激活过的 delta assets。

当你已经验证 activation 是否真的帮到了任务时，再使用 `feedback`。它会把帮助信号写回
activation，并同步刷新关联资产的温度和 review status。

如果需要手动调试，底层流程仍然可用：`ingest -> review -> extract -> promote -> activate`。

如果要把项目文档作为忠实的 codemap/context 资产导入召回层：

```bash
expcap ingest-docs --workspace "$PWD"
```

默认扫描 `README*`、`AGENTS.md`、`CLAUDE.md` 和 `docs/*.md`，按 chunk 写成
`knowledge_kind=codemap` 资产。它保留原始文本，不把文档强行改写成精炼“真理”。

如果要主动保存一条稀少但高价值的顶层先验：

```bash
expcap save-prior \
  --workspace "$PWD" \
  --knowledge-kind dont_repeat \
  --title "不要重复解释项目记忆定位" \
  --content "expcap 是用来保存用户/团队/项目局部先验、减少重复说明的本地先验层，不是真理型知识库。"
```

这个入口适合明确、稳定的偏好、约束、历史决策，或“不要让我反复说”的指令。高优先级先验会直接保存为
active project asset，后续可由注入策略路由到 `system_prompt`。Activation 时，这类显式高优先级先验也会进入一个很小的常驻候选池，避免仅因为 Milvus top-K 更偏向旧语义命中而丢失。

活跃项目控制：

```bash
scripts/expcap install-project --workspace /path/to/project --project-status active
scripts/expcap install-project --workspace /path/to/project --project-status inactive
```

两种状态下，只要真的开了新 chat，仍然都会执行 `auto-start`。`active / inactive` 更主要是统计标签，让日报和覆盖率分析聚焦真正活跃的项目，而不是所有已接入仓库。

## 核心概念

- `trace`：原始任务证据。
- `episode`：经过 review 的任务叙事。
- `candidate`：从 episode 中提取出的可复用经验。
- `asset`：晋升后的项目或团队记忆资产。
- `activation`：未来任务中被选中的资产。
- `feedback`：activation 是否真的帮到了任务。

Activation view 会包含 `source_provenance`、`match_evidence`、`risk_flags`
和 `llm_use_guidance`。召回层负责提供带来源的候选，coding agent 仍需结合当前任务判断是否采用。

Activation view 也会包含 `injection_plan`，把“召回到了什么”和“应该怎么注入”分开。
注入优先按三层理解：

- `task_start_runtime_injection`：任务开始运行时注入，用于 `SessionStart`、`UserPromptSubmit`、`auto-start` 的任务输入增强。
- `system_prompt_injection`：系统提示词注入，把稳定、很短的项目级先验沉淀到 `AGENTS.md` / `AGENTS.expcap.md`。
- `continuous_runtime_recall_injection`：持续运行时召回注入，在对话中出现新错误、新文件、新阶段或 topic drift 时通过 `progressive-recall` 按需查找。

为保持兼容，runtime 仍保留旧的 `system_prompt`、`runtime_context`、`reference_summary` channel 字段；它们会映射到上面的三层注入语义。

每次 `auto-start` / `activate` 也会把注入计划物化成宿主友好的运行时产物：

- `injections/<activation_id>.md`
- `injections/<activation_id>.json`
- `injections/latest.md`
- `injections/latest.json`

这些文件位于 `$EXPCAP_HOME` 项目记忆根目录下（只有 local profile 才会在 `.agent-memory/`）。
Claude hooks 会把渲染后的 Markdown 作为 `additionalContext`；其他宿主可以直接读取
`latest.md` 或 `latest.json`。

资产带有作用域和生命周期字段：

- `knowledge_scope`：`project` 或 `cross-project`。
- `knowledge_kind`：`pattern`、`anti_pattern`、`rule`、`context`、`checklist`、
  `past_win`、`preference`、`constraint`、`decision_memory`、`dont_repeat` 或 `codemap`。
- `temperature`：`hot`、`warm`、`neutral`、`cool`。
- `review_status`：`healthy`、`watch`、`needs_review`、`unproven`。

## 存储

经验资产始终有项目归属，即使数据源是共享的。项目保留 identity 和 ownership
元数据，存储可以是项目本地、用户级缓存或远端共享后端。

Storage profile：

- `local`：运行数据写入项目 `.agent-memory/`。
- `user-cache`：运行数据写入 `EXPCAP_HOME`，不落在项目目录。
- `shared`：正文真源、状态索引和召回都预期使用共享后端。
- `hybrid`：共享正文/召回，加本地缓存和 SQLite 状态索引。

Agent 工作流推荐默认：

```bash
export EXPCAP_STORAGE_PROFILE=user-cache
export EXPCAP_HOME="$HOME/.expcap"
```

这样运行数据不会落在项目目录，同时仍保留 project-owned asset identity。

知识保存也按四层理解：

- `milvus`：语义召回索引层，负责找得到相似经验。
- `sqlite`：轻量状态索引层，负责候选状态、反馈、review queue、activation log 与统计。
- `markdown_files`：人类可读知识层，包括项目提示词、注入快照和文档资产。
- `logs`：原始运行证据层，包括 trace、episode、hook event 和 activation view。

显式 local profile：

- JSON 文件是正文真源。
- Milvus Lite 是本地核心语义召回层。
- SQLite 存储轻量状态、审核结果、activation log 和降级用 metadata index。

如果需要强制写回项目目录：

```bash
export EXPCAP_STORAGE_PROFILE=local
```

共享模式使用同一套 asset contract：

```bash
export EXPCAP_STORAGE_PROFILE=shared
export EXPCAP_SOURCE_OF_TRUTH_BACKEND=object-storage
export EXPCAP_STATE_INDEX_BACKEND=cloud-sql
export EXPCAP_RETRIEVAL_BACKEND=milvus
export EXPCAP_SHARING_BACKEND=cloud-shared
export EXPCAP_PROJECT_ID=github:org/repo
export EXPCAP_OWNING_TEAM=agent-platform
export EXPCAP_ASSET_STORE_URI=s3://bucket/expcap/assets
export EXPCAP_STATE_INDEX_URI=postgres://expcap
export EXPCAP_RETRIEVAL_INDEX_URI=https://milvus.example.com
export EXPCAP_SHARED_ASSET_STORE_URI=s3://bucket/expcap/shared
```

托管 Milvus 现在已经可以接入。设置 `EXPCAP_RETRIEVAL_BACKEND=milvus` 和
`EXPCAP_RETRIEVAL_INDEX_URI` 后，expcap 会使用远端 Milvus endpoint，而不是本地
Milvus Lite DB：

```bash
export EXPCAP_RETRIEVAL_BACKEND=milvus
export EXPCAP_RETRIEVAL_INDEX_URI=https://milvus.example.com
export EXPCAP_RETRIEVAL_INDEX_TOKEN="..."
export EXPCAP_MILVUS_DB_NAME=expcap
export EXPCAP_MILVUS_COLLECTION=experience_assets
```

`EXPCAP_RETRIEVAL_INDEX_TOKEN`、`EXPCAP_MILVUS_DB_NAME` 和
`EXPCAP_MILVUS_COLLECTION` 都是可选项。对象存储和 cloud SQL 目前仍是后端契约字段，
等待 adapter 落地；这些 adapter 增加时不会改变产品模型。

## 状态指标

使用 `status` 做短周期评估：

```bash
expcap status --workspace "$PWD"
```

需要可执行诊断时使用 `doctor`：

```bash
expcap doctor --workspace "$PWD"
expcap benchmark-milvus --workspace "$PWD" --sample-size 5 --limit 3 --include-shared
```

想直接看本地后台时使用 `dashboard`：

```bash
expcap dashboard --workspace "$PWD"
```

它会生成只读的 `dashboard.html` 和 JSON 侧车文件，展示资产列表、activation
帮助反馈、Milvus 召回贡献、candidate review queue 和写入频率。推荐的
`user-cache` profile 会把文件写到 `$EXPCAP_HOME` 下，而不是项目目录。可以把命令输出里的
`saved_to` 路径复制到浏览器打开，也可以使用快速开始里的 macOS 一键打开命令。

`benchmark-milvus` 会先同步当前 embedding profile 对应的 Milvus index，再执行
查询，避免 profile 切换被误判成召回失败。如果当前环境 Milvus runtime 不可用，benchmark
会明确报告该状态，并可使用带标记的 state-index fallback 做期望条件诊断。

如果要检查 codemap / 文档召回，可以带上期望条件：

```bash
expcap benchmark-milvus \
  --workspace "$PWD" \
  --query "README ingest-docs codemap" \
  --expect-kind codemap
```

重点观察：

- `activation_feedback_summary`：经验是否帮忙、是否 pending、是否 stale missing。
- `feedback_cleanup`：过期未处理 activation 是否已被自动收敛为 `unclear`，避免指标长期失真。
- `candidate_review_queue`：是否有需要人工审核的候选。
- `asset_effectiveness_summary`：资产热度和健康状态。
- `retrieval_backends`：Milvus 核心召回是否可用，以及 SQLite 轻量索引是否健康。
- `milvus_benchmark`：抽样检查 Milvus 召回质量，包括 provider 元数据、top score 和历史选中资产命中率。
- `injection_policy_summary`：最近 activation 是否把内容路由到了 `system_prompt`、`runtime_context`
  或 `reference_summary`。
- `project_activity`：当前项目是 `active` 还是 `inactive`，用于报表和覆盖率口径。
- `backend_configuration`：当前是本地模式还是共享模式。

Milvus 是核心召回能力。如果 Milvus Lite 被锁住或不可用，runtime 应该降级到 JSON/SQLite 以保证工作不中断，但 `doctor` 必须清楚暴露该降级，因为召回质量会下降。`doctor` 也会报告 Milvus lock 元数据和安全恢复建议。

## 文档

- [核心原则](docs/core_principles.md)
- [架构](docs/experience_capitalization_architecture.md)
- [MVP 规格](docs/mvp_spec.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [治理](GOVERNANCE.md)

## 项目状态

当前项目处于 pre-1.0 阶段。重点是验证经验资产模型、本地 runtime、activation feedback 闭环和存储契约，再逐步扩大云端后端能力。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

## 许可证

Apache-2.0。详见 [LICENSE](LICENSE)。
