from __future__ import annotations

from pathlib import Path

from runtime.core.project_policy import DEFAULT_PROJECT_STATUS, write_project_policy


EXPCAP_BLOCK_START = "<!-- EXPCAP START -->"
EXPCAP_BLOCK_END = "<!-- EXPCAP END -->"
EXPCAP_GITIGNORE_ENTRY = ".agent-memory/"


def _sidecar_content(workspace: Path, *, project_status: str) -> str:
    workspace_path = str(workspace.resolve())
    return f"""# AGENTS.expcap.md

本文件由 `expcap install-project` 生成，用于把经验资本化工作流非破坏式接入当前项目。

## 目标

- 不替换原有 `AGENTS.md`
- 只为当前项目补充经验 `get/save` 规则
- 让 Codex 在这个项目中默认通过 `expcap` skill 执行经验 `get/save`
- 默认把运行数据写入 `EXPCAP_HOME` 集中数据中心，而不是项目目录

## 核心定位

`expcap` 不和 Codex / Claude Code 的个人记忆竞争。它专注于项目级、团队级、公司级的工程经验资产：可共享、可审阅、可交割，不绑定某一个人的模型账号。

## 默认行为

### 0. 项目活跃状态

- 当前项目状态：`{project_status}`
- 只有 `active` 项目默认自动执行 `auto-start`
- 如果项目暂停维护、只读或已归档，改成 `inactive` 后就不再默认自动启动
- 需要切换状态时，可重新执行：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap install-project --workspace "{workspace_path}" --project-status active
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap install-project --workspace "{workspace_path}" --project-status inactive
```

### 1. 任务开始前默认先做 get

当项目状态为 `active` 时，在开始实质性分析、改代码、跑命令之前，优先执行：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap auto-start --task "<当前任务摘要>" --workspace "{workspace_path}"
```

如果命中经验，优先把命中结果作为当前执行策略的一部分。

### 2. 任务收敛后默认尝试做 save

当任务完成一轮收敛，或形成了稳定 lesson / pattern / anti-pattern 后，优先执行：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap auto-finish --workspace "{workspace_path}" --task "<当前任务摘要>" ...
```

如果经验高置信且明显可复用，再继续：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap promote --candidate "<auto-finish 输出的 candidate path>"
```

### 3. 作用域策略

- 默认把当前项目内沉淀的经验视为 project-owned 经验
- 只有经过多个项目验证的稳定经验，才考虑后续晋升为 team-shared 经验
- 不要把项目局部 workaround 误提升为团队共享经验
- 默认先激活 `project` 资产，再补充 `cross-project` 资产
- 项目规范、历史决策、目录约定等，也可以作为 `context / rule` 类型知识沉淀

### 4. 什么时候不要自动 save

- 任务尚未收敛
- 只是临时 workaround
- 缺少验证结果
- 用户明确要求不要记录

## 说明

- `expcap` 是全局 skill + 本地 runtime 能力
- skill 是推荐入口，CLI 是执行层
- 当前项目经验默认落在 `EXPCAP_HOME/projects/...`
- `.agent-memory/` 仅作为显式 `EXPCAP_STORAGE_PROFILE=local` 的兼容目录
- 正文真源是 JSON 文件，Milvus 是核心语义召回层，SQLite 是轻量状态/日志索引
"""


def _managed_block(sidecar_name: str = "AGENTS.expcap.md") -> str:
    return f"""{EXPCAP_BLOCK_START}
## Expcap Integration

- 本项目额外启用经验资本化工作流，详细规则见 `{sidecar_name}`
- 不替换本项目原有 agent 约束，只补充经验 `get/save` 行为
- 仅 active 项目在任务开始前优先执行集中存储模式的 `expcap auto-start`
- 任务收敛后优先执行集中存储模式的 `expcap auto-finish`
- 高置信经验再继续 `promote`

{EXPCAP_BLOCK_END}"""


def _upsert_managed_block(
    path: Path,
    *,
    title: str,
    intro: str,
    sidecar_name: str,
) -> tuple[bool, bool]:
    block = _managed_block(sidecar_name=sidecar_name)
    created = False
    updated = False

    if not path.exists():
        path.write_text(f"# {title}\n\n{intro}\n\n{block}\n", encoding="utf-8")
        return True, True

    original = path.read_text(encoding="utf-8")
    if EXPCAP_BLOCK_START in original and EXPCAP_BLOCK_END in original:
        start = original.index(EXPCAP_BLOCK_START)
        end = original.index(EXPCAP_BLOCK_END) + len(EXPCAP_BLOCK_END)
        new_content = original[:start].rstrip() + "\n\n" + block + "\n" + original[end:].lstrip()
    else:
        suffix = "" if original.endswith("\n") else "\n"
        new_content = original + suffix + "\n" + block + "\n"
    if new_content != original:
        path.write_text(new_content, encoding="utf-8")
        updated = True
    return created, updated


def _ensure_gitignore_entry(workspace: Path) -> tuple[Path, bool, bool]:
    gitignore_path = workspace / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(
            "# expcap local runtime data\n.agent-memory/\n",
            encoding="utf-8",
        )
        return gitignore_path, True, True

    original = gitignore_path.read_text(encoding="utf-8")
    entries = {line.strip() for line in original.splitlines()}
    if EXPCAP_GITIGNORE_ENTRY in entries or ".agent-memory" in entries:
        return gitignore_path, False, False

    suffix = "" if original.endswith("\n") else "\n"
    new_content = original + suffix + "\n# expcap local runtime data\n.agent-memory/\n"
    gitignore_path.write_text(new_content, encoding="utf-8")
    return gitignore_path, False, True


def install_project_agents(
    workspace: Path,
    *,
    include_claude: bool = False,
    project_status: str = DEFAULT_PROJECT_STATUS,
) -> dict[str, str | bool]:
    workspace = workspace.resolve()
    sidecar_path = workspace / "AGENTS.expcap.md"
    policy_path = write_project_policy(workspace, project_status=project_status)
    sidecar_path.write_text(_sidecar_content(workspace, project_status=project_status), encoding="utf-8")
    gitignore_path, created_gitignore, updated_gitignore = _ensure_gitignore_entry(workspace)

    agents_path = workspace / "AGENTS.md"
    created_agents, updated_agents = _upsert_managed_block(
        agents_path,
        title="AGENTS.md",
        intro="本项目启用了 `expcap` 经验资本化工作流，详细规则见 `AGENTS.expcap.md`。",
        sidecar_name="AGENTS.expcap.md",
    )

    claude_path = workspace / "CLAUDE.md"
    created_claude = False
    updated_claude = False
    if include_claude:
        created_claude, updated_claude = _upsert_managed_block(
            claude_path,
            title="CLAUDE.md",
            intro="本项目启用了 `expcap` 经验资本化工作流，详细规则见 `AGENTS.expcap.md`。",
            sidecar_name="AGENTS.expcap.md",
        )

    return {
        "workspace": str(workspace),
        "agents_path": str(agents_path),
        "sidecar_path": str(sidecar_path),
        "policy_path": str(policy_path),
        "project_status": project_status,
        "gitignore_path": str(gitignore_path),
        "claude_path": str(claude_path) if include_claude else "",
        "created_agents": created_agents,
        "updated_agents": updated_agents,
        "created_gitignore": created_gitignore,
        "updated_gitignore": updated_gitignore,
        "created_claude": created_claude,
        "updated_claude": updated_claude,
    }
