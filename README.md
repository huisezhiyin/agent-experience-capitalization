# Agent Experience Capitalization

面向 `Codex`、`Claude Code` 等 coding agent 的本地优先经验资本化系统。

`expcap` 的入口是 `skill`，后端是本地 runtime。它让 agent 在任务开始前自动激活历史经验，在任务结束后自动沉淀经验，并用 `SQLite` / `Milvus Lite` 等后端维护可复用经验。

## Quickstart

克隆仓库后可以直接用仓库内 wrapper 试跑：

```bash
git clone https://github.com/<owner>/agent-experience-capitalization.git
cd agent-experience-capitalization

python3 -m venv .venv
.venv/bin/pip install -e .
scripts/expcap --help
```

也可以安装成命令行入口：

```bash
.venv/bin/expcap --help
```

如果希望启用 Milvus Lite 语义召回：

```bash
.venv/bin/pip install -e ".[milvus]"
scripts/expcap sync-milvus --workspace "$PWD" --include-shared
```

## Demo Flow

下面这组命令会在当前仓库生成一条本地 trace，再流转成 episode、candidate、asset，并做一次 activation：

```bash
scripts/expcap ingest --workspace "$PWD" --task "fix pytest import error" --command "python3 -m pytest tests/test_imports.py" --error "ModuleNotFoundError: no module named foo" --verification-status passed --verification-summary "1 passed" --result-status success --result-summary "修复导入路径并补充回归测试" --trace-id trace_demo_import_fix
scripts/expcap review --input .agent-memory/traces/bundles/trace_demo_import_fix.json
scripts/expcap extract --episode .agent-memory/episodes/ep_demo_import_fix.json
scripts/expcap promote --candidate .agent-memory/candidates/cand_demo_import_fix.json
scripts/expcap activate --task "fix pytest import error" --workspace "$PWD"
scripts/expcap status --workspace "$PWD"
```

运行态会写入 `.agent-memory/`，该目录默认被 `.gitignore` 排除。

## Skill Usage

在目标项目中接入默认 `get/save` 行为：

```bash
scripts/expcap install-project --workspace /path/to/your/project
```

如果也希望给 Claude Code 写入同样的启用入口：

```bash
scripts/expcap install-project --workspace /path/to/your/project --include-claude
```

也可以直接使用一键启用脚本，它会安装本地 CLI 包、写入 `AGENTS.md` / `CLAUDE.md`，最后输出一次项目状态：

```bash
scripts/expcap-enable /path/to/your/project
```

如果希望顺带安装并同步 Milvus Lite：

```bash
EXPCAP_WITH_MILVUS=1 scripts/expcap-enable /path/to/your/project
```

接入后，项目会获得 `AGENTS.expcap.md`，agent 可在任务开始前默认执行 `auto-start`，任务收敛后默认执行 `auto-finish`。

## Experience Dimensions

`expcap` 不是只存一段文本，而是给经验带上多层维度，方便后续检索、审核和降权：

- `knowledge_scope`：经验层级，目前支持 `project` 与 `cross-project`。项目经验默认只服务当前 workspace，跨项目经验会进入共享层。
- `knowledge_kind`：知识类型，目前支持 `pattern`、`anti_pattern`、`rule`、`context`、`checklist`。
- `scope.level / scope.value`：任务作用域，例如 `task-family::python-import-error` 或 `workspace::general-coding-task`，用于避免宽泛经验误召回。
- `workspace / source_workspace`：经验来自哪个项目。当前版本主要按显式 `--workspace` 的规范化路径识别项目。
- `temperature / review_status`：经验热度与健康状态，例如 `hot`、`warm`、`watch`、`needs_review`，用于观察真实帮助效果。

项目识别当前是“显式 workspace 优先”：agent 或脚本把目标项目路径传给 `--workspace`，运行态把经验写到该项目的 `.agent-memory/`，并优先激活这个项目自己的资产。后续可以在这个基础上增加模糊项目识别，例如结合 git remote、仓库名、包名、目录结构和 `.agent-memory` 指纹来判断“这是哪个项目的延续”。

当前阶段以研究与架构设计为主，核心方向是：

- 轻 `skill`，作为默认入口
- 重 `local runtime`，作为真正后端
- 本地文件 + `SQLite` 状态层 + `Milvus Lite` 可选语义召回层
- 将任务经验从 `trace -> episode -> candidate -> asset -> activation` 制度化流转

## 北极星目标

这个项目的核心目标不是“多存一些经验”，而是同时做到两件事：

- `自动化`：让 agent 在任务开始时默认自动 `get` 相关经验，在任务收敛后默认自动 `save` 有价值经验，尽量不依赖用户手工触发
- `真的有帮助`：让被激活的经验能够提高任务执行质量，而不是把不准确或不适用的内容塞进上下文

换句话说，这个项目真正要验证的是：

> agent 是否能把过去任务中的有效经验，自动转成未来任务中的正确助力。

如果只能存，不能自动触发，或者触发了但对当前任务没有强化作用，那都不算达成目标。

## 当前文档

- [Concept](docs/my_idea.md)
- [Architecture](docs/experience_capitalization_architecture.md)
- [MVP Spec](docs/mvp_spec.md)

## 当前代码骨架

第一版最小 runtime 已起步，当前目录包含：

- `runtime/cli/`
- `runtime/core/`
- `runtime/storage/`
- `schemas/`
- `examples/`

当前命令面为：

```bash
expcap auto-start --task "fix pytest import error" --workspace "$PWD" --constraint "不要改 public API"
expcap auto-finish --workspace "$PWD" --task "fix pytest import error" --command "uv run pytest tests/test_imports.py" --error "ModuleNotFoundError: no module named foo" --verification-status passed --verification-summary "1 passed" --result-status success --result-summary "修复导入路径并补充回归测试"
expcap review-candidates --workspace "$PWD"
expcap review-candidates --workspace "$PWD" --action approve --candidate-id cand_xxx
expcap status --workspace "$PWD"
expcap sync-milvus --workspace "$PWD" --include-shared
expcap sync-milvus --workspace "$PWD" --include-shared --prune
python3 -m runtime.cli ingest --workspace "$PWD" --task "fix pytest import error" --command "uv run pytest tests/test_imports.py" --error "ModuleNotFoundError: no module named foo" --verification-status passed --verification-summary "1 passed" --result-status success --result-summary "修复导入路径并补充回归测试"
python3 -m runtime.cli auto-finish --workspace "$PWD" --task "stabilize API contract checks" --constraint "不要破坏现有 API 契约" --verification-status passed --result-status success --knowledge-scope cross-project --knowledge-kind rule
python3 -m runtime.cli install-project --workspace /path/to/another-repo
python3 -m runtime.cli review --input examples/sample_trace_bundle.json
python3 -m runtime.cli extract --episode .agent-memory/episodes/ep_20260413_001.json
python3 -m runtime.cli promote --candidate .agent-memory/candidates/cand_20260413_001.json
python3 -m runtime.cli activate --task "fix pytest import error" --workspace "$PWD"
python3 -m runtime.cli explain --input .agent-memory/views/act_fix-pytest-import-error.json
```

最小回归测试：

```bash
python3 -m unittest discover -s tests -v
```

说明：

- 当前实现是零依赖 Python 骨架，目标是先打通 `ingest -> review -> extract -> activate -> explain`
- 输出默认落在工作区下的 `.agent-memory/`
- 跨项目共享资产默认落在 `$CODEX_HOME/expcap-memory/`
- `ingest` 可直接把任务事实落成 `.agent-memory/traces/bundles/*.json`
- `auto-start` 是默认的任务开始入口，会执行激活并写入 `activation_view`
- `auto-finish` 是默认的任务结束入口，会串起 `ingest -> review -> extract`，并在满足阈值时自动 `promote`
- `promote/auto-finish` 现在支持 `project` 与 `cross-project` 两层资产，以及 `pattern / anti_pattern / rule / context / checklist` 等知识类型
- `review-candidates` 现在既能生成待审队列，也能直接执行 `approve / reject / promote` 审核动作，并留下 `review_history`
- `status` 会输出当前工作区的短测摘要，包括使用量、activation 帮助反馈、asset 温度、candidate 状态与 review queue
- `status` 现在也会输出 retrieval backend 摘要。默认只做轻量检查，不启动 Milvus Lite；如需检查 collection/entity 数，可加 `--deep-retrieval-check`
- `status` 现在也会输出 `backend_configuration`，明确展示当前运行是在 `local-first` 还是 `hybrid` 配置下
- 优先调用安装后的 `expcap`；如果当前环境没有安装 CLI，可退回 `python3 -m runtime.cli`
- `install-project` 会非破坏式接入其他项目：保留原有 `AGENTS.md`，只追加 `expcap` 区块并生成 `AGENTS.expcap.md`
- `.agent-memory/index.sqlite3` 负责本地状态索引、审核结果、activation log 与统计汇总，正文内容仍以 JSON 文件为真源
- Milvus Lite 已作为可选语义召回层接入：本项目资产走 `<workspace>/.agent-memory/milvus.db`，跨项目资产走 `$CODEX_HOME/expcap-memory/milvus.db`
- 目前属于 MVP 早期版本，优先验证对象模型和数据流，不追求复杂策略

## 下一轮短测

如果要做一轮“小步快跑”的短期测试，建议直接按下面顺序执行：

```bash
expcap auto-start --task "your real task" --workspace "$PWD"
# 正常完成一轮真实任务
expcap auto-finish --task "your real task" --workspace "$PWD" --verification-status passed --result-status success --result-summary "本轮结果摘要"
expcap review-candidates --workspace "$PWD"
expcap status --workspace "$PWD"
```

短测时最值得重点看四个结果：

- `activation_feedback_summary`：最近激活到底有没有真正帮到任务
- `candidate_status_summary`：candidate 是继续堆积，还是在进入 `needs_review / approved / promoted`
- `candidate_review_queue.top_items`：当前最值得人工审核的候选是谁
- `asset_effectiveness_summary`：现有资产是在升温，还是开始进入 `watch / needs_review`

如果这轮测试要顺带观察检索层，也建议额外看：

- `retrieval_backends.sqlite`：SQLite 状态索引是否正常生成
- `retrieval_backends.milvus.local`：当前工作区的 Milvus Lite 配置是否可用、db 是否存在、是否被锁住
- `retrieval_backends.milvus.shared`：跨项目共享语义索引配置是否可用、db 是否存在、是否被锁住
- `retrieval_backends.milvus.asset_coverage`：深度检查时展示 Milvus indexed entity 与当前 asset 数的覆盖关系，并提示可能的 stale entity
- `backend_configuration`：当前 source of truth / state index / retrieval / sharing 分别请求的是哪一类 backend

默认 `status` 不会打开 Milvus Lite client，避免日报在受限环境中被本地 socket 初始化拖住。需要深度检查时可以运行：

```bash
expcap status --workspace "$PWD" --deep-retrieval-check
```

每次 `activate` 也会输出 `retrieval_summary`，并在每条 selected asset 上标注 `retrieval_sources` 与 `vector_score`。如果 `retrieval_sources` 包含 `milvus`，说明该经验确实经过 Milvus 语义召回参与排序。

如果 `asset_coverage.possible_stale_entities` 大于 0，可以运行 `sync-milvus --prune` 清理 Milvus 中已经没有对应 JSON asset 的旧 entity。

## Backend 配置

当前默认策略是 `local-first`：

- `source_of_truth=local-json`
- `state_index=sqlite`
- `retrieval=milvus-lite`
- `sharing=local-shared`

如果后面要切到混合模式，可以通过环境变量显式声明目标 backend：

```bash
export EXPCAP_SOURCE_OF_TRUTH_BACKEND=object-storage
export EXPCAP_STATE_INDEX_BACKEND=cloud-sql
export EXPCAP_RETRIEVAL_BACKEND=milvus
export EXPCAP_SHARING_BACKEND=cloud-shared
```

当前这一步先提供配置抽象与状态展示，不要求云端实现已经全部接通。

## 当前检索策略

`activate` 按轻量 experience RAG pipeline 执行：`retrieve -> rerank -> assemble`。

- 先检索当前项目资产，再补跨项目资产
- 如果 Milvus Lite 可用，先做语义召回候选，再与 SQLite / JSON 元数据混排
- 最终只注入少量高价值上下文，避免把“相似但不适用”的内容塞进当前任务

## 当前评估口径

这个项目当前最重要的评估维度，不是“接入了多少仓库”，而是下面这些结果指标：

- `自动 get 覆盖率`：任务开始时，系统是否真的自动做了经验激活
- `自动 save 覆盖率`：任务收敛后，系统是否真的自动沉淀了经验
- `有效命中率`：被激活的经验里，有多少条和当前任务强相关
- `帮助率`：被激活经验是否实际改变了执行策略、减少重复排查、避免错误路径
- `误召回率`：是否把无关、低置信、局部 workaround 注入了上下文
- `资产化率`：candidate 中有多少最终变成稳定 asset，而不是永远停留在临时候选层
- `跨项目强化率`：不同项目之间是否真的出现了可复用的高质量经验，而不只是项目内自循环

现阶段的主要判断标准应该是：

- 能不能默认自动运行
- 命中的东西对不对
- 命中之后有没有让 agent 变强

## 可选安装

如需启用 Milvus Lite 语义召回，可在项目本地虚拟环境中安装：

```bash
python3 -m venv .venv
.venv/bin/pip install "setuptools<81" "pymilvus[milvus-lite]"
```

## 当前定位

该项目不是新的完整 agent 平台，而是一个可挂载到现有 agent 之上的经验后端。

它的目标是：

- 采集本地任务证据
- 提炼任务 episode
- 维护 candidate 与长期资产
- 在新任务开始时按需激活高价值经验

## 当前结论

从技术路线看，当前更推荐：

- `skill` 作为 agent-facing API 与默认入口
- `local runtime` 作为真正后端
- `SQLite` 作为默认状态索引层
- `Milvus Lite` 作为推荐的语义增强层
- 后续按 `CLI runtime -> stateful runtime -> optional local service` 演化
