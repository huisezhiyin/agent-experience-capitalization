# Agent Experience Capitalization

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-pre--1.0-orange.svg)](GOVERNANCE.md)

面向 coding agent 的项目级记忆层。

`expcap` 把有效的 agent 工作沉淀成可复用的工程资产。这些资产属于项目和团队，而不是某个用户、某台机器或某个模型账号。

语言：[English](README.md) | [简体中文](README.zh-CN.md)

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
- 支持本地 JSON/SQLite，以及可选 Milvus Lite 语义召回。
- 提供共享对象存储、云端状态索引和托管向量检索的后端契约。

## 安装

```bash
git clone <repo-url>
cd agent-experience-capitalization

python3 -m venv .venv
.venv/bin/pip install -e .
scripts/expcap --help
```

可选启用 Milvus Lite：

```bash
.venv/bin/pip install -e ".[milvus]"
scripts/expcap sync-milvus --workspace "$PWD" --include-shared
```

## 快速开始

下面的流程会创建 trace，review 成 episode，提取 candidate，晋升 asset，然后执行一次 activation：

```bash
scripts/expcap ingest \
  --workspace "$PWD" \
  --task "fix pytest import error" \
  --command "python3 -m unittest discover -s tests -v" \
  --error "ModuleNotFoundError: no module named foo" \
  --verification-status passed \
  --verification-summary "tests passed" \
  --result-status success \
  --result-summary "fixed import path" \
  --trace-id trace_demo_import_fix

scripts/expcap review --input .agent-memory/traces/bundles/trace_demo_import_fix.json
scripts/expcap extract --episode .agent-memory/episodes/ep_demo_import_fix.json
scripts/expcap promote --candidate .agent-memory/candidates/cand_demo_import_fix.json
scripts/expcap activate --task "fix pytest import error" --workspace "$PWD"
scripts/expcap status --workspace "$PWD"
```

运行数据会写入 `.agent-memory/`。这个目录应始终排除在源码提交之外。

## Agent 工作流

把 `expcap` 接入另一个项目：

```bash
scripts/expcap install-project --workspace /path/to/project
```

同时写入 `CLAUDE.md`：

```bash
scripts/expcap install-project --workspace /path/to/project --include-claude
```

安装器会非破坏式追加说明，创建 `AGENTS.expcap.md`，并确保 `.agent-memory/`
写入 `.gitignore`。之后 agent 可以使用：

```bash
expcap auto-start --task "your task" --workspace "$PWD"
expcap auto-finish --task "your task" --workspace "$PWD" --verification-status passed --result-status success
```

## 核心概念

- `trace`：原始任务证据。
- `episode`：经过 review 的任务叙事。
- `candidate`：从 episode 中提取出的可复用经验。
- `asset`：晋升后的项目或团队记忆资产。
- `activation`：未来任务中被选中的资产。
- `feedback`：activation 是否真的帮到了任务。

Activation view 会包含 `source_provenance`、`match_evidence`、`risk_flags`
和 `llm_use_guidance`。召回层负责提供带来源的候选，coding agent 仍需结合当前任务判断是否采用。

资产带有作用域和生命周期字段：

- `knowledge_scope`：`project` 或 `cross-project`。
- `knowledge_kind`：`pattern`、`anti_pattern`、`rule`、`context`、`checklist`。
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

默认本地 profile：

- JSON 文件是正文真源。
- SQLite 存储状态、索引、审核结果和 activation log。
- Milvus Lite 可作为可选语义召回层。

把本地运行数据移出项目目录：

```bash
export EXPCAP_STORAGE_PROFILE=user-cache
export EXPCAP_HOME="$HOME/.expcap"
export EXPCAP_PROJECT_ID=github:org/repo
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

当前实现重点是本地 runtime 和可迁移资产契约。云端后端应通过配置启用，而不是改变产品模型。

## 状态指标

使用 `status` 做短周期评估：

```bash
expcap status --workspace "$PWD"
```

需要可执行诊断时使用 `doctor`：

```bash
expcap doctor --workspace "$PWD"
```

重点观察：

- `activation_feedback_summary`：经验是否帮忙、是否 pending、是否 stale missing。
- `candidate_review_queue`：是否有需要人工审核的候选。
- `asset_effectiveness_summary`：资产热度和健康状态。
- `retrieval_backends`：SQLite 与 Milvus 是否可用。
- `backend_configuration`：当前是本地模式还是共享模式。

Milvus Lite 是可选层。如果它被锁住或不可用，runtime 应该降级到 JSON/SQLite，而不是阻塞工作流。`doctor` 也会报告 Milvus lock 元数据和安全恢复建议。

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
