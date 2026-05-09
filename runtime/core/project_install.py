from __future__ import annotations

import json
from pathlib import Path
import stat

from runtime.core.project_policy import DEFAULT_PROJECT_STATUS, write_project_policy


EXPCAP_BLOCK_START = "<!-- EXPCAP START -->"
EXPCAP_BLOCK_END = "<!-- EXPCAP END -->"
EXPCAP_GITIGNORE_ENTRY = ".agent-memory/"
INTEGRATION_MODE_DOCS_ONLY = "docs-only"
INTEGRATION_MODE_CODEX_HOOKS = "codex-hooks"
INTEGRATION_MODE_CLAUDE_HOOKS = "claude-hooks"
SUPPORTED_INTEGRATION_MODES = (
    INTEGRATION_MODE_DOCS_ONLY,
    INTEGRATION_MODE_CODEX_HOOKS,
    INTEGRATION_MODE_CLAUDE_HOOKS,
)


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
- 只要这个项目里真的开了新 chat，默认仍然会执行 `auto-start`
- `active / inactive` 主要用于统计、审阅和覆盖率口径，不用于阻断新 chat 激活
- 如果项目暂停维护、只读或已归档，可以标成 `inactive`，这样日报和横向分析时更容易把它和活跃项目区分开
- 需要切换状态时，可重新执行：

```bash
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap install-project --workspace "{workspace_path}" --project-status active
EXPCAP_STORAGE_PROFILE=user-cache EXPCAP_HOME="$HOME/.expcap" expcap install-project --workspace "{workspace_path}" --project-status inactive
```

### 1. 任务开始前默认先做 get

在开始实质性分析、改代码、跑命令之前，优先执行：

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
- 只要进入新 chat，就优先执行集中存储模式的 `expcap auto-start`
- 任务收敛后优先执行集中存储模式的 `expcap auto-finish`
- 高置信经验再继续 `promote`

{EXPCAP_BLOCK_END}"""


def normalize_integration_mode(
    *,
    integration_mode: str | None = None,
    include_claude: bool = False,
) -> str:
    if integration_mode:
        if integration_mode not in SUPPORTED_INTEGRATION_MODES:
            raise ValueError(f"unsupported integration mode: {integration_mode}")
        return integration_mode
    if include_claude:
        return INTEGRATION_MODE_CLAUDE_HOOKS
    return INTEGRATION_MODE_DOCS_ONLY


def _claude_settings_payload(workspace: Path) -> dict[str, object]:
    project_dir = str(workspace.resolve())
    hooks_dir = "$CLAUDE_PROJECT_DIR/.claude/hooks"
    return {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{hooks_dir}/expcap_user_prompt_submit.sh",
                            "timeout": 20,
                        }
                    ]
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{hooks_dir}/expcap_stop.sh",
                            "timeout": 30,
                        }
                    ]
                }
            ],
        },
        "env": {
            "EXPCAP_STORAGE_PROFILE": "user-cache",
            "EXPCAP_HOME": "$HOME/.expcap",
            "EXPCAP_PROJECT_DIR": project_dir,
        },
    }


def _codex_hooks_payload(workspace: Path) -> dict[str, object]:
    return {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume|clear",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'bash "$(git rev-parse --show-toplevel)/.codex/hooks/expcap_session_start.sh"',
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'bash "$(git rev-parse --show-toplevel)/.codex/hooks/expcap_user_prompt_submit.sh"',
                            "timeout": 20,
                        }
                    ]
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Bash|apply_patch",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'bash "$(git rev-parse --show-toplevel)/.codex/hooks/expcap_pre_tool_use.sh"',
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "PermissionRequest": [
                {
                    "matcher": "Bash|apply_patch",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'bash "$(git rev-parse --show-toplevel)/.codex/hooks/expcap_permission_request.sh"',
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash|apply_patch",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'bash "$(git rev-parse --show-toplevel)/.codex/hooks/expcap_post_tool_use.sh"',
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'bash "$(git rev-parse --show-toplevel)/.codex/hooks/expcap_stop.sh"',
                            "timeout": 30,
                        }
                    ]
                }
            ],
        }
    }


def _merge_hook_settings(existing: dict[str, object], update: dict[str, object]) -> dict[str, object]:
    merged = dict(existing)
    hooks = dict(existing.get("hooks") or {})
    update_hooks = update.get("hooks") or {}
    for event_name, entries in update_hooks.items():
        current_entries = hooks.get(event_name)
        if not isinstance(current_entries, list):
            hooks[event_name] = entries
            continue
        managed_names = _managed_codex_hook_names(entries)
        if managed_names:
            current_entries = [
                entry
                for entry in current_entries
                if not (_managed_codex_hook_names([entry]) & managed_names)
            ]
        normalized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in current_entries]
        for entry in entries:
            encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            if encoded not in normalized:
                current_entries.append(entry)
                normalized.append(encoded)
        hooks[event_name] = current_entries
    merged["hooks"] = hooks
    return merged


def _managed_codex_hook_names(entries: object) -> set[str]:
    names: set[str] = set()
    if not isinstance(entries, list):
        return names
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            command = str(hook.get("command") or "")
            marker = ".codex/hooks/expcap_"
            if marker not in command:
                continue
            tail = command.split(marker, 1)[1]
            names.add(tail.split('"', 1)[0].split("'", 1)[0].split()[0])
    return names


def _merge_claude_settings(existing: dict[str, object], update: dict[str, object]) -> dict[str, object]:
    merged = _merge_hook_settings(existing, update)

    env = dict(existing.get("env") or {})
    env.update(update.get("env") or {})
    merged["env"] = env
    return merged


def _write_executable_script(path: Path, content: str) -> tuple[bool, bool]:
    created = not path.exists()
    original = path.read_text(encoding="utf-8") if path.exists() else None
    if original != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        updated = True
    else:
        updated = False
    current_mode = path.stat().st_mode if path.exists() else 0
    executable_mode = current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    if path.exists() and executable_mode != current_mode:
        path.chmod(executable_mode)
    return created, updated


def _runtime_hook_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "expcap-hook"


def _ensure_claude_hook_files(workspace: Path) -> dict[str, str | bool]:
    claude_dir = workspace / ".claude"
    hooks_dir = claude_dir / "hooks"
    settings_path = claude_dir / "settings.json"

    prompt_hook_path = hooks_dir / "expcap_user_prompt_submit.sh"
    stop_hook_path = hooks_dir / "expcap_stop.sh"

    runtime_hook = str(_runtime_hook_path())
    prompt_hook = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${{CLAUDE_PROJECT_DIR:-$(pwd)}}"
HOOK_SCRIPT="$PROJECT_DIR/scripts/expcap-hook"
if [[ ! -f "$HOOK_SCRIPT" ]]; then
  HOOK_SCRIPT="{runtime_hook}"
fi

exec python3 "$HOOK_SCRIPT" user-prompt-submit --host claude --workspace "$PROJECT_DIR"
"""
    stop_hook = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${{CLAUDE_PROJECT_DIR:-$(pwd)}}"
HOOK_SCRIPT="$PROJECT_DIR/scripts/expcap-hook"
if [[ ! -f "$HOOK_SCRIPT" ]]; then
  HOOK_SCRIPT="{runtime_hook}"
fi

exec python3 "$HOOK_SCRIPT" stop --host claude --workspace "$PROJECT_DIR"
"""
    created_prompt_hook, updated_prompt_hook = _write_executable_script(prompt_hook_path, prompt_hook)
    created_stop_hook, updated_stop_hook = _write_executable_script(stop_hook_path, stop_hook)

    payload = _claude_settings_payload(workspace)
    existing = {}
    if settings_path.exists():
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    merged = _merge_claude_settings(existing, payload)
    created_settings = not settings_path.exists()
    original_settings = json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) if settings_path.exists() else None
    new_settings = json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    updated_settings = original_settings != new_settings if original_settings is not None else True
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(new_settings, encoding="utf-8")

    return {
        "claude_settings_path": str(settings_path),
        "claude_hooks_dir": str(hooks_dir),
        "created_claude_settings": created_settings,
        "updated_claude_settings": updated_settings,
        "created_claude_prompt_hook": created_prompt_hook,
        "updated_claude_prompt_hook": updated_prompt_hook,
        "created_claude_stop_hook": created_stop_hook,
        "updated_claude_stop_hook": updated_stop_hook,
    }


def _ensure_codex_hook_files(workspace: Path) -> dict[str, str | bool]:
    codex_dir = workspace / ".codex"
    hooks_dir = codex_dir / "hooks"
    hooks_path = codex_dir / "hooks.json"

    prompt_hook_path = hooks_dir / "expcap_user_prompt_submit.sh"
    stop_hook_path = hooks_dir / "expcap_stop.sh"
    session_start_hook_path = hooks_dir / "expcap_session_start.sh"
    pre_tool_use_hook_path = hooks_dir / "expcap_pre_tool_use.sh"
    permission_request_hook_path = hooks_dir / "expcap_permission_request.sh"
    post_tool_use_hook_path = hooks_dir / "expcap_post_tool_use.sh"

    runtime_hook = str(_runtime_hook_path())
    hook_preamble = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${{CODEX_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"
export EXPCAP_STORAGE_PROFILE="${{EXPCAP_STORAGE_PROFILE:-user-cache}}"
export EXPCAP_HOME="${{EXPCAP_HOME:-$HOME/.expcap}}"
HOOK_SCRIPT="$PROJECT_DIR/scripts/expcap-hook"
if [[ ! -f "$HOOK_SCRIPT" ]]; then
  HOOK_SCRIPT="{runtime_hook}"
fi

"""
    prompt_hook = hook_preamble + """\
exec python3 "$HOOK_SCRIPT" user-prompt-submit --host codex --workspace "$PROJECT_DIR"
"""
    stop_hook = hook_preamble + """\
exec python3 "$HOOK_SCRIPT" stop --host codex --workspace "$PROJECT_DIR"
"""
    session_start_hook = hook_preamble + """\
exec python3 "$HOOK_SCRIPT" session-start --host codex --workspace "$PROJECT_DIR"
"""
    pre_tool_use_hook = hook_preamble + """\
exec python3 "$HOOK_SCRIPT" pre-tool-use --host codex --workspace "$PROJECT_DIR"
"""
    permission_request_hook = hook_preamble + """\
exec python3 "$HOOK_SCRIPT" permission-request --host codex --workspace "$PROJECT_DIR"
"""
    post_tool_use_hook = hook_preamble + """\
exec python3 "$HOOK_SCRIPT" post-tool-use --host codex --workspace "$PROJECT_DIR"
"""
    created_prompt_hook, updated_prompt_hook = _write_executable_script(prompt_hook_path, prompt_hook)
    created_stop_hook, updated_stop_hook = _write_executable_script(stop_hook_path, stop_hook)
    created_session_start_hook, updated_session_start_hook = _write_executable_script(
        session_start_hook_path,
        session_start_hook,
    )
    created_pre_tool_use_hook, updated_pre_tool_use_hook = _write_executable_script(
        pre_tool_use_hook_path,
        pre_tool_use_hook,
    )
    created_permission_request_hook, updated_permission_request_hook = _write_executable_script(
        permission_request_hook_path,
        permission_request_hook,
    )
    created_post_tool_use_hook, updated_post_tool_use_hook = _write_executable_script(
        post_tool_use_hook_path,
        post_tool_use_hook,
    )

    payload = _codex_hooks_payload(workspace)
    existing = {}
    if hooks_path.exists():
        existing = json.loads(hooks_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    merged = _merge_hook_settings(existing, payload)
    created_hooks = not hooks_path.exists()
    original_hooks = json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) if hooks_path.exists() else None
    new_hooks = json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    updated_hooks = original_hooks != new_hooks if original_hooks is not None else True
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    hooks_path.write_text(new_hooks, encoding="utf-8")

    return {
        "codex_hooks_path": str(hooks_path),
        "codex_hooks_dir": str(hooks_dir),
        "created_codex_hooks": created_hooks,
        "updated_codex_hooks": updated_hooks,
        "created_codex_prompt_hook": created_prompt_hook,
        "updated_codex_prompt_hook": updated_prompt_hook,
        "created_codex_stop_hook": created_stop_hook,
        "updated_codex_stop_hook": updated_stop_hook,
        "created_codex_session_start_hook": created_session_start_hook,
        "updated_codex_session_start_hook": updated_session_start_hook,
        "created_codex_pre_tool_use_hook": created_pre_tool_use_hook,
        "updated_codex_pre_tool_use_hook": updated_pre_tool_use_hook,
        "created_codex_permission_request_hook": created_permission_request_hook,
        "updated_codex_permission_request_hook": updated_permission_request_hook,
        "created_codex_post_tool_use_hook": created_post_tool_use_hook,
        "updated_codex_post_tool_use_hook": updated_post_tool_use_hook,
    }


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
    integration_mode: str | None = None,
    include_claude: bool = False,
    project_status: str = DEFAULT_PROJECT_STATUS,
) -> dict[str, str | bool]:
    workspace = workspace.resolve()
    normalized_mode = normalize_integration_mode(
        integration_mode=integration_mode,
        include_claude=include_claude,
    )
    sidecar_path = workspace / "AGENTS.expcap.md"
    policy_path = write_project_policy(
        workspace,
        project_status=project_status,
        integration_mode=normalized_mode,
    )
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
    claude_hook_result: dict[str, str | bool] = {
        "claude_settings_path": "",
        "claude_hooks_dir": "",
        "created_claude_settings": False,
        "updated_claude_settings": False,
        "created_claude_prompt_hook": False,
        "updated_claude_prompt_hook": False,
        "created_claude_stop_hook": False,
        "updated_claude_stop_hook": False,
    }
    codex_hook_result: dict[str, str | bool] = {
        "codex_hooks_path": "",
        "codex_hooks_dir": "",
        "created_codex_hooks": False,
        "updated_codex_hooks": False,
        "created_codex_prompt_hook": False,
        "updated_codex_prompt_hook": False,
        "created_codex_stop_hook": False,
        "updated_codex_stop_hook": False,
        "created_codex_session_start_hook": False,
        "updated_codex_session_start_hook": False,
        "created_codex_pre_tool_use_hook": False,
        "updated_codex_pre_tool_use_hook": False,
        "created_codex_permission_request_hook": False,
        "updated_codex_permission_request_hook": False,
        "created_codex_post_tool_use_hook": False,
        "updated_codex_post_tool_use_hook": False,
    }
    if normalized_mode == INTEGRATION_MODE_CODEX_HOOKS:
        codex_hook_result = _ensure_codex_hook_files(workspace)
    if normalized_mode == INTEGRATION_MODE_CLAUDE_HOOKS:
        created_claude, updated_claude = _upsert_managed_block(
            claude_path,
            title="CLAUDE.md",
            intro="本项目启用了 `expcap` 经验资本化工作流，详细规则见 `AGENTS.expcap.md`。",
            sidecar_name="AGENTS.expcap.md",
        )
        claude_hook_result = _ensure_claude_hook_files(workspace)

    return {
        "workspace": str(workspace),
        "integration_mode": normalized_mode,
        "agents_path": str(agents_path),
        "sidecar_path": str(sidecar_path),
        "policy_path": str(policy_path),
        "project_status": project_status,
        "gitignore_path": str(gitignore_path),
        "claude_path": str(claude_path) if normalized_mode == INTEGRATION_MODE_CLAUDE_HOOKS else "",
        "created_agents": created_agents,
        "updated_agents": updated_agents,
        "created_gitignore": created_gitignore,
        "updated_gitignore": updated_gitignore,
        "created_claude": created_claude,
        "updated_claude": updated_claude,
        **codex_hook_result,
        **claude_hook_result,
    }
